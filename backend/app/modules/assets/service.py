from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.time import business_today
from app.modules.assets.access import (
    can_read_org,
    can_write_org,
    visibility_filter,
)
from app.modules.assets.domain import ASSET_KINDS, asset_dedup_key
from app.modules.assets.enterprise_scale import classify_enterprise, is_sme
from app.modules.assets.field_policies import apply_field_policies
from app.modules.assets.models import Company, Personnel, Qualification
from app.modules.assets.repository import AssetRepository, CompanyRepository
from app.modules.assets.schemas import (
    AssetItem,
    CompanyCreate,
    CompanyUpdate,
    CreditCreate,
    PerformanceCreate,
    PersonnelCreate,
    QualificationCreate,
)
from app.modules.audit.models import AuditEvent
from app.modules.identity.schemas import CurrentUser
from app.modules.org.models import OrgUnit
from app.modules.projects.models import BidProject

CREATE_SCHEMAS: dict[str, Any] = {
    "qualifications": QualificationCreate,
    "personnel": PersonnelCreate,
    "performances": PerformanceCreate,
    "credits": CreditCreate,
}


async def get_company(session: AsyncSession, tenant_id: UUID, company_id: UUID) -> Company:
    return await CompanyRepository(session, tenant_id).get_or_404(company_id)


async def list_companies(session: AsyncSession, tenant_id: UUID, q: str | None) -> list[Company]:
    repo = CompanyRepository(session, tenant_id)
    statement = repo.scoped(select(Company))
    if q:
        statement = statement.where(Company.name.ilike(f"%{q}%"))
    return list((await session.execute(statement.order_by(Company.name))).scalars().all())


async def create_company(session: AsyncSession, tenant_id: UUID, body: CompanyCreate) -> Company:
    repo = CompanyRepository(session, tenant_id)
    existing = (
        await session.execute(repo.scoped(select(Company).where(Company.name == body.name)))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("企业已存在")
    payload = body.model_dump()
    company = Company(
        tenant_id=tenant_id,
        name=body.name,
        credit_code=body.credit_code,
        region=body.region,
        industry=body.industry,
        contacts=body.contacts,
        source="manual",
    )
    # 先把划型原始指标写到实例上，再算划型——否则 _apply_scale_inputs 读到的都是
    # 默认 None，会得出「缺指标」的错误结论。
    for field in SCALE_INPUT_FIELDS:
        if payload.get(field) is not None:
            setattr(company, field, payload[field])
    _apply_scale_inputs(company, payload)
    session.add(company)
    await session.commit()
    await session.refresh(company)
    return company


async def update_company(
    session: AsyncSession, tenant_id: UUID, company_id: UUID, body: CompanyUpdate
) -> Company:
    company = await get_company(session, tenant_id, company_id)
    payload = body.model_dump(exclude_unset=True)
    for field, value in payload.items():
        setattr(company, field, value)
    _apply_scale_inputs(company, payload)
    await session.commit()
    await session.refresh(company)
    return company


async def scale_history(
    session: AsyncSession, tenant_id: UUID, company_id: UUID, limit: int = 20
) -> list[dict[str, Any]]:
    """划型的变更历史。

    声明函纠纷时这是自证材料：能证明「投标当时的划型依据是什么、谁确认的」。
    数据取自审计事件（带哈希链，不可篡改），而不是另建一张业务表。
    """
    rows = (
        await session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == tenant_id,
                AuditEvent.resource_type == "company",
                AuditEvent.resource_id == str(company_id),
                AuditEvent.action == "asset.company.update",
            )
            .order_by(AuditEvent.seq.desc())
            .limit(limit * 3)
        )
    ).scalars()

    history: list[dict[str, Any]] = []
    for event in rows:
        data = event.data or {}
        change = data.get("scale_change")
        if not change:
            continue
        history.append(
            {
                "at": event.created_at.isoformat(),
                "actor_id": str(event.actor_id) if event.actor_id else None,
                "from": change.get("from"),
                "to": change.get("to"),
                "crossed_boundary": is_sme(change.get("from")) != is_sme(change.get("to")),
            }
        )
        if len(history) >= limit:
            break
    return history


