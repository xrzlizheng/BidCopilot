import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import ConflictError, QuotaExceededError, ValidationAppError
from app.db.locks import advisory_lock
from app.db.session import SessionLocal
from app.modules.audit.service import AuditService
from app.modules.costs.service import query_hash, record_gateway_call
from app.modules.gateway.deps import get_keeprisk_adapter
from app.modules.gateway.keeprisk_adapter import KeepRiskTenderAdapter
from app.modules.notifications.models import Notification
from app.modules.notifications.service import NotificationService
from app.modules.opportunities.models import OpportunitySubscription, SourceSyncRun
from app.modules.opportunities.repository import SubscriptionRepository
from app.modules.opportunities.schemas import (
    OpportunitySubscriptionCreateRequest,
    OpportunitySubscriptionUpdateRequest,
)
from app.modules.opportunities.sync import OpportunitySyncService

logger = logging.getLogger("bidcopilot.subscriptions")

DEFAULT_INTERVAL_MINUTES = 60


def _lock_key(subscription_id: UUID) -> str:
    return f"opportunity-subscription:{subscription_id}"


@asynccontextmanager
async def subscription_lock(subscription_id: UUID):
    """订阅级付费调用互斥；机制与连接语义见 app.db.locks.advisory_lock。"""
    async with advisory_lock(_lock_key(subscription_id)) as acquired:
        yield acquired


