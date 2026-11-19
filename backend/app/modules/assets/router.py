from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationAppError
from app.db.session import get_session
from app.modules.assets.field_policies import (
    apply_field_policies,
    delete_policy,
    list_policies,
    upsert_policy,
)
from app.modules.assets.importer import MAX_FILE_BYTES, import_assets
from app.modules.assets.schemas import (
    AssetItem,
    CompanyCreate,
    CompanyResponse,
    CompanyScaleResponse,
    CompanyUpdate,
    FieldPolicyResponse,
    FieldPolicyUpsert,
    ImportResult,
)
from app.modules.assets.service import (
    CREATE_SCHEMAS,
    company_scale,
    create_asset,
    create_company,
    delete_asset,
    expiring_assets,
    get_company,
    list_assets,
    list_companies,
    scale_change_impact,
    scale_history,
    to_asset_item,
    update_asset,
    update_company,
)
from app.modules.audit.service import AuditService
from app.modules.identity.deps import require_permission, require_permission_or_org
from app.modules.identity.schemas import CurrentUser

router = APIRouter(prefix="/api/v1", tags=["assets"])


async def _audit(
    session: AsyncSession,
    user: CurrentUser,
    action: str,
    resource_type: str,
    resource_id: str,
    data: dict,
) -> None:
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        data=data,
    )
    await session.commit()