async def scale_change_impact(
    session: AsyncSession,
    tenant_id: UUID,
    company_id: UUID,
    previous_scale: str | None,
    user: Any,
) -> dict[str, Any]:
    """评估划型变化对在投项目的影响。

    为什么只有「跨过中小企业分界线」才提醒：
    小型→中型不影响能否投中小企业项目（同属中小企业），提醒了就是噪音；
    中型→大型则会让人**丧失投标资格**，声明函一旦交了就是提供虚假材料。
    """
    company = await get_company(session, tenant_id, company_id)
    current = company_scale(company)
    before = is_sme(previous_scale)
    after = current["is_sme"]

    crossed = before is not None and after is not None and before != after
    changed = previous_scale != current["scale"]

    affected: list[dict[str, Any]] = []
    if crossed or (changed and current["is_sme"] is False):
        # 只查在投项目：won/lost 已成事实，提醒也没有挽回余地
        rows = (
            await session.execute(
                select(BidProject.id, BidProject.name, BidProject.bid_deadline)
                .where(
                    BidProject.tenant_id == tenant_id,
                    BidProject.company_id == company_id,
                    BidProject.status == "active",
                )
                .order_by(BidProject.created_at.desc())
                .limit(50)
            )
        ).all()
        for project_id, name, deadline in rows:
            affected.append(
                {
                    "project_id": str(project_id),
                    "name": name,
                    "bid_deadline": deadline.isoformat() if deadline else None,
                }
            )

    return {
        "previous_scale": previous_scale,
        "current_scale": current["scale"],
        "previous_is_sme": before,
        "current_is_sme": after,
        "crossed_boundary": crossed,
        "needs_review": bool(affected),
        "affected_projects": affected,
        "message": _impact_message(previous_scale, current["scale"], crossed, len(affected)),
    }


def _impact_message(
    previous: str | None, current: str | None, crossed: bool, count: int
) -> str:
    if previous == current:
        return "划型未变化"
    if crossed and count:
        return (
            f"划型由「{previous or '未判定'}」变为「{current}」，已跨越中小企业分界线；"
            f"{count} 个在投项目的中小企业声明函需重新核对，错填属于提供虚假材料"
        )
    if crossed:
        return f"划型由「{previous or '未判定'}」变为「{current}」，已跨越中小企业分界线，请复核在投项目"
    if count:
        return f"划型由「{previous or '未判定'}」变为「{current}」，{count} 个在投项目建议复核"
    return f"划型由「{previous or '未判定'}」变为「{current}」，暂无在投项目受影响"


SCALE_INPUT_FIELDS = (
    "employee_count",
    "annual_revenue",
    "total_assets",
    "enterprise_industry",
)
SCALE_FIELDS = (
    "employee_count",
    "annual_revenue",
    "total_assets",
    "enterprise_industry",
    "enterprise_scale",
)
MANUAL_SCALES = {"大型", "中型", "小型", "微型"}


def _apply_scale_inputs(company: Company, payload: dict[str, Any]) -> None:
    """维护中小企业划型：人工确认值优先，其余情况按标准自动计算。

    规则：
    - 显式传 enterprise_scale：视为人工确认，source=manual，不再自动重算；
    - 未传 enterprise_scale 但划型要素有变更：自动重算，source=auto；
    - 要素不全：清空自动结果（保留人工值），并记录缺少什么——绝不保留过期结论。
    """
    touched = any(field in payload for field in SCALE_FIELDS)
    if not touched:
        return

    manual = payload.get("enterprise_scale") in MANUAL_SCALES
    if manual:
        # 必须显式写入，否则只记了 source=manual 却没有值，前端查到 None
        company.enterprise_scale = payload["enterprise_scale"]
        company.enterprise_scale_source = "manual"
        company.enterprise_scale_checked_at = datetime.now(UTC)
        return

    if company.enterprise_scale_source == "manual" and "enterprise_scale" not in payload:
        # 人工确认过且本次未显式改结论：尊重人工值，但仍刷新核对时间
        company.enterprise_scale_checked_at = datetime.now(UTC)
        return

    result = classify_enterprise(
        industry=company.enterprise_industry or company.industry,
        employees=company.employee_count,
        revenue=company.annual_revenue,
        total_assets=company.total_assets,
    )
    company.enterprise_scale = result.scale
    company.enterprise_scale_source = "auto"
    company.enterprise_scale_checked_at = datetime.now(UTC)


