import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.config import get_settings
from app.db.session import SessionLocal
from app.modules.evaluation.models import BidEvaluation
from app.modules.generation.models import GenerationRun

logger = logging.getLogger("bidcopilot.workers.recovery")


async def recover_stale_ai_runs(minutes: int | None = None) -> int:
    """把超时/进程重启后永久 running 的生成与评标任务标记为 failed。

    这两类任务只在进程内同步执行；没有该清扫时，前端会一直显示「进行中」且
    用户重试会被并发保护挡住。

    阈值默认取 `AI_RUN_STALE_MINUTES`（分钟，可用环境变量覆盖）。
    假设：`GenerationRun` / `BidEvaluation` 均无 updated_at/心跳列（只有
    created_at 与 started_at），因此按 created_at 判定 stale；若未来增加心跳，
    应改用心跳时间，阈值语义随之变为「多久没有进度」。
    """
    settings = get_settings()
    stale_minutes = settings.AI_RUN_STALE_MINUTES if minutes is None else minutes
    now = datetime.now(UTC)
    cutoff = now - timedelta(minutes=stale_minutes)
    async with SessionLocal() as session:
        evaluations = (
            await session.execute(
                update(BidEvaluation)
                .where(BidEvaluation.status == "running", BidEvaluation.created_at < cutoff)
                .values(
                    status="failed",
                    error_code="stale_timeout",
                    error_message="任务超时或进程重启，已标记失败，请重新发起",
                    finished_at=now,
                )
                .returning(BidEvaluation.id)
                .execution_options(synchronize_session=False)
            )
        ).all()
        generations = (
            await session.execute(
                update(GenerationRun)
                .where(GenerationRun.status == "running", GenerationRun.created_at < cutoff)
                .values(
                    status="failed",
                    error_code="stale_timeout",
                    error_message="任务超时或进程重启，已标记失败，请重新发起",
                    finished_at=now,
                )
                .returning(GenerationRun.id)
                .execution_options(synchronize_session=False)
            )
        ).all()
        await session.commit()
    recovered = len(evaluations) + len(generations)
    if recovered:
        logger.info("stale ai runs recovered: %s", recovered)
    return recovered
