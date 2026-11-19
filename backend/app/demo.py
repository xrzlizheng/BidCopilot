from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.modules.assets.domain import asset_dedup_key
from app.modules.assets.models import Company, Credit, Performance, Personnel, Qualification
from app.modules.generation.models import BidSection
from app.modules.identity.models import Role, Tenant, User, UserRole
from app.modules.identity.roles import SYSTEM_ROLES
from app.modules.opportunities.models import Opportunity  # noqa: F401  (register FK target)
from app.modules.org.models import OrgUnit  # noqa: F401  (register FK target)
from app.modules.projects.models import (
    BidProject,
    BidResult,
    ScoringItem,
    TenderConstraint,
    TenderDocument,
)

DEMO_COMPANY = "演示系统集成有限公司"
DEMO_PROJECT = "某市政务云系统集成项目"
DEMO_LOST_PROJECT = "某区视频监控运维项目"


@dataclass(frozen=True)
class DemoSeedResult:
    tenant_id: UUID
    tenant_slug: str
    email: str
    company_id: UUID
    project_ids: list[UUID]


async def _ensure_roles(session: AsyncSession) -> dict[str, Role]:
    roles: dict[str, Role] = {}
    for code, spec in SYSTEM_ROLES.items():
        role = (
            await session.execute(select(Role).where(Role.tenant_id.is_(None), Role.code == code))
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                tenant_id=None,
                code=code,
                name=str(spec["name"]),
                permissions=list(spec["permissions"]),
                is_system=True,
            )
            session.add(role)
            await session.flush()
        else:
            role.name = str(spec["name"])
            role.permissions = list(spec["permissions"])
        roles[code] = role
    return roles


