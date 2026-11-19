from io import BytesIO
from uuid import UUID, uuid4

from docx import Document
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.modules.audit.models import AuditEvent
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES


def sample_tender_docx() -> bytes:
    document = Document()
    document.add_heading("第一章 招标公告", level=1)
    document.add_paragraph("本项目最高限价为 1,200,000 元。")
    document.add_paragraph("投标截止时间为 2026年11月10日 09:00。")
    document.add_paragraph("未提供有效营业执照的投标将被否决。")
    document.add_paragraph("投标人须具备涉密信息系统集成乙级及以上资质。")
    document.add_paragraph("项目经理须具备 PMP 证书。")
    document.add_paragraph("近三年具有不少于 2 个类似项目业绩。")
    document.add_paragraph("投标文件应采用 A4 纸，正文宋体小四。")
    document.add_paragraph("本项目采用暗标，不得出现公司名称。")
    document.add_paragraph("投标文件应包括营业执照、报价表、技术方案。")
    table = document.add_table(rows=4, cols=3)
    table.cell(0, 0).text = "评分项"
    table.cell(0, 1).text = "分值"
    table.cell(0, 2).text = "评分标准"
    table.cell(1, 0).text = "技术方案"
    table.cell(1, 1).text = "40 分"
    table.cell(1, 2).text = "方案完整、可落地"
    table.cell(2, 0).text = "项目经理（PMP）"
    table.cell(2, 1).text = "20 分"
    table.cell(2, 2).text = "人员配置合理"
    table.cell(3, 0).text = "投标报价"
    table.cell(3, 1).text = "40 分"
    table.cell(3, 2).text = "价格分"
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def sample_tender_docx_merged_scoring() -> bytes:
    """复刻真实公告的两个坑：预算单位写在括号里、评分表合并单元格导致多行共用评审内容。"""
    document = Document()
    document.add_heading("第一章 公开招标采购公告", level=1)
    document.add_paragraph("预算金额（元）：1559100")
    document.add_paragraph("最高限价（元）：1559100")
    document.add_paragraph("投标截止时间为 2026年11月10日 09:00。")
    document.add_paragraph("预算金额未达100万元的采购项目，由采购人处理采购争议。")
    table = document.add_table(rows=5, cols=4)
    header = ("序号", "评审内容", "评审细则", "分值")
    for col, text in enumerate(header):
        table.cell(0, col).text = text
    rows = (
        ("1", "投标供应商履约能力评价", "1.1质量管理能力评价：良好的内部质量控制能力", "2"),
        ("1", "投标供应商履约能力评价", "1.2信息安全管理能力评价：体系化的信息安全管理", "2"),
        ("2", "投标响应情况", "2.1投标响应情况与采购需求一致（分值：18分）", "18"),
        ("3", "技术服务实施方案评价", "3.1需求理解情况评价（分值：5分）", "5"),
    )
    for index, row in enumerate(rows, start=1):
        for col, text in enumerate(row):
            table.cell(index, col).text = text
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def seed_tenant_user(
    label: str, role_code: str = "tenant_admin", tenant_id: UUID | None = None
) -> tuple[UUID, UUID, str]:
    """建租户与挂指定系统角色的用户；给出 tenant_id 时在该租户内追加用户。"""
    async with SessionLocal() as session:
        role = (
            await session.execute(
                select(Role).where(Role.tenant_id.is_(None), Role.code == role_code)
            )
        ).scalar_one_or_none()
        if role is None:
            spec = SYSTEM_ROLES[role_code]
            role = Role(
                tenant_id=None,
                code=role_code,
                name=spec["name"],
                permissions=list(spec["permissions"]),
                is_system=True,
            )
            session.add(role)
            await session.flush()
        if tenant_id is None:
            slug = f"{label}-{uuid4().hex[:10]}"
            tenant = Tenant(name=label, slug=slug, kind="customer")
            session.add(tenant)
            await session.flush()
            email = f"{slug}@example.com"
        else:
            tenant = await session.get(Tenant, tenant_id)
            assert tenant is not None
            email = f"{tenant.slug}-{role_code}-{uuid4().hex[:6]}@example.com"
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
        return tenant.id, user.id, email


async def login(client, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "Password123!"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def cleanup_tenants(tenant_ids: list[UUID]) -> None:
    async with SessionLocal() as session:
        await session.execute(delete(AuditEvent).where(AuditEvent.tenant_id.in_(tenant_ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(tenant_ids)))
        await session.commit()
