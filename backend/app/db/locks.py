import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text

from app.db.session import engine

logger = logging.getLogger("bidcopilot.locks")


@asynccontextmanager
async def advisory_lock(key: str) -> AsyncIterator[bool]:
    """会话级 advisory lock，专用连接持锁贯穿临界区。

    不能用 ORM session 的多次 commit 承载：commit 会把连接归还池，后续
    SQL 可能换连接，锁与连接解绑。这里显式从 engine 取一条连接，整个
    临界区结束后再归还。产出 True 表示获锁成功；False 表示已被他人持有。

    连接使用 AUTOCOMMIT：临界区可能长达数分钟（外部 HTTP/全表扫描），
    不能让池化连接长期停在 idle in transaction。
    """
    async with engine.connect() as connection:
        connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtext(:key))"),
            {"key": key},
        )
        if not locked:
            yield False
            return
        try:
            yield True
        finally:
            try:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": key},
                )
            except Exception:
                # 解锁失败时立即关闭连接：服务端 session 结束会释放 advisory lock，
                # 绝不能把带锁连接归还连接池（否则该 job 会永久静默停摆）。
                logger.warning("advisory unlock failed; closing connection", exc_info=True)
                await connection.close()