def company_scale(company: Company) -> dict[str, Any]:
    """读取划型：人工确认值直接采用；否则按当前要素即时计算（不依赖历史落库值）。"""
    if company.enterprise_scale_source == "manual" and company.enterprise_scale:
        return {
            "scale": company.enterprise_scale,
            "industry": company.enterprise_industry,
            "industry_label": None,
            "basis": "人工确认值",
            "missing": [],
            "is_sme": is_sme(company.enterprise_scale),
            "source": "manual",
            "confirmed": True,
        }
    result = classify_enterprise(
        industry=company.enterprise_industry or company.industry,
        employees=company.employee_count,
        revenue=company.annual_revenue,
        total_assets=company.total_assets,
    )
    return {
        "scale": result.scale,
        "industry": result.industry,
        "industry_label": result.industry_label,
        "basis": result.basis,
        "missing": result.missing,
        "is_sme": is_sme(result.scale),
        "source": "auto",
        "confirmed": False,
    }


def asset_values(kind: str, body: Any) -> dict[str, Any]:
    data = body.model_dump(exclude_none=True)
    data.pop("company_id", None)
    return data


async def upsert_asset(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    kind: str,
    company_id: UUID,
    values: dict[str, Any],
    source: str,
) -> Any:
    if kind not in ASSET_KINDS:
        raise NotFoundError()
    await get_company(session, tenant_id, company_id)
    model = ASSET_KINDS[kind]
    dedup_key = asset_dedup_key(kind, company_id, values)
    payload = {
        "tenant_id": tenant_id,
        "company_id": company_id,
        "dedup_key": dedup_key,
        "source": source,
        **values,
    }
    statement = (
        insert(model)
        .values(**payload)
        .on_conflict_do_update(
            index_elements=["tenant_id", "dedup_key"],
            set_={**values, "source": source},
        )
        .returning(model.id)
    )
    asset_id = (await session.execute(statement)).scalar_one()
    return await session.get(model, asset_id)


async def create_asset(
    session: AsyncSession, user: CurrentUser, kind: str, body: Any
) -> Any:
    if kind not in CREATE_SCHEMAS:
        raise NotFoundError()
    org_unit_id = getattr(body, "org_unit_id", None)
    if org_unit_id is not None:
        unit = await session.get(OrgUnit, org_unit_id)
        if unit is None or unit.tenant_id != user.tenant_id:
            raise ValidationAppError("部门不存在")
    if not can_write_org(user, org_unit_id):
        raise ForbiddenError("无权写入该部门资产")
    await get_company(session, user.tenant_id, body.company_id)
    return await upsert_asset(
        session,
        tenant_id=user.tenant_id,
        kind=kind,
        company_id=body.company_id,
        values=asset_values(kind, body),
        source="manual",
    )


async def list_assets(
    session: AsyncSession,
    user: CurrentUser,
    kind: str,
    company_id: UUID | None,
    q: str | None,
) -> list[Any]:
    if kind not in ASSET_KINDS:
        raise NotFoundError()
    model = ASSET_KINDS[kind]
    repo = AssetRepository(session, user.tenant_id, model)
    statement = repo.scoped(select(model)).where(visibility_filter(user, model))
    if company_id is not None:
        await get_company(session, user.tenant_id, company_id)
        statement = statement.where(model.company_id == company_id)
    if q:
        if kind == "performances":
            text_column = model.project_name
        elif kind == "credits":
            text_column = model.check_item
        else:
            text_column = model.name
        statement = statement.where(text_column.ilike(f"%{q}%"))
    return list((await session.execute(statement.order_by(model.created_at.desc()))).scalars().all())


