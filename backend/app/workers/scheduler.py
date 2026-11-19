import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from app.config import get_settings
from app.db.locks import advisory_lock
from app.modules.opportunities.subscriptions import run_due_subscriptions

logger = logging.getLogger("bidcopilot.scheduler")

TICK_SECONDS = 60
JOB_ID = "opportunity-subscription-tick"
TICK_LOCK_KEY = "bidcopilot.scheduler.subscription-tick"
MILESTONE_LOCK_KEY = "bidcopilot.scheduler.milestone-reminder"
BID_REMINDER_LOCK_KEY = "bidcopilot.scheduler.bid-deadline-reminder"
PARSE_JOB_LOCK_KEY = "bidcopilot.scheduler.parse-job-recovery"
CONTRACT_EXPIRY_LOCK_KEY = "bidcopilot.scheduler.contract-expiry-reminder"
WATCHLIST_LOCK_KEY = "bidcopilot.scheduler.intel-watchlist"
AI_RUN_RECOVERY_LOCK_KEY = "bidcopilot.scheduler.ai-run-recovery"
WEBHOOK_RETRY_LOCK_KEY = "bidcopilot.scheduler.webhook-retry"


async def _tick() -> None:
    """多副本部署（K8s / SaaS）下每个实例都会构建调度器；执行锁保证同一时刻
    只有一个实例真正执行，未获锁的实例直接跳过本次，下一个周期重新竞争。
    """
    try:
        async with advisory_lock(TICK_LOCK_KEY) as acquired:
            if not acquired:
                return
            executed = await run_due_subscriptions()
            if executed:
                logger.info("subscription tick executed %s run(s)", executed)
    except Exception:
        logger.exception("subscription tick failed")


async def _milestone_reminder() -> None:
    """每日扫描到期履约节点 → 消息中心提醒。互斥语义同 _tick。"""
    from datetime import UTC, datetime

    from app.db.session import SessionLocal
    from app.modules.contracts.service import scan_due_milestones

    try:
        async with advisory_lock(MILESTONE_LOCK_KEY) as acquired:
            if not acquired:
                return
            async with SessionLocal() as session:
                sent = await scan_due_milestones(session, now=datetime.now(UTC))
            if sent:
                logger.info("contract milestone reminders sent: %s", sent)
    except Exception:
        logger.exception("contract milestone reminder failed")


async def _bid_deadline_reminder() -> None:
    """投标截止/答疑/保证金/开标/内部截止提醒；唯一键保证同一节点只发一次。"""
    from datetime import UTC, datetime

    from app.db.session import SessionLocal
    from app.modules.projects.reminders import scan_due_project_reminders

    try:
        async with advisory_lock(BID_REMINDER_LOCK_KEY) as acquired:
            if not acquired:
                return
            async with SessionLocal() as session:
                sent = await scan_due_project_reminders(session, now=datetime.now(UTC))
            if sent:
                logger.info("project deadline reminders sent: %s", sent)
    except Exception:
        logger.exception("project deadline reminder failed")


async def _parse_job_recovery() -> None:
    from app.modules.projects.service import recover_stale_parse_jobs

    try:
        async with advisory_lock(PARSE_JOB_LOCK_KEY) as acquired:
            if not acquired:
                return
            recovered = await recover_stale_parse_jobs()
            if recovered:
                logger.info("parse job recovery attempted %s job(s)", recovered)
    except Exception:
        logger.exception("parse job recovery failed")


async def _contract_expiry_reminder() -> None:
    """合同到期前 N 天提醒（N 由租户 TenantSetting 决定）；互斥语义同 _tick。"""
    from datetime import UTC, datetime

    from app.db.session import SessionLocal
    from app.modules.contracts.records_service import scan_contract_expiries

    try:
        async with advisory_lock(CONTRACT_EXPIRY_LOCK_KEY) as acquired:
            if not acquired:
                return
            async with SessionLocal() as session:
                sent = await scan_contract_expiries(session, now=datetime.now(UTC))
            if sent:
                logger.info("contract expiry reminders sent: %s", sent)
    except Exception:
        logger.exception("contract expiry reminder failed")


async def _ai_run_recovery() -> None:
    """清扫超时后永久 running 的生成/评标任务（无独立 worker 时的补偿）。"""
    from app.workers.recovery import recover_stale_ai_runs

    try:
        async with advisory_lock(AI_RUN_RECOVERY_LOCK_KEY) as acquired:
            if not acquired:
                return
            # 阈值统一走 AI_RUN_STALE_MINUTES，不在此处硬编码分钟数
            recovered = await recover_stale_ai_runs(get_settings().AI_RUN_STALE_MINUTES)
            if recovered:
                logger.info("stale ai runs recovered: %s", recovered)
    except Exception:
        logger.exception("ai run recovery failed")


async def _webhook_retry() -> None:
    """补投到期未完成的 webhook outbox 行（含进程重启遗留）。"""
    from app.modules.notifications.webhooks import process_due_webhook_deliveries

    try:
        async with advisory_lock(WEBHOOK_RETRY_LOCK_KEY) as acquired:
            if not acquired:
                return
            processed = await process_due_webhook_deliveries()
            if processed:
                logger.info("webhook outbox retried %s delivery(ies)", processed)
    except Exception:
        logger.exception("webhook retry failed")


async def _watchlist_check() -> None:
    from app.modules.company_intel.watchlist_service import run_due_watchlist

    try:
        async with advisory_lock(WATCHLIST_LOCK_KEY) as acquired:
            if not acquired:
                return
            executed = await run_due_watchlist()
            if executed:
                logger.info("intel watchlist checks executed: %s", executed)
    except Exception:
        logger.exception("intel watchlist check failed")


def build_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _tick,
        IntervalTrigger(seconds=TICK_SECONDS),
        id=JOB_ID,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _milestone_reminder,
        IntervalTrigger(hours=24),
        id="contract-milestone-reminder",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _bid_deadline_reminder,
        IntervalTrigger(hours=1),
        id="bid-deadline-reminder",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _contract_expiry_reminder,
        IntervalTrigger(hours=24),
        id="contract-expiry-reminder",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _parse_job_recovery,
        IntervalTrigger(minutes=5),
        id="parse-job-recovery",
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _ai_run_recovery,
        IntervalTrigger(minutes=10),
        id="ai-run-recovery",
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _webhook_retry,
        IntervalTrigger(seconds=60),
        id="webhook-outbox-retry",
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _watchlist_check,
        IntervalTrigger(hours=settings.INTEL_WATCHLIST_INTERVAL_HOURS),
        id="intel-watchlist-check",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    return scheduler
