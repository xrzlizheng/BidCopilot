from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.config import get_settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.knowledge.image_sanitize import webp_supported
from app.modules.knowledge.kb import MAX_IMAGE_BYTES
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument, KnowledgeImage

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
WEBP_RGB = bytes.fromhex(
    "524946461e000000574542505650384c110000002f0600010007508f22d7a3ff8188e87f0000"
)


def _product_docx() -> bytes:
    document = Document()
    document.add_heading("星云运维平台产品说明", level=1)
    document.add_paragraph("星云运维平台支持双活架构，提供 7x24 小时监控与自动化巡检。")
    document.add_paragraph("平台适用于政务云、医疗云与教育云场景，支持国产化适配。")
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def _seed_tenant(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        role = (
            await session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.code == "tenant_admin")
            )
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                tenant_id=None,
                code="tenant_admin",
                name="企业管理员",
                permissions=list(SYSTEM_ROLES["tenant_admin"]["permissions"]),
                is_system=True,
            )
            session.add(role)
            await session.flush()
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name=label,
        )
        session.add(user)
        await session.flush()
        session.add(UserRole(tenant_id=tenant.id, user_id=user.id, role_id=role.id, scope_type="tenant", scope_id=tenant.id))
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
async def test_knowledge_base_ingest_search_and_isolation() -> None:
    tenant_id, email = await _seed_tenant("kb-a")
    other_tenant_id, other_email = await _seed_tenant("kb-b")
    document_id: str | None = None
    image_id: str | None = None
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            upload = await client.post(
                "/api/v1/knowledge/documents",
                files={
                    "file": (
                        "product.docx",
                        _product_docx(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                data={"title": "星云运维平台产品说明", "kind": "product", "tags": "运维,产品"},
                headers=headers,
            )
            assert upload.status_code == 200, upload.text
            document_id = upload.json()["id"]
            assert upload.json()["status"] == "parsed"
            assert upload.json()["text_chars"] > 0
            assert upload.json()["tags"] == ["运维", "产品"]

            documents = await client.get("/api/v1/knowledge/documents", headers=headers)
            assert documents.status_code == 200
            assert any(item["id"] == document_id for item in documents.json())

            detail = await client.get(
                f"/api/v1/knowledge/documents/{document_id}", headers=headers
            )
            assert detail.status_code == 200, detail.text
            assert detail.json()["chunks"]
            assert "星云" in detail.json()["text_preview"]

            before_patch = await client.get(
                "/api/v1/knowledge/search",
                params={"q": "运维 云原生"},
                headers=headers,
            )
            before_score = next(
                item["score"]
                for item in before_patch.json()["chunks"]
                if item["document_id"] == document_id
            )
            patched = await client.patch(
                f"/api/v1/knowledge/documents/{document_id}",
                json={"title": "星云运维平台（修订）", "kind": "solution", "tags": ["云原生"]},
                headers=headers,
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["title"] == "星云运维平台（修订）"
            assert patched.json()["kind"] == "solution"
            assert patched.json()["tags"] == ["云原生"]
            after_patch = await client.get(
                "/api/v1/knowledge/search",
                params={"q": "运维 云原生"},
                headers=headers,
            )
            after_score = next(
                item["score"]
                for item in after_patch.json()["chunks"]
                if item["document_id"] == document_id
            )
            assert after_score > before_score
            renamed = await client.get("/api/v1/knowledge/documents", headers=headers)
            assert any(
                item["id"] == document_id and item["title"] == "星云运维平台（修订）"
                for item in renamed.json()
            )

            image = await client.post(
                "/api/v1/knowledge/images",
                files={"file": ("architecture.png", PNG, "image/png")},
                data={"title": "星云平台双活架构图", "caption": "双活架构", "tags": "架构图,双活"},
                headers=headers,
            )
            assert image.status_code == 200, image.text
            image_id = image.json()["id"]
            assert image.json()["width"] == 1 and image.json()["height"] == 1

            webp = await client.post(
                "/api/v1/knowledge/images",
                files={"file": ("photo.webp", WEBP_RGB, "image/webp")},
                data={"title": "WebP 图片"},
                headers=headers,
            )
            if webp_supported():
                assert webp.status_code == 200, webp.text
                stored = webp.json()
                assert stored["width"] == 7 and stored["height"] == 5
                assert stored["filename"].endswith(".png")
                assert stored["content_type"] == "image/png"
            else:
                assert webp.status_code == 503

            mislabeled = await client.post(
                "/api/v1/knowledge/images",
                files={"file": ("notreally.webp", PNG, "image/webp")},
                data={"title": "伪装 WebP"},
                headers=headers,
            )
            assert mislabeled.status_code == 422

            oversized = await client.post(
                "/api/v1/knowledge/images",
                files={
                    "file": (
                        "huge.png",
                        b"\x89PNG\r\n\x1a\n" + b"0" * MAX_IMAGE_BYTES,
                        "image/png",
                    )
                },
                data={"title": "超大图片"},
                headers=headers,
            )
            assert oversized.status_code == 422
            assert "exceeds 8MB" in oversized.text

            search = await client.get(
                "/api/v1/knowledge/search", params={"q": "运维平台 双活架构"}, headers=headers
            )
            assert search.status_code == 200, search.text
            body = search.json()
            assert body["chunks"], body
            assert "星云" in body["chunks"][0]["chunk"]
            assert any(item["id"] == image_id for item in body["images"])

            other_headers = await _login(client, other_email)
            image_content = await client.get(
                f"/api/v1/knowledge/images/{image_id}/content", headers=headers
            )
            assert image_content.status_code == 200
            assert image_content.content == PNG
            assert image_content.headers["content-type"].startswith("image/png")
            forbidden_image = await client.get(
                f"/api/v1/knowledge/images/{image_id}/content", headers=other_headers
            )
            assert forbidden_image.status_code == 404
            forbidden_update = await client.patch(
                f"/api/v1/knowledge/images/{image_id}",
                json={"sensitive": True},
                headers=other_headers,
            )
            assert forbidden_update.status_code == 404
            forbidden_document_update = await client.patch(
                f"/api/v1/knowledge/documents/{document_id}",
                json={"tags": ["劫持"]},
                headers=other_headers,
            )
            assert forbidden_document_update.status_code == 404
            updated_image = await client.patch(
                f"/api/v1/knowledge/images/{image_id}",
                json={"sensitive": True, "caption": "敏感架构图"},
                headers=headers,
            )
            assert updated_image.status_code == 200
            assert updated_image.json()["sensitive"] is True
            assert updated_image.json()["caption"] == "敏感架构图"

            other_search = await client.get(
                "/api/v1/knowledge/search", params={"q": "运维平台"}, headers=other_headers
            )
            assert other_search.json()["chunks"] == []
            other_documents = await client.get("/api/v1/knowledge/documents", headers=other_headers)
            assert other_documents.json() == []
            other_detail = await client.get(
                f"/api/v1/knowledge/documents/{document_id}", headers=other_headers
            )
            assert other_detail.status_code == 404
            cross_delete = await client.delete(
                f"/api/v1/knowledge/documents/{document_id}", headers=other_headers
            )
            assert cross_delete.status_code == 404

            async with SessionLocal() as session:
                document_row = await session.get(KnowledgeDocument, UUID(document_id))
                document_ref = document_row.object_ref if document_row else ""
                image_row = await session.get(KnowledgeImage, UUID(image_id))
                image_ref = image_row.object_ref if image_row else ""
            snapshot_root = Path(get_settings().SNAPSHOT_LOCAL_DIR)
            assert document_ref and (snapshot_root / document_ref).exists()
            assert image_ref and (snapshot_root / image_ref).exists()

            deleted = await client.delete(
                f"/api/v1/knowledge/documents/{document_id}", headers=headers
            )
            assert deleted.status_code == 204
            assert not (snapshot_root / document_ref).exists()
            async with SessionLocal() as session:
                remaining = (
                    await session.execute(
                        select(KnowledgeChunk).where(KnowledgeChunk.document_id == UUID(document_id))
                    )
                ).scalars().all()
                assert remaining == []

            deleted_image = await client.delete(
                f"/api/v1/knowledge/images/{image_id}", headers=headers
            )
            assert deleted_image.status_code == 204
            assert not (snapshot_root / image_ref).exists()
    finally:
        await _cleanup([tenant_id, other_tenant_id])