async def update_asset(
    session: AsyncSession, user: CurrentUser, kind: str, asset_id: UUID, body: dict[str, Any]
) -> Any:
    if kind not in ASSET_KINDS:
        raise NotFoundError()
    model = ASSET_KINDS[kind]
    row = await AssetRepository(session, user.tenant_id, model).get_or_404(asset_id)
    if not can_read_org(user, row.org_unit_id):
        raise NotFoundError()
    if not can_write_org(user, row.org_unit_id):
        raise ForbiddenError("无权修改该部门资产")
    schema = CREATE_SCHEMAS[kind]
    payload = schema.model_validate(
        {
            **body,
            "company_id": row.company_id,
            "org_unit_id": body.get("org_unit_id", row.org_unit_id),
        }
    )
    if payload.org_unit_id is not None:
        unit = await session.get(OrgUnit, payload.org_unit_id)
        if unit is None or unit.tenant_id != user.tenant_id:
            raise ValidationAppError("部门不存在")
    if not can_write_org(user, payload.org_unit_id):
        raise ForbiddenError("无权写入目标部门资产")
    values = payload.model_dump(exclude_none=True)
    values.pop("company_id", None)
    for field, value in values.items():
        setattr(row, field, value)
    dedup_values = {
        field: getattr(row, field)
        for field in payload.model_dump()
        if field != "company_id"
    }
    row.dedup_key = asset_dedup_key(kind, row.company_id, dedup_values)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("同部门已存在相同资产") from exc
    await session.refresh(row)
    return row


async def delete_asset(
    session: AsyncSession, user: CurrentUser, kind: str, asset_id: UUID
) -> None:
    if kind not in ASSET_KINDS:
        raise NotFoundError()
    model = ASSET_KINDS[kind]
    row = await AssetRepository(session, user.tenant_id, model).get_or_404(asset_id)
    if not can_read_org(user, row.org_unit_id):
        raise NotFoundError()
    if not can_write_org(user, row.org_unit_id):
        raise ForbiddenError("无权删除该部门资产")
    await session.delete(row)
    await session.commit()


def to_asset_item(kind: str, row: Any) -> AssetItem:
    common = {
        "id": row.id,
        "company_id": row.company_id,
        "org_unit_id": getattr(row, "org_unit_id", None),
        "kind": kind,
        "source": row.source,
    }
    if kind == "qualifications":
        return AssetItem(
            **common,
            name=row.name,
            level=row.level,
            cert_no=row.cert_no,
            issuer=row.issuer,
            valid_from=row.valid_from,
            valid_to=row.valid_to,
        )
    if kind == "personnel":
        return AssetItem(
            **common,
            name=row.name,
            cert_type=row.cert_type,
            level=row.level,
            major=row.major,
            cert_no=row.cert_no,
            valid_to=row.valid_to,
        )
    if kind == "performances":
        return AssetItem(
            **common,
            name=row.project_name,
            project_name=row.project_name,
            amount=row.amount,
            complete_date=row.complete_date,
            buyer=row.buyer,
            category=row.category,
        )
    return AssetItem(
        **common,
        name=row.check_item,
        check_item=row.check_item,
        status=row.status,
        checked_at=row.checked_at,
    )


async def expiring_assets(
    session: AsyncSession, user: CurrentUser, days: int
) -> list[dict[str, Any]]:
    deadline = business_today() + timedelta(days=days)
    result: list[dict[str, Any]] = []
    for kind, model in (("qualifications", Qualification), ("personnel", Personnel)):
        repo = AssetRepository(session, user.tenant_id, model)
        rows: list[Any] = list(
            (
                await session.execute(
                    repo.scoped(
                        select(model).where(
                            model.valid_to.is_not(None),
                            model.valid_to <= deadline,
                            visibility_filter(user, model),
                        )
                    )
                )
            ).scalars().all()
        )
        kind_items = [
            {
                "id": row.id,
                "company_id": row.company_id,
                "kind": kind,
                "name": row.name,
                "valid_to": row.valid_to,
            }
            for row in rows
        ]
        result.extend(await apply_field_policies(session, user, kind, kind_items))
    result.sort(key=lambda item: item["valid_to"])
    return result