async def owned_subscription(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID, subscription_id: UUID
) -> OpportunitySubscription:
    """订阅是用户级对象：列表按 user 过滤，写操作也必须同口径，否则同租户他人可改。"""
    subscription = (
        await session.execute(
            SubscriptionRepository(session, tenant_id)
            .scoped(select(OpportunitySubscription))
            .where(
                OpportunitySubscription.id == subscription_id,
                OpportunitySubscription.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if subscription is None:
        from app.core.errors import NotFoundError

        raise NotFoundError()
    return subscription


def _validate_interval(interval_minutes: int) -> None:
    minimum = get_settings().SYNC_MIN_INTERVAL_MINUTES
    if interval_minutes < minimum:
        raise ValidationAppError(f"interval_minutes 不得小于 {minimum} 分钟")


async def create_subscription(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    body: OpportunitySubscriptionCreateRequest,
) -> OpportunitySubscription:
    settings = get_settings()
    interval = body.interval_minutes or DEFAULT_INTERVAL_MINUTES
    _validate_interval(interval)
    # 上限检查与插入必须在同一事务里串行，否则并发创建可共同越过上限。
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"opportunity-subscription-cap:{tenant_id}"},
    )
    total = int(
        (
            await session.execute(
                select(func.count())
                .select_from(OpportunitySubscription)
                .where(OpportunitySubscription.tenant_id == tenant_id)
            )
        ).scalar_one()
    )
    if total >= settings.OPPORTUNITY_MAX_SUBSCRIPTIONS_PER_TENANT:
        raise ConflictError(f"每租户订阅数上限 {settings.OPPORTUNITY_MAX_SUBSCRIPTIONS_PER_TENANT}")
    name = body.name.strip()
    duplicate = (
        await session.execute(
            select(OpportunitySubscription.id).where(
                OpportunitySubscription.tenant_id == tenant_id,
                OpportunitySubscription.user_id == user_id,
                OpportunitySubscription.name == name,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError("订阅名称已存在")
    subscription = OpportunitySubscription(
        tenant_id=tenant_id,
        user_id=user_id,
        name=name,
        query_json=body.query.model_dump(mode="json"),
        interval_minutes=interval,
        enabled=True,
    )
    session.add(subscription)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("订阅名称已存在") from exc
    await session.refresh(subscription)
    await AuditService(session).append(
        tenant_id=tenant_id,
        actor_id=user_id,
        action="opportunity.subscription_created",
        resource_type="opportunity_subscription",
        resource_id=str(subscription.id),
        data={"name": subscription.name, "interval_minutes": subscription.interval_minutes},
    )
    await session.commit()
    return subscription


async def update_subscription(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    subscription_id: UUID,
    body: OpportunitySubscriptionUpdateRequest,
) -> OpportunitySubscription:
    subscription = await owned_subscription(
        session, tenant_id=tenant_id, user_id=user_id, subscription_id=subscription_id
    )
    if body.interval_minutes is not None:
        _validate_interval(body.interval_minutes)
        subscription.interval_minutes = body.interval_minutes
    if body.name is not None:
        subscription.name = body.name.strip()
    if body.enabled is not None:
        subscription.enabled = body.enabled
    if body.query is not None:
        subscription.query_json = body.query.model_dump(mode="json")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("订阅名称已存在") from exc
    await session.refresh(subscription)
    await AuditService(session).append(
        tenant_id=tenant_id,
        actor_id=user_id,
        action="opportunity.subscription_updated",
        resource_type="opportunity_subscription",
        resource_id=str(subscription.id),
        data={"enabled": subscription.enabled, "interval_minutes": subscription.interval_minutes},
    )
    await session.commit()
    return subscription


async def delete_subscription(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    subscription_id: UUID,
) -> None:
    subscription = await owned_subscription(
        session, tenant_id=tenant_id, user_id=user_id, subscription_id=subscription_id
    )
    await session.delete(subscription)
    await AuditService(session).append(
        tenant_id=tenant_id,
        actor_id=user_id,
        action="opportunity.subscription_deleted",
        resource_type="opportunity_subscription",
        resource_id=str(subscription.id),
        data={"name": subscription.name},
    )
    await session.commit()


async def list_subscriptions(
    session: AsyncSession, *, tenant_id: UUID, user_id: UUID
) -> list[OpportunitySubscription]:
    rows = (
        await session.execute(
            SubscriptionRepository(session, tenant_id)
            .scoped(select(OpportunitySubscription))
            .where(OpportunitySubscription.user_id == user_id)
            .order_by(OpportunitySubscription.created_at.desc())
        )
    ).scalars().all()
    return list(rows)


async def run_subscription(
    session: AsyncSession,
    adapter: KeepRiskTenderAdapter,
    subscription: OpportunitySubscription,
    *,
    actor_id: UUID | None = None,
) -> SourceSyncRun:
    actor = actor_id or subscription.user_id
    subscription_id = subscription.id
    if subscription.last_sync_run_id is not None:
        previous = await session.get(SourceSyncRun, subscription.last_sync_run_id)
        if previous is not None and previous.status in {"queued", "running"}:
            # 崩溃恢复：锁覆盖整个付费调用，未完成的 run 只能来自异常退出的进程。
            # 这里不再发起第二次付费请求，先把中断标出来。
            previous.status = "failed"
            previous.error_code = "sync_interrupted"
            previous.error_message = "previous run did not finish; not retried automatically"
            previous.finished_at = datetime.now(UTC)
            subscription.last_run_at = datetime.now(UTC)
            await session.commit()
            await _notify_subscription_result(session, subscription, previous)
            return previous

    run = SourceSyncRun(
        tenant_id=subscription.tenant_id,
        source="keeprisk:E_007_1271",
        pcode="E_007_1271",
        query_json=subscription.query_json,
        status="queued",
        created_by=actor,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)

    try:
        run = await OpportunitySyncService(session, adapter).run(run.id)
    except Exception as exc:
        run_id = run.id
        await session.rollback()
        # session.get 返回 Optional；先落到新变量再断言后回填，
        # 避免把 Optional 直接赋回已推断为非空的 run/subscription。
        failed_run = await session.get(SourceSyncRun, run_id)
        failed_subscription = await session.get(OpportunitySubscription, subscription_id)
        assert failed_run is not None and failed_subscription is not None
        run = failed_run
        subscription = failed_subscription
        run.status = "failed"
        run.error_code = (
            exc.code if isinstance(exc, QuotaExceededError) else "sync_exception"
        )
        run.error_message = str(exc)[:500]
        run.finished_at = datetime.now(UTC)
        await session.commit()

    # 先推进水位：否则后续台账/审计异常会让下一轮 tick 再抓一次已计费的请求。
    subscription.last_run_at = datetime.now(UTC)
    subscription.last_sync_run_id = run.id
    await session.commit()
    run_id = run.id

    try:
        await record_gateway_call(
            session,
            tenant_id=subscription.tenant_id,
            pcode="E_007_1271",
            operation="opportunity.list",
            status=run.source_status or run.status,
            order_no=run.gateway_order_no,
            charged=run.charged,
            rows=run.rows_fetched,
            query_hash_value=query_hash(run.query_json),
            resource_type="source_sync_run",
            resource_id=str(run.id),
            created_by=actor,
        )
        await AuditService(session).append(
            tenant_id=subscription.tenant_id,
            actor_id=actor,
            action="opportunity.sync_scheduled",
            resource_type="source_sync_run",
            resource_id=str(run.id),
            data={
                "status": run.status,
                "rows": run.rows_upserted,
                "rows_new": run.rows_new,
                "subscription_id": str(subscription.id),
            },
        )
        await session.commit()
    except Exception:
        # rollback 会过期会话内实例，之后再同步取属性（含日志里的 subscription.id）
        # 在 async 下抛 MissingGreenlet；用已捕获的主键重取，通知与返回值才不丢。
        await session.rollback()
        logger.exception("subscription %s bookkeeping failed", subscription_id)
        recovered_run = await session.get(SourceSyncRun, run_id)
        recovered_subscription = await session.get(OpportunitySubscription, subscription_id)
        assert recovered_run is not None and recovered_subscription is not None
        run = recovered_run
        subscription = recovered_subscription

    try:
        await _notify_subscription_result(session, subscription, run)
    except Exception:
        await session.rollback()
        logger.exception("subscription %s notification failed", subscription.id)

    # 订阅同步落库完成后立即触发自动匹配；失败不回滚已完成的同步与计费结果。
    if run.status == "succeeded":
        try:
            from app.modules.opportunities.auto_match import run_auto_match_for_tenant

            await run_auto_match_for_tenant(
                session,
                tenant_id=subscription.tenant_id,
                user_id=subscription.user_id,
                actor_id=actor,
                limit=50,
            )
        except Exception:
            await session.rollback()
            logger.exception("subscription %s auto match failed", subscription_id)
            refreshed = await session.get(SourceSyncRun, run_id)
            if refreshed is not None:
                run = refreshed
    return run


async def _notify_subscription_result(
    session: AsyncSession,
    subscription: OpportunitySubscription,
    run: SourceSyncRun,
) -> None:
    service = NotificationService(session)
    notification_type = "sync_new_rows" if run.status == "succeeded" else "sync_failed"
    # 按 run 幂等：重试/补记不会产生重复消息，失败的通知也可被下一轮补回。
    existing = (
        await session.execute(
            select(Notification.id).where(
                Notification.tenant_id == subscription.tenant_id,
                Notification.user_id == subscription.user_id,
                Notification.type == notification_type,
                Notification.resource_type == "source_sync_run",
                Notification.resource_id == str(run.id),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    if run.status == "succeeded":
        if run.rows_new > 0:
            await service.notify(
                tenant_id=subscription.tenant_id,
                user_id=subscription.user_id,
                notification_type=notification_type,
                title=f"订阅「{subscription.name}」新增 {run.rows_new} 条商机",
                body=f"抓取 {run.rows_fetched} 条，入库 {run.rows_upserted} 条，其中新增 {run.rows_new} 条。",
                resource_type="source_sync_run",
                resource_id=str(run.id),
            )
        return
    await service.notify(
        tenant_id=subscription.tenant_id,
        user_id=subscription.user_id,
        notification_type=notification_type,
        title=f"订阅「{subscription.name}」同步失败",
        body=(
            "同步执行异常，请查看服务端日志。"
            if run.error_code == "sync_exception"
            else f"{run.error_code or run.status}: {run.error_message or run.status}"
        ),
        resource_type="source_sync_run",
        resource_id=str(run.id),
    )


def _keeprisk_configured() -> bool:
    settings = get_settings()
    return bool(settings.KEEPRISK_API_BASE_URL and settings.KEEPRISK_APP_CODE)


async def due_subscription_ids(
    session: AsyncSession, *, now: datetime | None = None
) -> list[UUID]:
    # 调度器自身的跨租户扫描，不承载用户请求，因此不经 TenantRepository。
    now = now or datetime.now(UTC)
    rows = (
        await session.execute(
            select(OpportunitySubscription).where(OpportunitySubscription.enabled.is_(True))
        )
    ).scalars().all()
    due: list[UUID] = []
    for subscription in rows:
        if subscription.last_run_at is None:
            due.append(subscription.id)
            continue
        next_run = subscription.last_run_at + timedelta(minutes=subscription.interval_minutes)
        if next_run <= now:
            due.append(subscription.id)
    return due


async def run_due_subscriptions() -> int:
    if not _keeprisk_configured():
        logger.debug("keeprisk not configured; skipping subscription tick")
        return 0
    executed = 0
    async with SessionLocal() as session:
        ids = await due_subscription_ids(session)
    for subscription_id in ids:
        async with subscription_lock(subscription_id) as acquired:
            if not acquired:
                continue
            async with SessionLocal() as session:
                try:
                    subscription = await session.get(OpportunitySubscription, subscription_id)
                    if subscription is None or not subscription.enabled:
                        continue
                    now = datetime.now(UTC)
                    if subscription.last_run_at is not None and (
                        subscription.last_run_at + timedelta(minutes=subscription.interval_minutes)
                        > now
                    ):
                        # 获锁后复查：另一个进程（或手动执行）刚刚跑过，不再重复计费。
                        continue
                    await run_subscription(session, get_keeprisk_adapter(), subscription)
                    executed += 1
                except Exception:
                    logger.exception("subscription %s run failed", subscription_id)
    return executed
