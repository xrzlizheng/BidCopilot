from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.modules.audit.service import AuditService
from app.modules.compliance.personal_resources_service import transfer_personal_resource
from app.modules.compliance.schemas import PersonalResourceMetadata, PersonalResourceTransfer
from app.modules.identity.deps import require_permission
from app.modules.identity.models import User
from app.modules.identity.schemas import CurrentUser
from app.modules.knowledge.models import KnowledgeBase, KnowledgeDocument, KnowledgePack

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


@router.get("/personal-resources", response_model=list[PersonalResourceMetadata])
async def get_personal_resources(
    user: CurrentUser = Depends(require_permission("audit:read")),
    session: AsyncSession = Depends(get_session),
) -> list[PersonalResourceMetadata]:
    kb_rows = (
        await session.execute(
            select(KnowledgeBase, User.email)
            .outerjoin(User, User.id == KnowledgeBase.owner_id)
            .where(
                KnowledgeBase.tenant_id == user.tenant_id,
                KnowledgeBase.scope == "personal",
            )
            .order_by(KnowledgeBase.created_at.desc())
        )
    ).all()
    kb_ids = [row[0].id for row in kb_rows]
    counts: dict = {}
    if kb_ids:
        counts = {
            kb_id: (doc_count, size_bytes)
            for kb_id, doc_count, size_bytes in (
                await session.execute(
                    select(
                        KnowledgeDocument.kb_id,
                        func.count(),
                        func.coalesce(func.sum(KnowledgeDocument.size_bytes), 0),
                    )
                    .where(KnowledgeDocument.kb_id.in_(kb_ids))
                    .group_by(KnowledgeDocument.kb_id)
                )
            ).all()
        }

    pack_rows = (
        await session.execute(
            select(KnowledgePack, User.email)
            .outerjoin(User, User.id == KnowledgePack.owner_id)
            .where(
                KnowledgePack.tenant_id == user.tenant_id,
                KnowledgePack.scope == "personal",
            )
            .order_by(KnowledgePack.created_at.desc())
        )
    ).all()

    result = [
        PersonalResourceMetadata(
            resource_type="knowledge_base",
            id=kb.id,
            name=kb.name,
            owner_id=kb.owner_id,
            owner_email=email,
            classification=kb.classification,
            status=kb.status,
            owner_status=kb.owner_status,
            created_at=kb.created_at,
            document_count=counts.get(kb.id, (0, 0))[0],
            size_bytes=int(counts.get(kb.id, (0, 0))[1]),
        )
        for kb, email in kb_rows
    ]
    result.extend(
        PersonalResourceMetadata(
            resource_type="knowledge_pack",
            id=pack.id,
            name=pack.name,
            owner_id=pack.owner_id,
            owner_email=email,
            classification=pack.classification,
            status=pack.status,
            owner_status=pack.owner_status,
            created_at=pack.created_at,
            version=pack.version,
        )
        for pack, email in pack_rows
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="compliance.personal_resource.metadata_view",
        resource_type="personal_resource",
        resource_id="list",
        data={"count": len(result)},
    )
    await session.commit()
    return result


@router.post("/personal-resources/{resource_type}/{resource_id}/transfer")
async def transfer_resource(
    resource_type: str,
    resource_id: UUID,
    body: PersonalResourceTransfer,
    user: CurrentUser = Depends(require_permission("user:manage")),
    session: AsyncSession = Depends(get_session),
) -> dict:
    resource, target_id = await transfer_personal_resource(
        session,
        actor=user,
        resource_type=resource_type,
        resource_id=resource_id,
        target_user_id=body.target_user_id,
    )
    await AuditService(session).append(
        tenant_id=user.tenant_id,
        actor_id=user.id,
        action="compliance.personal_resource.transfer",
        resource_type=resource_type,
        resource_id=str(resource_id),
        data={"target_user_id": str(target_id)},
    )
    await session.commit()
    return {
        "resource_type": resource_type,
        "resource_id": str(resource_id),
        "target_user_id": str(target_id),
        "owner_status": resource.owner_status,
    }
