"""暗标上传件自查接口：身份泄露、自指、联系方式与元数据。"""

from io import BytesIO
from uuid import UUID, uuid4

import pytest
from docx import Document
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.main import app
from app.modules.assets.models import Company, Personnel
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES

COMPANY_NAME = "北京华信科技有限公司"


def _docx(paragraphs: list[str], *, author: str = "", header: str = "") -> bytes:
    document = Document()
    document.core_properties.author = author
    for text in paragraphs:
        document.add_paragraph(text)
    if header:
        document.sections[0].header.paragraphs[0].text = header
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def _seed() -> tuple[UUID, str]:
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
        slug = f"anbiao-{uuid4().hex[:10]}"
        tenant = Tenant(name="演示投标企业", slug=slug, kind="customer")
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
        company = Company(
            tenant_id=tenant.id,
            name=COMPANY_NAME,
            credit_code="91110108MA01ABCD2X",
            contacts={"phone": "010-88886666"},
            source="manual",
        )
        session.add(company)
        await session.flush()
        session.add(
            Personnel(
                tenant_id=tenant.id,
                company_id=company.id,
                dedup_key=f"p-{uuid4().hex[:8]}",
                name="张三",
                cert_type="一级建造师",
                cert_no="BJ-2024-000123",
                source="manual",
            )
        )
        await session.commit()
        return tenant.id, email


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


@pytest.mark.integration
async def test_anbiao_check_flags_identity_and_metadata() -> None:
    tenant_id, email = await _seed()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
            )
            assert login.status_code == 200, login.text
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            content = _docx(
                [
                    f"我公司（{COMPANY_NAME}）承诺：项目经理张三，电话 010-88886666，"
                    "信用代码 91110108MA01ABCD2X，证书编号 BJ-2024-000123。",
                    "本项目将按招标文件要求实施。",
                ],
                author="张三",
                header=f"{COMPANY_NAME} 技术标",
            )
            response = await client.post(
                "/api/v1/file-check/anbiao",
                files={
                    "file": (
                        f"{COMPANY_NAME}-技术标.docx",
                        content,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                headers=headers,
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["status"] == "warning"
            rule_ids = {item["rule_id"] for item in payload["findings"]}
            assert {"BID-101", "BID-102", "BID-103", "BID-104", "BID-105"} <= rule_ids
            assert {"BID-106", "BID-107", "BID-108"} <= rule_ids
            assert payload["summary"]["hard"] == len(payload["findings"])

            # 干净文件：正文无身份、元数据为空 → 通过
            clean = _docx(["投标人将按招标文件要求实施，项目经理负责总体协调。"], author="")
            passed = await client.post(
                "/api/v1/file-check/anbiao",
                files={
                    "file": (
                        "技术标.docx",
                        clean,
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
                headers=headers,
            )
            assert passed.status_code == 200, passed.text
            assert passed.json()["status"] == "ok"
            assert passed.json()["findings"] == []
    finally:
        await _cleanup(tenant_id)
