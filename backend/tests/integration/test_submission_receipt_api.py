import hashlib
import shutil
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.projects.receipts import ATTACHMENT_MAX_BYTES

# 真实文件头：PNG / JPEG / PDF / WebP 各一份，用于验证魔数白名单。
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)
JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000") + b"\x00" * 64 + b"\xff\xd9"
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
# 不在白名单内的真实文件头（GIF89a），用来断言类型不符/不支持都返回 400。
GIF = b"GIF89a" + b"\x00" * 32


async def _seed_admin(label: str) -> tuple[UUID, str]:
    async with SessionLocal() as session:
        roles: dict[str, Role] = {}
        for code, spec in SYSTEM_ROLES.items():
            role = (
                await session.execute(
                    select(Role).where(Role.tenant_id.is_(None), Role.code == code)
                )
            ).scalar_one_or_none()
            if role is None:
                role = Role(
                    tenant_id=None,
                    code=code,
                    name=spec["name"],
                    permissions=list(spec["permissions"]),
                    is_system=True,
                )
                session.add(role)
                await session.flush()
            roles[code] = role
        slug = f"{label}-{uuid4().hex[:10]}"
        tenant = Tenant(name=label, slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        email = f"admin-{slug}@example.com"
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password("Password123!"),
            display_name="Admin",
        )
        session.add(user)
        await session.flush()
        session.add(
            UserRole(
                tenant_id=tenant.id,
                user_id=user.id,
                role_id=roles["tenant_admin"].id,
                scope_type="tenant",
                scope_id=tenant.id,
            )
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(*tenant_ids: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()
    # 附件真实落盘到 ./.snapshots-test（SNAPSHOT_STORE=local），按租户目录清理。
    for tenant_id in tenant_ids:
        shutil.rmtree(
            Path("./.snapshots-test/receipt-attachments") / str(tenant_id),
            ignore_errors=True,
        )


async def _login(client: AsyncClient, email: str) -> dict[str, str]:
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _create_project(client: AsyncClient, headers: dict[str, str], name: str) -> str:
    project = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    assert project.status_code == 201, project.text
    return project.json()["id"]


async def _create_receipt(
    client: AsyncClient, headers: dict[str, str], project_id: str, receipt_no: str
) -> str:
    receipt = await client.post(
        f"/api/v1/projects/{project_id}/submission-receipts",
        json={
            "submitted_at": "2026-10-31T09:30:00+08:00",
            "platform": "某省公共资源交易平台",
            "receipt_no": receipt_no,
        },
        headers=headers,
    )
    assert receipt.status_code == 201, receipt.text
    return receipt.json()["id"]


@pytest.mark.integration
async def test_submission_receipt_captures_readiness_snapshot() -> None:
    """回执落库 + 交付摘要。交付品牌取投标企业自身（Company.name），不是客户白标。"""
    tenant_id, email = await _seed_admin("submission-receipt")
    other_id, other_email = await _seed_admin("submission-receipt-other")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)

            project = await client.post(
                "/api/v1/projects", json={"name": "回执验收项目"}, headers=headers
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["id"]

            # 单一投标主体：交付主体就是企业自己，项目关联本企业即可
            company = await client.post(
                "/api/v1/companies",
                json={"name": "验收投标企业"},
                headers=headers,
            )
            assert company.status_code == 201, company.text
            branded_project = await client.post(
                "/api/v1/projects",
                json={"name": "企业交付项目", "company_id": company.json()["id"]},
                headers=headers,
            )
            assert branded_project.status_code == 201, branded_project.text
            branded_id = branded_project.json()["id"]

            receipt = await client.post(
                f"/api/v1/projects/{project_id}/submission-receipts",
                json={
                    "submitted_at": "2026-10-31T09:30:00+08:00",
                    "platform": "某省公共资源交易平台",
                    "receipt_no": "RECEIPT-001",
                    "notes": "人工确认已上传",
                },
                headers=headers,
            )
            assert receipt.status_code == 201, receipt.text
            body = receipt.json()
            assert body["receipt_no"] == "RECEIPT-001"
            assert body["checklist_summary"]["total"] > 0
            assert "blocking_open" in body["checklist_summary"]
            # 附件契约：新建回执默认空列表，字段存在（前端无需判空）。
            assert body["attachments"] == []

            listed = await client.get(
                f"/api/v1/projects/{project_id}/submission-receipts", headers=headers
            )
            assert listed.status_code == 200, listed.text
            assert len(listed.json()) == 1

            summary = await client.get(
                f"/api/v1/projects/{branded_id}/delivery-summary", headers=headers
            )
            assert summary.status_code == 200, summary.text
            # 品牌 = project.company_id 对应企业名；无 Logo（Company 无 logo 字段）
            assert summary.json()["brand"]["name"] == "验收投标企业"
            assert summary.json()["brand"]["logo_embedded"] is False
            assert summary.json()["brand"]["logo_url"] is None
            # 多客户概念已下线：交付摘要不再返回 client 字段
            assert "client" not in summary.json()
            assert summary.json()["readiness"]["total"] > 0
            assert summary.json()["receipt_attachments"]["total"] == 0

            # 未关联企业的项目回退 BidCopilot
            fallback = await client.get(
                f"/api/v1/projects/{project_id}/delivery-summary", headers=headers
            )
            assert fallback.status_code == 200, fallback.text
            assert fallback.json()["brand"]["name"] == "BidCopilot"

            other_headers = await _login(client, other_email)
            blocked = await client.get(
                f"/api/v1/projects/{project_id}/submission-receipts", headers=other_headers
            )
            assert blocked.status_code == 404
    finally:
        await _cleanup(tenant_id, other_id)


@pytest.mark.integration
async def test_submission_receipt_attachment_upload_download_delete() -> None:
    tenant_id, email = await _seed_admin("receipt-attachment")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, "附件留痕项目")
            receipt_id = await _create_receipt(client, headers, project_id, "RECEIPT-ATT-1")
            base = f"/api/v1/projects/{project_id}/submission-receipts/{receipt_id}/attachments"

            upload = await client.post(
                base,
                files={"file": ("平台回执.png", PNG, "image/png")},
                data={"kind": "receipt"},
                headers=headers,
            )
            assert upload.status_code == 201, upload.text
            stored = upload.json()
            assert stored["receipt_id"] == receipt_id
            assert stored["kind"] == "receipt"
            assert stored["filename"] == "平台回执.png"
            assert stored["content_type"] == "image/png"
            assert stored["size_bytes"] == len(PNG)
            assert stored["sha256"] == hashlib.sha256(PNG).hexdigest()
            # storage_ref 是内部实现细节，不应出现在 API 出口。
            assert "storage_ref" not in stored
            attachment_id = stored["id"]

            pdf_upload = await client.post(
                base,
                files={"file": ("签章凭证.pdf", PDF, "application/pdf")},
                data={"kind": "signature"},
                headers=headers,
            )
            assert pdf_upload.status_code == 201, pdf_upload.text
            assert pdf_upload.json()["content_type"] == "application/pdf"
            assert pdf_upload.json()["filename"] == "签章凭证.pdf"

            webp_upload = await client.post(
                base,
                files={"file": ("加密页.webp", WEBP, "image/webp")},
                data={"kind": "encryption"},
                headers=headers,
            )
            assert webp_upload.status_code == 201, webp_upload.text
            assert webp_upload.json()["content_type"] == "image/webp"

            # image/jpg 是客户端常见别名：归一化为 image/jpeg，不影响魔数校验。
            jpeg_upload = await client.post(
                base,
                files={"file": ("现场照片.jpg", JPEG, "image/jpg")},
                data={"kind": "other"},
                headers=headers,
            )
            assert jpeg_upload.status_code == 201, jpeg_upload.text
            assert jpeg_upload.json()["content_type"] == "image/jpeg"

            # 真实落盘：内容在 .snapshots-test 下，且与 SHA-256 对得上（不 Mock 存储层）。
            tenant_dir = Path("./.snapshots-test/receipt-attachments") / str(tenant_id) / receipt_id
            files = sorted(tenant_dir.glob("*"))
            assert len(files) == 4, files
            digests = {hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
            assert digests == {
                hashlib.sha256(PNG).hexdigest(),
                hashlib.sha256(PDF).hexdigest(),
                hashlib.sha256(WEBP).hexdigest(),
                hashlib.sha256(JPEG).hexdigest(),
            }

            # 回执列表回流附件（一次批量查询），按创建顺序稳定。
            listed = await client.get(
                f"/api/v1/projects/{project_id}/submission-receipts", headers=headers
            )
            assert listed.status_code == 200, listed.text
            rows = listed.json()
            assert len(rows) == 1
            attachments = rows[0]["attachments"]
            assert {item["id"] for item in attachments} == {
                attachment_id,
                pdf_upload.json()["id"],
                webp_upload.json()["id"],
                jpeg_upload.json()["id"],
            }
            assert sorted(item["kind"] for item in attachments) == [
                "encryption",
                "other",
                "receipt",
                "signature",
            ]
            assert all("storage_ref" not in item for item in attachments)

            # 交付摘要带附件计数与类型汇总。
            summary = await client.get(
                f"/api/v1/projects/{project_id}/delivery-summary", headers=headers
            )
            assert summary.status_code == 200, summary.text
            summary_body = summary.json()
            total_bytes = len(PNG) + len(PDF) + len(WEBP) + len(JPEG)
            assert summary_body["receipt_attachments"]["total"] == 4
            assert summary_body["receipt_attachments"]["total_bytes"] == total_bytes
            assert summary_body["receipt_attachments"]["by_kind"] == {
                "receipt": 1,
                "signature": 1,
                "encryption": 1,
                "other": 1,
            }
            assert len(summary_body["latest_receipt"]["attachments"]) == 4

            # 下载：字节一致，Content-Disposition 为附件且能还原中文名。
            download = await client.get(f"{base}/{attachment_id}", headers=headers)
            assert download.status_code == 200, download.text
            assert download.content == PNG
            assert download.headers["content-type"] == "image/png"
            disposition = download.headers["content-disposition"]
            assert disposition.startswith("attachment;")
            assert "filename*=UTF-8''" in disposition

            # 删除：204，且对象存储内容一起删掉，记录与下载都消失。
            deleted = await client.delete(f"{base}/{attachment_id}", headers=headers)
            assert deleted.status_code == 204, deleted.text
            after_delete = await client.get(f"{base}/{attachment_id}", headers=headers)
            assert after_delete.status_code == 404
            assert len(sorted(tenant_dir.glob("*"))) == 3
            relisted = await client.get(
                f"/api/v1/projects/{project_id}/submission-receipts", headers=headers
            )
            assert sorted(
                item["kind"] for item in relisted.json()[0]["attachments"]
            ) == ["encryption", "other", "signature"]

            # 留痕审计：上传与删除都要可追溯。
            async with SessionLocal() as session:
                actions = [
                    row.action
                    for row in (
                        await session.execute(
                            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
                        )
                    ).scalars()
                ]
            assert "project.submission_receipt.attachment.upload" in actions
            assert "project.submission_receipt.attachment.delete" in actions
    finally:
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_submission_receipt_attachment_rejects_bad_payload() -> None:
    tenant_id, email = await _seed_admin("receipt-attachment-invalid")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, "附件校验项目")
            receipt_id = await _create_receipt(client, headers, project_id, "RECEIPT-BAD-1")
            base = f"/api/v1/projects/{project_id}/submission-receipts/{receipt_id}/attachments"

            # 超过 10MB 上限。
            oversized = await client.post(
                base,
                files={
                    "file": (
                        "huge.png",
                        PNG + b"\x00" * ATTACHMENT_MAX_BYTES,
                        "image/png",
                    )
                },
                data={"kind": "receipt"},
                headers=headers,
            )
            assert oversized.status_code == 400, oversized.text
            assert "10MB" in oversized.text

            # 声明 image/png 实际是 PDF：魔数与声明不一致必须拒绝。
            mismatch = await client.post(
                base,
                files={"file": ("伪装.png", PDF, "image/png")},
                data={"kind": "receipt"},
                headers=headers,
            )
            assert mismatch.status_code == 400, mismatch.text
            assert "不一致" in mismatch.text

            # 白名单外类型（GIF）。
            unsupported = await client.post(
                base,
                files={"file": ("动图.gif", GIF, "image/gif")},
                data={"kind": "other"},
                headers=headers,
            )
            assert unsupported.status_code == 400, unsupported.text

            # kind 非法。
            bad_kind = await client.post(
                base,
                files={"file": ("回执.png", PNG, "image/png")},
                data={"kind": "platform_receipt"},
                headers=headers,
            )
            assert bad_kind.status_code == 400, bad_kind.text

            # 空文件。
            empty = await client.post(
                base,
                files={"file": ("空.png", b"", "image/png")},
                data={"kind": "receipt"},
                headers=headers,
            )
            assert empty.status_code == 400, empty.text

            # 被拒绝的请求不得留下任何附件记录或落盘文件。
            listed = await client.get(
                f"/api/v1/projects/{project_id}/submission-receipts", headers=headers
            )
            assert listed.json()[0]["attachments"] == []
            tenant_dir = Path("./.snapshots-test/receipt-attachments") / str(tenant_id)
            assert not tenant_dir.exists() or not list(tenant_dir.rglob("*"))
    finally:
        await _cleanup(tenant_id)


@pytest.mark.integration
async def test_submission_receipt_attachment_cross_scope_is_404() -> None:
    tenant_id, email = await _seed_admin("receipt-attachment-scope")
    other_id, other_email = await _seed_admin("receipt-attachment-scope-other")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = await _login(client, email)
            project_id = await _create_project(client, headers, "附件归属项目")
            other_project_id = await _create_project(client, headers, "同租户另一项目")
            receipt_id = await _create_receipt(client, headers, project_id, "RECEIPT-SCOPE-1")
            base = f"/api/v1/projects/{project_id}/submission-receipts/{receipt_id}/attachments"

            upload = await client.post(
                base,
                files={"file": ("回执.png", PNG, "image/png")},
                data={"kind": "receipt"},
                headers=headers,
            )
            assert upload.status_code == 201, upload.text
            attachment_id = upload.json()["id"]

            # 跨项目：同一租户、回执不属于该 project → 404（不泄露存在性）。
            wrong_project = (
                f"/api/v1/projects/{other_project_id}/submission-receipts/{receipt_id}/attachments"
            )
            assert (
                await client.post(
                    wrong_project,
                    files={"file": ("回执.png", PNG, "image/png")},
                    data={"kind": "receipt"},
                    headers=headers,
                )
            ).status_code == 404
            assert (
                await client.get(f"{wrong_project}/{attachment_id}", headers=headers)
            ).status_code == 404
            assert (
                await client.delete(f"{wrong_project}/{attachment_id}", headers=headers)
            ).status_code == 404

            # 跨租户：其他租户看不到该 project 的任何回执/附件 → 一律 404。
            other_headers = await _login(client, other_email)
            assert (
                await client.post(
                    base,
                    files={"file": ("回执.png", PNG, "image/png")},
                    data={"kind": "receipt"},
                    headers=other_headers,
                )
            ).status_code == 404
            assert (
                await client.get(f"{base}/{attachment_id}", headers=other_headers)
            ).status_code == 404
            assert (
                await client.delete(f"{base}/{attachment_id}", headers=other_headers)
            ).status_code == 404
            # 不存在的附件 id 同样 404，而不是 500/403。
            assert (
                await client.get(f"{base}/{uuid4()}", headers=headers)
            ).status_code == 404

            # 越权/跨域失败后原附件仍然可正常下载。
            still_there = await client.get(f"{base}/{attachment_id}", headers=headers)
            assert still_there.status_code == 200
            assert still_there.content == PNG
    finally:
        await _cleanup(tenant_id, other_id)
