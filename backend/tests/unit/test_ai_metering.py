"""AI 计量单元测试：字数口径、按项目聚合、租户隔离、状态原样保留。

不依赖 PostgreSQL：用 aiosqlite 内存库建最小表集（租户/项目/台账），
把 `costs/metering.py` 的写读闭环真正跑一遍。生产链路（生成/评标）落库
在 `tests/integration/test_ai_usage_api.py` 用真实 API + 容器内 PG 验证。

产品只面向企业投标部门（单一投标主体），多客户分摊能力已下线，因此这里
只校验项目维度与租户维度隔离。
"""

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

pytest.importorskip("aiosqlite")

from app.db.base import Base  # noqa: E402
from app.modules.costs.metering import (  # noqa: E402
    count_words,
    record_ai_usage,
    usage_by_project,
)
from app.modules.costs.metering_models import AiUsageLedger  # noqa: E402
from app.modules.identity.models import Tenant  # noqa: E402
from app.modules.projects.models import BidProject  # noqa: E402


@compiles(PGUUID, "sqlite")
def _uuid_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN201
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN201
    return "JSON"


def _import_all_models() -> None:
    """SQLite 建表只需目标表存在，但 FK 解析要求被引用表已在 metadata 中。"""
    import app.modules.assets.models  # noqa: F401
    import app.modules.identity.models  # noqa: F401
    import app.modules.opportunities.models  # noqa: F401


@pytest.fixture
async def session():
    _import_all_models()
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        Tenant.__table__,
        BidProject.__table__,
        AiUsageLedger.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables)
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        yield db_session
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.drop_all(sync_connection, tables=tables)
        )
    await engine.dispose()


async def _seed(session) -> tuple[Tenant, BidProject]:
    tenant = Tenant(name="计量测试租户", slug=f"meter-{uuid4().hex[:8]}", kind="customer")
    session.add(tenant)
    await session.flush()
    project = BidProject(tenant_id=tenant.id, name="计量测试项目", decision_json={})
    session.add(project)
    await session.commit()
    return tenant, project


def test_count_words_pure_chinese_counts_each_char() -> None:
    assert count_words("投标文件") == 4


def test_count_words_pure_english_counts_whitespace_tokens() -> None:
    assert count_words("bid  document generation") == 3


def test_count_words_mixed_chinese_and_english() -> None:
    # 中文 4 字 + 英文/数字 2 个 token（ABC、123）= 6
    assert count_words("技术方案 ABC 123") == 6
    # 中英紧贴（无空格）时英文仍按一个 token 计
    assert count_words("投标ABC") == 3


def test_count_words_empty_and_whitespace_are_zero() -> None:
    assert count_words("") == 0
    assert count_words("   \n\t ") == 0


def test_count_words_counts_digits_as_words() -> None:
    assert count_words("2026 10 31") == 3


async def test_record_then_usage_by_project(session) -> None:
    tenant, project = await _seed(session)

    await record_ai_usage(
        session,
        tenant_id=tenant.id,
        project_id=project.id,
        operation="generation",
        piece_count=2,
        word_count=120,
        model="test-model",
    )
    await record_ai_usage(
        session,
        tenant_id=tenant.id,
        project_id=project.id,
        operation="evaluation",
        piece_count=1,
        word_count=30,
        model="judge-model",
    )

    assert await usage_by_project(session, tenant.id, project.id) == {
        "pieces": 3,
        "words": 150,
    }


async def test_failed_status_is_preserved_and_costs_nothing(session) -> None:
    tenant, project = await _seed(session)

    ok_entry = await record_ai_usage(
        session,
        tenant_id=tenant.id,
        project_id=project.id,
        operation="generation",
        piece_count=1,
        word_count=50,
    )
    failed_entry = await record_ai_usage(
        session,
        tenant_id=tenant.id,
        project_id=project.id,
        operation="generation",
        status="failed",
        piece_count=0,
        word_count=0,
    )
    partial_entry = await record_ai_usage(
        session,
        tenant_id=tenant.id,
        project_id=project.id,
        operation="generation",
        status="partial",
        piece_count=0,
        word_count=0,
    )

    rows = await session.execute(select(AiUsageLedger))
    entries = {entry.id: entry for entry in rows.scalars().all()}
    assert entries[ok_entry.id].status == "ok"
    # 失败/部分失败不能折算成 0 篇：状态与 0 消耗都原样保留，便于对账
    assert entries[failed_entry.id].status == "failed"
    assert entries[failed_entry.id].piece_count == 0
    assert entries[partial_entry.id].status == "partial"
    assert entries[partial_entry.id].piece_count == 0
    # 台账里三条都算总量，但只有成功那条贡献篇数
    assert await usage_by_project(session, tenant.id, project.id) == {
        "pieces": 1,
        "words": 50,
    }


async def test_usage_is_isolated_per_tenant(session) -> None:
    tenant, project = await _seed(session)
    other = Tenant(name="另一个租户", slug=f"meter-{uuid4().hex[:8]}", kind="customer")
    session.add(other)
    await session.flush()
    other_project = BidProject(tenant_id=other.id, name="别人的项目", decision_json={})
    session.add(other_project)
    await session.commit()

    await record_ai_usage(
        session,
        tenant_id=tenant.id,
        project_id=project.id,
        operation="generation",
        piece_count=5,
        word_count=500,
    )

    # 换个租户查同一个项目 ID：租户过滤必须让它看起来「没有用量」
    assert await usage_by_project(session, other.id, project.id) == {"pieces": 0, "words": 0}
    # 同一租户下的其他项目也不能串号
    assert await usage_by_project(session, tenant.id, other_project.id) == {
        "pieces": 0,
        "words": 0,
    }