@router.get("/companies", response_model=list[CompanyResponse])
async def get_companies(
    q: str | None = None,
    user: CurrentUser = Depends(require_permission("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> list[CompanyResponse]:
    companies = await list_companies(session, user.tenant_id, q)
    return [CompanyResponse.model_validate(company) for company in companies]


@router.get("/asset-field-policies", response_model=list[FieldPolicyResponse])
async def get_field_policies(
    user: CurrentUser = Depends(require_permission("asset:write")),
    session: AsyncSession = Depends(get_session),
) -> list:
    return await list_policies(session, user.tenant_id)


@router.put("/asset-field-policies", response_model=FieldPolicyResponse)
async def put_field_policy(
    body: FieldPolicyUpsert,
    user: CurrentUser = Depends(require_permission("asset:write")),
    session: AsyncSession = Depends(get_session),
) -> object:
    row = await upsert_policy(
        session, tenant_id=user.tenant_id, body=body, user_id=user.id
    )
    await _audit(
        session,
        user,
        "asset.field_policy.upsert",
        "field_policy",
        str(row.id),
        {
            "resource_type": row.resource_type,
            "field_name": row.field_name,
            "min_clearance": row.min_clearance,
            "mask_strategy": row.mask_strategy,
        },
    )
    return row


@router.delete("/asset-field-policies/{policy_id}", status_code=204)
async def remove_field_policy(
    policy_id: UUID,
    user: CurrentUser = Depends(require_permission("asset:write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await delete_policy(session, user.tenant_id, policy_id)
    await _audit(
        session, user, "asset.field_policy.delete", "field_policy", str(policy_id), {}
    )


@router.post("/companies", response_model=CompanyResponse, status_code=201)
async def post_company(
    body: CompanyCreate,
    user: CurrentUser = Depends(require_permission("asset:write")),
    session: AsyncSession = Depends(get_session),
) -> CompanyResponse:
    company = await create_company(session, user.tenant_id, body)
    await _audit(session, user, "asset.company.create", "company", str(company.id), {"name": company.name})
    return CompanyResponse.model_validate(company)


@router.patch("/companies/{company_id}", response_model=CompanyResponse)
async def patch_company(
    company_id: UUID,
    body: CompanyUpdate,
    user: CurrentUser = Depends(require_permission("asset:write")),
    session: AsyncSession = Depends(get_session),
) -> CompanyResponse:
    # 记录变更前的划型，用于判断是否跨越中小企业分界线
    before = await get_company(session, user.tenant_id, company_id)
    previous_scale = before.enterprise_scale
    company = await update_company(session, user.tenant_id, company_id, body)
    payload: dict[str, object] = {}
    if previous_scale != company.enterprise_scale:
        payload["scale_change"] = {
            "from": previous_scale,
            "to": company.enterprise_scale,
        }
    await _audit(
        session, user, "asset.company.update", "company", str(company.id), payload
    )
    return CompanyResponse.model_validate(company)


@router.get("/companies/{company_id}/scale-history")
async def get_scale_history(
    company_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    """划型变更历史（取自审计事件，带哈希链）。"""
    return await scale_history(session, user.tenant_id, company_id, limit)


@router.get("/companies/{company_id}/scale-impact")
async def get_scale_impact(
    company_id: UUID,
    previous_scale: str | None = Query(default=None),
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """查询划型变化对在投项目的影响。

    单独一个端点而不是塞进 PATCH 响应，是为了不破坏既有调用方；
    前端保存成功后拿旧值来查，即可给出「N 个在投项目需复核」的提示。
    """
    return await scale_change_impact(
        session, user.tenant_id, company_id, previous_scale, user
    )


@router.get("/companies/{company_id}/scale", response_model=CompanyScaleResponse)
async def get_company_scale(
    company_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> CompanyScaleResponse:
    """查询企业划型结果。

    返回依据与缺失项，而不是一个孤零零的「小型」——用户需要能核对，因为
    「专门面向中小企业」的项目里声明函填错即废标。
    """
    company = await get_company(session, user.tenant_id, company_id)
    return CompanyScaleResponse(**company_scale(company))


@router.get("/assets/expiring")
async def get_expiring(
    days: int = Query(default=90, ge=1, le=3650),
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    items = await expiring_assets(session, user, days)
    return {"items": items, "total": len(items)}


@router.get("/assets/{kind}")
async def get_assets(
    kind: str,
    company_id: UUID | None = None,
    q: str | None = None,
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    rows = await list_assets(session, user, kind, company_id, q)
    items = [to_asset_item(kind, row).model_dump() for row in rows]
    items = await apply_field_policies(session, user, kind, items)
    return {
        "items": items,
        "total": len(items),
    }


@router.post("/assets/import", response_model=ImportResult)
async def import_asset_file(
    kind: str = Form(...),
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_permission("asset:import")),
    session: AsyncSession = Depends(get_session),
) -> ImportResult:
    content = await file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise ValidationAppError("导入文件超过 10MB 上限")
    result = await import_assets(
        session,
        tenant_id=user.tenant_id,
        kind=kind,
        filename=file.filename or "",
        content=content,
    )
    await _audit(
        session,
        user,
        "asset.import",
        kind,
        "bulk",
        {"imported": result.imported, "failed": result.failed},
    )
    return result


@router.post("/assets/{kind}", response_model=AssetItem, status_code=201)
async def post_asset(
    kind: str,
    body: dict,
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    schema = CREATE_SCHEMAS.get(kind)
    if schema is None:
        raise NotFoundError()
    payload = schema.model_validate(body)
    row = await create_asset(session, user, kind, payload)
    await _audit(session, user, "asset.create", kind, str(row.id), {"company_id": str(row.company_id)})
    item = to_asset_item(kind, row).model_dump()
    # 脱敏后是 dict，由 response_model=AssetItem 负责校验序列化
    return (await apply_field_policies(session, user, kind, [item]))[0]


@router.patch("/assets/{kind}/{asset_id}", response_model=AssetItem)
async def patch_asset(
    kind: str,
    asset_id: UUID,
    body: dict,
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await update_asset(session, user, kind, asset_id, body)
    await _audit(session, user, "asset.update", kind, str(row.id), {})
    item = to_asset_item(kind, row).model_dump()
    # 脱敏后是 dict，由 response_model=AssetItem 负责校验序列化
    return (await apply_field_policies(session, user, kind, [item]))[0]


@router.delete("/assets/{kind}/{asset_id}", status_code=204)
async def remove_asset(
    kind: str,
    asset_id: UUID,
    user: CurrentUser = Depends(require_permission_or_org("asset:read")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await delete_asset(session, user, kind, asset_id)
    await _audit(session, user, "asset.delete", kind, str(asset_id), {})
