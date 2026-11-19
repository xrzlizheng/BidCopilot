"""决策证据链单测：纯确定性聚合，不连库、不 Mock 外部数据。

覆盖：有/无中标记录、样本不足不编数、租户过滤写进 SQL、确定性输出。
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from app.modules.projects.decision_card import DEFAULT_WEIGHTS, DIMENSION_LABELS
from app.modules.projects.decision_evidence import (
    AWARD_SAMPLE_LIMIT,
    AWARD_WINDOW_DAYS,
    BUYER_MATCH_EXACT,
    BUYER_MATCH_FUZZY,
    BUYER_MATCH_NONE,
    MIN_BUDGET_ANCHOR_SAMPLES,
    AwardSample,
    build_award_count_query,
    build_award_query,
    build_budget_anchor,
    build_buyer_history,
    build_competition,
    build_our_win_rate,
    collect_decision_evidence,
    competition_signal,
    normalize_name_key,
)

TENANT = UUID("11111111-1111-1111-1111-111111111111")
OTHER_TENANT = UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
BUYER = "某市大数据局"


def _samples() -> list[AwardSample]:
    return [
        AwardSample("供应商甲", Decimal("1200000"), NOW - timedelta(days=30)),
        AwardSample("供应商乙", Decimal("900000"), NOW - timedelta(days=120)),
        AwardSample("供应商甲", Decimal("1000000"), NOW - timedelta(days=200)),
        AwardSample("供应商甲", None, NOW - timedelta(days=300)),
    ]


# ---------------------------------------------------------------------------
# 归一化与确定性
# ---------------------------------------------------------------------------


def test_normalize_name_key_merges_width_case_and_whitespace() -> None:
    assert normalize_name_key("某市 大数据局") == normalize_name_key("某市大数据局")
    assert normalize_name_key("ＡＢＣ") == normalize_name_key("abc")
    assert normalize_name_key("某市大数据局") == normalize_name_key("　某市大数据局　")
    assert normalize_name_key(None) == ""
    assert normalize_name_key("") == ""


def test_weights_stay_normalized_and_competition_label_exists() -> None:
    """DEFAULT_WEIGHTS 必须和为 100，且 competition 有中文标签。"""
    assert sum(DEFAULT_WEIGHTS.values()) == 100
    assert DEFAULT_WEIGHTS["competition"] > 0
    assert "competition" in DIMENSION_LABELS


# ---------------------------------------------------------------------------
# 采购人历史
# ---------------------------------------------------------------------------


def test_buyer_history_computes_median_and_latest_award() -> None:
    item = build_buyer_history(
        _samples(),
        tenant_id=TENANT,
        buyer=BUYER,
        buyer_key=normalize_name_key(BUYER),
        match_mode=BUYER_MATCH_EXACT,
        record_count=4,
        truncated=False,
    )
    assert item["scored"] is True
    assert item["scope"]["sources"] == ["award_records", "opportunities"]
    metrics = item["metrics"]
    assert metrics["record_count"] == 4
    assert metrics["amount_samples"] == 3
    assert metrics["median_win_amount"] == "1000000.00"
    assert metrics["p25_win_amount"] == "950000.00"
    assert metrics["p75_win_amount"] == "1100000.00"
    assert metrics["distinct_winners"] == 2
    # 最近中标时间是 30 天前那条
    assert metrics["latest_award_at"] == (NOW - timedelta(days=30)).isoformat()
    assert metrics["top_winners"][0]["winner"] == "供应商甲"
    assert metrics["top_winners"][0]["wins"] == 3


def test_buyer_history_without_buyer_is_explicitly_unscored() -> None:
    item = build_buyer_history(
        [],
        tenant_id=TENANT,
        buyer=None,
        buyer_key="",
        match_mode=BUYER_MATCH_NONE,
        record_count=0,
        truncated=False,
    )
    assert item["scored"] is False
    assert "未记录采购人" in item["reason"]
    assert item["metrics"]["record_count"] == 0


def test_buyer_history_insufficient_samples_does_not_fabricate_median() -> None:
    """样本不足时不许输出中位数——一条记录也能算中位数，但那不是统计。"""
    item = build_buyer_history(
        _samples()[:2],
        tenant_id=TENANT,
        buyer=BUYER,
        buyer_key=normalize_name_key(BUYER),
        match_mode=BUYER_MATCH_EXACT,
        record_count=2,
        truncated=False,
    )
    assert item["scored"] is False
    assert "样本不足" in item["reason"]
    assert "median_win_amount" not in item["metrics"]
    assert "mean_win_amount" not in item["metrics"]
    # 真实计数仍然如实给出，只是不参与评分
    assert item["metrics"]["record_count"] == 2
    assert item["metrics"]["amount_samples"] == 2


def test_buyer_history_fuzzy_match_is_hint_only() -> None:
    item = build_buyer_history(
        _samples(),
        tenant_id=TENANT,
        buyer=BUYER,
        buyer_key=normalize_name_key(BUYER),
        match_mode=BUYER_MATCH_FUZZY,
        record_count=4,
        truncated=False,
    )
    assert item["scored"] is False
    assert "模糊匹配" in item["reason"]
    assert item["hint"]["match_mode"] == BUYER_MATCH_FUZZY
    assert item["hint"]["note"] == "模糊匹配仅作提示，未计入评分"


def test_buyer_history_flags_truncation() -> None:
    samples = [AwardSample(f"供应商{i}", Decimal("100"), NOW - timedelta(days=1)) for i in range(3)]
    item = build_buyer_history(
        samples,
        tenant_id=TENANT,
        buyer=BUYER,
        buyer_key=normalize_name_key(BUYER),
        match_mode=BUYER_MATCH_EXACT,
        record_count=AWARD_SAMPLE_LIMIT + 5,
        truncated=True,
    )
    assert any("采样上限" in note for note in item["notes"])


# ---------------------------------------------------------------------------
# 竞争强度
# ---------------------------------------------------------------------------


def test_competition_counts_distinct_winners() -> None:
    item = build_competition(
        _samples(),
        tenant_id=TENANT,
        buyer=BUYER,
        match_mode=BUYER_MATCH_EXACT,
        record_count=4,
        buyer_intel=None,
    )
    assert item["scored"] is True
    assert item["metrics"]["distinct_winners"] == 2
    assert "2 家" in item["reason"]


def test_competition_insufficient_samples_is_unscored() -> None:
    item = build_competition(
        _samples()[:1],
        tenant_id=TENANT,
        buyer=BUYER,
        match_mode=BUYER_MATCH_EXACT,
        record_count=1,
        buyer_intel=None,
    )
    assert item["scored"] is False
    assert "样本不足" in item["reason"]


def test_competition_single_winner_is_hint_only() -> None:
    """3 条记录全是同一家：在位供应商信号，但没有竞争格局可评。"""
    samples = [
        AwardSample("在位供应商", Decimal("1000000"), NOW - timedelta(days=10)),
        AwardSample("在位供应商", Decimal("1100000"), NOW - timedelta(days=200)),
        AwardSample("在位供应商", Decimal("900000"), NOW - timedelta(days=400)),
    ]
    item = build_competition(
        samples,
        tenant_id=TENANT,
        buyer=BUYER,
        match_mode=BUYER_MATCH_EXACT,
        record_count=3,
        buyer_intel=None,
    )
    assert item["scored"] is False
    assert "同一家供应商" in item["reason"]
    assert item["hint"]["distinct_winners"] == 1


def test_competition_passes_through_buyer_intel_status() -> None:
    intel = {
        "status": "unauthorized",
        "recent_wins_12m": 0,
        "source_table": "opportunity_buyer_intel",
    }
    item = build_competition(
        _samples(),
        tenant_id=TENANT,
        buyer=BUYER,
        match_mode=BUYER_MATCH_EXACT,
        record_count=4,
        buyer_intel=intel,
    )
    # 非 ok 状态原样透传，不降级成 0 家和「无竞争」
    assert item["metrics"]["buyer_intel"]["status"] == "unauthorized"
    assert "opportunity_buyer_intel" in item["scope"]["sources"]


def test_competition_not_scored_on_fuzzy_match() -> None:
    item = build_competition(
        _samples(),
        tenant_id=TENANT,
        buyer=BUYER,
        match_mode=BUYER_MATCH_FUZZY,
        record_count=4,
        buyer_intel=None,
    )
    assert item["scored"] is False


# ---------------------------------------------------------------------------
# 我方胜率
# ---------------------------------------------------------------------------


def test_our_win_rate_uses_only_decided_projects() -> None:
    item = build_our_win_rate(
        tenant_id=TENANT, won=6, lost=2, buyer=BUYER, scope_label="同采购人"
    )
    assert item["scored"] is True
    assert item["metrics"]["decided"] == 8
    assert item["metrics"]["win_rate"] == 0.75
    assert "75.0%" in item["reason"]


def test_our_win_rate_insufficient_samples_is_unscored() -> None:
    item = build_our_win_rate(
        tenant_id=TENANT, won=1, lost=1, buyer=BUYER, scope_label="同采购人"
    )
    assert item["scored"] is False
    assert "样本不足" in item["reason"]
    assert "win_rate" not in item["metrics"]


# ---------------------------------------------------------------------------
# 预算锚点
# ---------------------------------------------------------------------------


def test_budget_anchor_compares_budget_with_median_win_amount() -> None:
    item = build_budget_anchor(
        tenant_id=TENANT,
        buyer=BUYER,
        budget=Decimal("1500000"),
        budget_basis="tender_constraints",
        budget_label="项目预算",
        samples=_samples(),
    )
    assert item["scored"] is True
    assert item["metrics"]["median_win_amount"] == "1000000.00"
    assert item["metrics"]["budget_to_median_ratio"] == "1.5000"
    assert "P75" in item["metrics"]["position"]
    assert "tender_constraints" in item["scope"]["sources"]


def test_budget_anchor_without_budget_is_unscored() -> None:
    item = build_budget_anchor(
        tenant_id=TENANT,
        buyer=BUYER,
        budget=None,
        budget_basis=None,
        budget_label=None,
        samples=_samples(),
    )
    assert item["scored"] is False
    assert "未取到本项目预算" in item["reason"]


def test_budget_anchor_insufficient_award_samples_is_unscored() -> None:
    item = build_budget_anchor(
        tenant_id=TENANT,
        buyer=BUYER,
        budget=Decimal("1500000"),
        budget_basis="tender_constraints",
        budget_label="项目预算",
        samples=_samples()[: MIN_BUDGET_ANCHOR_SAMPLES - 1],
    )
    assert item["scored"] is False
    assert "样本不足" in item["reason"]
    assert "median_win_amount" not in item["metrics"]


# ---------------------------------------------------------------------------
# 维度信号
# ---------------------------------------------------------------------------


def _items(**overrides: dict) -> dict[str, dict]:
    base = {
        "buyer_history": build_buyer_history(
            _samples(),
            tenant_id=TENANT,
            buyer=BUYER,
            buyer_key=normalize_name_key(BUYER),
            match_mode=BUYER_MATCH_EXACT,
            record_count=4,
            truncated=False,
        ),
        "competition": build_competition(
            _samples(),
            tenant_id=TENANT,
            buyer=BUYER,
            match_mode=BUYER_MATCH_EXACT,
            record_count=4,
            buyer_intel=None,
        ),
        "our_win_rate": build_our_win_rate(
            tenant_id=TENANT, won=6, lost=2, buyer=BUYER, scope_label="同采购人"
        ),
        "budget_anchor": build_budget_anchor(
            tenant_id=TENANT,
            buyer=BUYER,
            budget=Decimal("1500000"),
            budget_basis="tender_constraints",
            budget_label="项目预算",
            samples=_samples(),
        ),
    }
    base.update(overrides)
    return base


def test_competition_signal_blends_win_rate_and_stays_deterministic() -> None:
    first = competition_signal(_items())
    second = competition_signal(_items())
    assert first == second, "同样输入必须给出同样输出"
    assert first["scored"] is True
    assert first["source"] == "award-records"
    # 2 家供应商 -> base=100-12=88；胜率 75% -> 88*0.6 + 75*0.4 = 82.8
    assert first["score"] == 82.8
    assert "启发式排序，非中标概率预测" in first["reason"]


def test_competition_signal_without_win_rate_notes_the_gap() -> None:
    signal = competition_signal(
        _items(
            our_win_rate=build_our_win_rate(
                tenant_id=TENANT, won=1, lost=0, buyer=BUYER, scope_label="同采购人"
            )
        )
    )
    assert signal["scored"] is True
    assert "胜率未参与加权" in signal["reason"]


def test_competition_signal_unscored_when_samples_insufficient() -> None:
    signal = competition_signal(
        _items(
            competition=build_competition(
                [],
                tenant_id=TENANT,
                buyer=BUYER,
                match_mode=BUYER_MATCH_EXACT,
                record_count=0,
                buyer_intel=None,
            )
        )
    )
    assert signal["scored"] is False
    assert signal["score"] is None
    assert signal["reason"]


# ---------------------------------------------------------------------------
# SQL 构造：租户过滤不能漏
# ---------------------------------------------------------------------------


def _compiled(statement) -> str:
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def _tenant_literal(tenant_id: UUID) -> str:
    """PG UUID 绑定时渲染成无连字符的 32 位十六进制。"""
    return str(tenant_id).replace("-", "")


def test_award_queries_filter_both_tables_by_tenant() -> None:
    since = NOW - timedelta(days=AWARD_WINDOW_DAYS)
    for statement in (
        build_award_query(
            TENANT, buyer_key=normalize_name_key(BUYER), match_mode=BUYER_MATCH_EXACT, since=since
        ),
        build_award_count_query(
            TENANT, buyer_key=normalize_name_key(BUYER), match_mode=BUYER_MATCH_EXACT, since=since
        ),
    ):
        sql = _compiled(statement)
        assert "award_records.tenant_id" in sql
        assert "opportunities.tenant_id" in sql
        assert _tenant_literal(TENANT) in sql
        assert "award_records.award_at >=" in sql


def test_award_query_without_buyer_does_not_scan_all_tenant_awards() -> None:
    """没有采购人时不能退化成「全租户中标记录」当历史价。"""
    sql = _compiled(
        build_award_query(
            TENANT,
            buyer_key=None,
            match_mode=BUYER_MATCH_NONE,
            since=NOW - timedelta(days=AWARD_WINDOW_DAYS),
        )
    )
    assert "regexp_replace" in sql
    assert "= ''" in sql


def test_award_query_fuzzy_uses_contains_not_equality() -> None:
    sql = _compiled(
        build_award_query(
            TENANT,
            buyer_key=normalize_name_key(BUYER),
            match_mode=BUYER_MATCH_FUZZY,
            since=NOW - timedelta(days=AWARD_WINDOW_DAYS),
        )
    )
    assert "LIKE" in sql.upper()
    assert "award_records.tenant_id" in sql


# ---------------------------------------------------------------------------
# 聚合入口：不编数 + 租户隔离
# ---------------------------------------------------------------------------


class _NoResult:
    """最小假 session：只验证「没有数据时不许编数」，不接触真实数据库。"""

    def __init__(self) -> None:
        self.statements: list[object] = []

    async def execute(self, statement, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        self.statements.append(statement)

        class _Result:
            def scalar_one(self_inner):  # noqa: N805
                return 0

            def scalar_one_or_none(self_inner):  # noqa: N805
                return None

            def all(self_inner):  # noqa: N805
                return []

            def scalars(self_inner):  # noqa: N805
                return self_inner

        return _Result()


async def test_collect_evidence_rejects_foreign_project_object() -> None:
    """防御：传入的 project 对象租户不符时直接拒绝，不复用。"""
    from types import SimpleNamespace

    from app.modules.projects.models import BidProject

    project = BidProject(
        tenant_id=OTHER_TENANT, name="别人的项目", status="active"
    )
    project.id = uuid4()
    try:
        await collect_decision_evidence(
            _NoResult(),
            tenant_id=TENANT,
            project_id=project.id,
            project=project,
        )
    except AssertionError as exc:
        assert "tenant mismatch" in str(exc)
    else:  # pragma: no cover - 断言失败路径
        raise AssertionError("跨租户 project 对象必须被拒绝")
    _ = SimpleNamespace  # 保持导入清晰
