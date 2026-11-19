"""暗标导出端到端：身份信息必须零残留，元数据必须清空。"""

import zipfile
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.modules.assets.models import Company, Personnel
from app.modules.generation.models import BidSection
from app.modules.generation.service import export_docx
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.projects.models import BidProject

COMPANY_NAME = "北京华信科技有限公司"
CREDIT_CODE = "91110108MA01ABCD2X"
PHONE = "010-88886666"
PERSON = "张三"


async def _seed() -> tuple[UUID, UUID]:
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
        slug = f"dark-bid-{uuid4().hex[:10]}"
        tenant = Tenant(name="演示投标企业", slug=slug, kind="customer")
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"admin-{slug}@example.com",
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
            credit_code=CREDIT_CODE,
            contacts={"phone": PHONE, "email": "bid@huaxin.example.com"},
            source="manual",
        )
        session.add(company)
        await session.flush()
        session.add(
            Personnel(
                tenant_id=tenant.id,
                company_id=company.id,
                dedup_key=f"p-{uuid4().hex[:8]}",
                name=PERSON,
                cert_type="一级建造师",
                source="manual",
            )
        )
        project = BidProject(
            tenant_id=tenant.id,
            company_id=company.id,
            name="某市政务云平台建设项目",
            buyer="某市大数据局",
            status="active",
        )
        session.add(project)
        await session.flush()
        leaks = (
            f"我公司（{COMPANY_NAME}）承诺：项目经理{PERSON}，联系电话 {PHONE}，"
            f"统一社会信用代码 {CREDIT_CODE}。"
        )
        session.add(
            BidSection(
                tenant_id=tenant.id,
                project_id=project.id,
                title="技术方案",
                section_type="scoring",
                order_index=0,
                content_json=[
                    {"type": "paragraph", "text": leaks},
                    {
                        "type": "table",
                        "caption": "项目团队",
                        "headers": ["岗位", "人员"],
                        "rows": [["项目经理", PERSON]],
                    },
                ],
                content_text=leaks,
                status="approved",
            )
        )
        await session.commit()
        return tenant.id, project.id


async def _cleanup(tenant_id: UUID) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


def _document_text(data: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for name in archive.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                parts.append(archive.read(name).decode("utf-8", errors="ignore"))
    import re

    return re.sub(r"<[^>]+>", "", "\n".join(parts))


@pytest.mark.integration
async def test_dark_bid_export_strips_identity_and_metadata() -> None:
    tenant_id, project_id = await _seed()
    try:
        async with SessionLocal() as session:
            dark, filename, findings = await export_docx(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                dark_bid=True,
            )
        text = _document_text(dark)
        for leak in (COMPANY_NAME, "北京华信科技", PERSON, PHONE, CREDIT_CODE, "我公司"):
            assert leak not in text, leak
        assert "投标人" in text
        assert filename.endswith("_dark.docx")
        assert findings == []

        with zipfile.ZipFile(BytesIO(dark)) as archive:
            core = archive.read("docProps/core.xml").decode("utf-8")
            assert "<dc:creator></dc:creator>" in core
            assert "张三" not in core

        # 对照：明标导出保留投标人名称（不做脱敏）
        async with SessionLocal() as session:
            normal, normal_name, _ = await export_docx(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                dark_bid=False,
            )
        assert COMPANY_NAME in _document_text(normal)
        assert normal_name.endswith("_normal.docx")
    finally:
        await _cleanup(tenant_id)