async def _ensure_tenant_user(
    session: AsyncSession,
    roles: dict[str, Role],
    *,
    name: str,
    slug: str,
    email: str,
    password: str,
) -> tuple[Tenant, User]:
    tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
    if tenant is None:
        tenant = Tenant(name=name, slug=slug, kind="customer", status="active")
        session.add(tenant)
        await session.flush()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(
            tenant_id=tenant.id,
            email=email,
            password_hash=hash_password(password),
            display_name="演示管理员",
            status="active",
        )
        session.add(user)
        await session.flush()
    elif user.tenant_id != tenant.id:
        raise ValueError(f"email {email} belongs to another tenant")
    else:
        # 已存在的账号不覆写口令：重复 seed 不应把运维改过的密码重置回演示值。
        user.status = "active"
    link = (
        await session.execute(
            select(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role_id == roles["tenant_admin"].id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        session.add(
            UserRole(tenant_id=tenant.id, user_id=user.id, role_id=roles["tenant_admin"].id, scope_type="tenant", scope_id=tenant.id)
        )
    return tenant, user


async def _ensure_company(session: AsyncSession, tenant_id: UUID) -> Company:
    company = (
        await session.execute(
            select(Company).where(Company.tenant_id == tenant_id, Company.name == DEMO_COMPANY)
        )
    ).scalar_one_or_none()
    if company is None:
        company = Company(
            tenant_id=tenant_id,
            name=DEMO_COMPANY,
            credit_code="91510000DEMO000001",
            region="辽宁省 盘锦市",
            industry="信息通信",
            contacts={"phone": "0427-0000000", "email": "demo@example.com"},
            source="demo",
        )
        session.add(company)
        await session.flush()
    return company


async def _asset_exists(session: AsyncSession, model, tenant_id: UUID, dedup_key: str) -> bool:
    return (
        await session.execute(
            select(model.id).where(model.tenant_id == tenant_id, model.dedup_key == dedup_key)
        )
    ).scalar_one_or_none() is not None


async def _ensure_assets(session: AsyncSession, tenant_id: UUID, company: Company) -> None:
    qualification_values = {
        "name": "信息系统集成及服务资质",
        "level": "二级",
        "cert_no": "ITSS-DEMO-001",
        "issuer": "中国电子工业标准化技术协会",
        "valid_from": date(2024, 1, 1),
        "valid_to": date(2027, 12, 31),
    }
    qualification_key = asset_dedup_key("qualifications", company.id, qualification_values)
    if not await _asset_exists(session, Qualification, tenant_id, qualification_key):
        session.add(
            Qualification(
                tenant_id=tenant_id,
                company_id=company.id,
                dedup_key=qualification_key,
                source="demo",
                **qualification_values,
            )
        )

    personnel_values = {
        "name": "张工",
        "cert_type": "PMP",
        "level": "高级",
        "major": "项目管理",
        "cert_no": "PMP-DEMO-001",
        "valid_to": date(2027, 6, 30),
    }
    personnel_key = asset_dedup_key("personnel", company.id, personnel_values)
    if not await _asset_exists(session, Personnel, tenant_id, personnel_key):
        session.add(
            Personnel(
                tenant_id=tenant_id,
                company_id=company.id,
                dedup_key=personnel_key,
                source="demo",
                **personnel_values,
            )
        )

    performance_values = {
        "project_name": "某市政务云平台建设项目",
        "amount": Decimal("1800000"),
        "complete_date": date(2025, 9, 30),
        "buyer": "某市大数据局",
        "category": "系统集成",
    }
    performance_key = asset_dedup_key("performances", company.id, performance_values)
    if not await _asset_exists(session, Performance, tenant_id, performance_key):
        session.add(
            Performance(
                tenant_id=tenant_id,
                company_id=company.id,
                dedup_key=performance_key,
                source="demo",
                **performance_values,
            )
        )

    credit_values = {"check_item": "失信被执行人", "checked_at": datetime(2026, 9, 1, tzinfo=UTC)}
    credit_key = asset_dedup_key("credits", company.id, credit_values)
    if not await _asset_exists(session, Credit, tenant_id, credit_key):
        session.add(
            Credit(
                tenant_id=tenant_id,
                company_id=company.id,
                dedup_key=credit_key,
                status="未发现",
                source="demo",
                **credit_values,
            )
        )


async def _ensure_project(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    company_id: UUID,
    name: str,
    code: str,
    buyer: str,
    deadline: datetime,
    status: str,
) -> BidProject:
    project = (
        await session.execute(
            select(BidProject).where(BidProject.tenant_id == tenant_id, BidProject.name == name)
        )
    ).scalar_one_or_none()
    if project is None:
        project = BidProject(
            tenant_id=tenant_id,
            company_id=company_id,
            name=name,
            code=code,
            buyer=buyer,
            status=status,
            bid_deadline=deadline,
        )
        session.add(project)
        await session.flush()
    return project


async def _ensure_constraint(session: AsyncSession, tenant_id: UUID, project: BidProject) -> None:
    existing = (
        (
            await session.execute(
                select(TenderConstraint).where(
                    TenderConstraint.tenant_id == tenant_id,
                    TenderConstraint.project_id == project.id,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return
    document = TenderDocument(
        tenant_id=tenant_id,
        project_id=project.id,
        filename="demo-tender.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=2048,
        file_hash="d" * 64,
        object_ref="demo://tender/demo-tender.docx",
        parse_status="parsed",
        parsed_at=datetime.now(UTC),
    )
    session.add(document)
    await session.flush()

    scoring_specs = [
        ("技术", "技术方案", Decimal("30"), "方案完整可行，含实施计划、人员安排与保障措施"),
        ("商务", "项目业绩", Decimal("20"), "提供同类项目合同，单项不低于100万元"),
        ("服务", "服务承诺", Decimal("10"), "响应时间、驻场安排与故障处理机制"),
    ]
    items = [
        ScoringItem(
            tenant_id=tenant_id,
            project_id=project.id,
            constraint_id=None,  # set after constraint flush
            category=category,
            item=item,
            score=score,
            criteria=criteria,
            source_span={"quote": f"{item}：{score}分"},
            confidence="high",
        )
        for category, item, score, criteria in scoring_specs
    ]
    quote = {
        "project": {
            "name": project.name,
            "code": project.code or "",
            "buyer": project.buyer or "",
            "bid_deadline": project.bid_deadline.isoformat() if project.bid_deadline else "",
        },
        "reject_clauses": [
            {
                "text": "投标文件未按要求密封或逾期送达的，将被否决。",
                "severity": "HARD",
                "source_span": {"quote": "未按要求密封或逾期送达的，将被否决"},
            }
        ],
        "scoring_items": [
            {
                "category": category,
                "item": item,
                "score": float(score),
                "criteria": criteria,
                "source_span": {"quote": f"{item}：{score}分"},
                "confidence": "high",
            }
            for category, item, score, criteria in scoring_specs
        ],
        "qualification_requirements": [
            {
                "category": "QUALIFICATION",
                "text": "投标人须具备信息系统集成及服务资质二级及以上。",
                "name": "信息系统集成及服务资质",
                "level": "二级",
                "quantity": None,
                "source_span": {"quote": "须具备信息系统集成及服务资质二级及以上"},
                "confidence": "high",
            },
            {
                "category": "PERSONNEL",
                "text": "项目经理须具备PMP证书，且不少于1名。",
                "name": "项目经理",
                "level": None,
                "quantity": 1,
                "source_span": {"quote": "项目经理须具备PMP证书"},
                "confidence": "medium",
            },
        ],
        "format_rules": [
            {
                "text": "投标文件采用A4纸，正文字体为宋体小四。",
                "source_span": {"quote": "A4纸，正文字体为宋体小四"},
            }
        ],
        "timeline": [
            {
                "event": "投标截止",
                "at": project.bid_deadline.isoformat() if project.bid_deadline else "",
                "source_span": {"quote": "投标截止时间"},
            }
        ],
        "budget": {"amount": "1980000", "currency": "CNY"},
        "submission_checklist": [
            {"text": "投标函", "source_span": {"quote": "投标文件组成：投标函"}},
            {"text": "资格证明文件", "source_span": {"quote": "资格证明文件"}},
            {"text": "技术方案", "source_span": {"quote": "技术方案"}},
        ],
        "dark_bid": {"required": False, "rules": []},
        "parse_meta": {
            "parser_version": "demo",
            "ocr_used": False,
            "llm_used": False,
            "warnings": ["演示数据，非真实招标文件"],
        },
    }
    constraint = TenderConstraint(
        tenant_id=tenant_id,
        project_id=project.id,
        document_id=document.id,
        version=1,
        payload=quote,
        parse_meta=quote["parse_meta"],
    )
    session.add(constraint)
    await session.flush()
    for item in items:
        item.constraint_id = constraint.id
        session.add(item)
    await session.flush()

    sections = [
        (
            "投标函",
            "cover",
            None,
            "致：某市大数据局\n我方已认真阅读招标文件，愿以人民币壹佰玖拾捌万元整参与投标。",
        ),
        (
            "资格证明文件",
            "qualification",
            None,
            "我方具备信息系统集成及服务资质二级，项目经理持有PMP证书。",
        ),
        (
            "技术方案响应",
            "scoring",
            items[0].id,
            "技术方案响应：采用政务云双活架构，分三阶段实施，配置项目经理1名、实施工程师4名，提供7×24小时保障。",
        ),
        (
            "项目业绩响应",
            "scoring",
            items[1].id,
            "项目业绩响应：近年完成某市政务云平台建设项目，合同金额180万元，已通过验收。",
        ),
        (
            "服务承诺响应",
            "scoring",
            items[2].id,
            "服务承诺响应：故障15分钟响应、2小时到场，提供季度巡检与驻场服务。",
        ),
    ]
    for index, (title, section_type, scoring_item_id, text) in enumerate(sections, start=1):
        session.add(
            BidSection(
                tenant_id=tenant_id,
                project_id=project.id,
                title=title,
                section_type=section_type,
                order_index=index,
                scoring_item_id=scoring_item_id,
                content_json=[{"type": "paragraph", "text": text}],
                content_text=text,
                status="draft",
                generation_mode="rules",
                factcheck_status="pending",
            )
        )
    await session.flush()


async def _ensure_result(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project: BidProject,
    outcome: str,
    win_amount: Decimal | None,
    loss_reason: str | None,
    competitor: str | None,
    notes: str,
) -> None:
    existing = (
        await session.execute(
            select(BidResult).where(
                BidResult.tenant_id == tenant_id, BidResult.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        BidResult(
            tenant_id=tenant_id,
            project_id=project.id,
            outcome=outcome,
            win_amount=win_amount,
            loss_reason=loss_reason,
            competitor=competitor,
            notes=notes,
            recorded_at=datetime.now(UTC),
        )
    )


async def seed_demo(
    session: AsyncSession,
    *,
    tenant_name: str = "演示投标企业",
    tenant_slug: str = "demo",
    email: str = "demo@example.com",
    # 演示口令禁止内置默认值：公开仓库中的弱默认凭据会被安全扫描命中，
    # 且演示租户常被部署到可达环境。必须由调用方显式提供——
    # CLI 走 `seed-demo --password` 或交互式 getpass，缺失即报错。
    password: str | None = None,
) -> DemoSeedResult:
    if not password:
        raise ValueError(
            "seed_demo 必须显式提供 password（演示管理员口令）："
            "请通过 CLI `seed-demo --password` 或交互式输入，不提供任何默认口令"
        )
    email = email.strip().lower()
    roles = await _ensure_roles(session)
    tenant, _ = await _ensure_tenant_user(
        session, roles, name=tenant_name, slug=tenant_slug, email=email, password=password
    )
    company = await _ensure_company(session, tenant.id)
    await _ensure_assets(session, tenant.id, company)
    deadline = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
    won = await _ensure_project(
        session,
        tenant_id=tenant.id,
        company_id=company.id,
        name=DEMO_PROJECT,
        code="DEMO-2026-001",
        buyer="某市大数据局",
        deadline=deadline,
        status="won",
    )
    await _ensure_constraint(session, tenant.id, won)
    await _ensure_result(
        session,
        tenant_id=tenant.id,
        project=won,
        outcome="won",
        win_amount=Decimal("1980000"),
        loss_reason=None,
        competitor="某竞争对手",
        notes="技术分领先，服务承诺完整。",
    )
    lost = await _ensure_project(
        session,
        tenant_id=tenant.id,
        company_id=company.id,
        name=DEMO_LOST_PROJECT,
        code="DEMO-2026-002",
        buyer="某区公安分局",
        deadline=datetime(2026, 9, 20, 1, 0, tzinfo=UTC),
        status="lost",
    )
    await _ensure_result(
        session,
        tenant_id=tenant.id,
        project=lost,
        outcome="lost",
        win_amount=None,
        loss_reason="price",
        competitor="某本地集成商",
        notes="报价高于对手约8%，技术分接近。",
    )
    await session.commit()
    return DemoSeedResult(
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        email=email,
        company_id=company.id,
        project_ids=[won.id, lost.id],
    )
