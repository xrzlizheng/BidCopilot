from decimal import Decimal

from app.modules.pricing.price_band import (
    build_price_band,
    estimate_win_rate,
    in_scope,
    percentile,
)

D = Decimal

AWARD_RATIOS = [D("0.80"), D("0.85"), D("0.88"), D("0.90"), D("0.95")]


def test_percentile_uses_linear_interpolation() -> None:
    values = [D("1"), D("2"), D("3"), D("4"), D("5")]
    assert percentile(values, D("0.25")) == D("2")
    assert percentile(values, D("0.50")) == D("3")
    assert percentile(values, D("0.75")) == D("4")
    assert percentile([], D("0.5")) is None


def test_price_band_insufficient_samples_returns_explicit_insufficient_data() -> None:
    band = build_price_band(
        award_ratios=AWARD_RATIOS[:4],
        decided_outcomes=["won", "lost", "won", "lost", "won", "lost", "won", "lost"],
        price_loss_count=2,
    )
    assert band.status == "insufficient_data"
    assert band.sample_count == 4
    assert band.p25 is None and band.p50 is None and band.p75 is None
    assert band.missing
    assert "样本不足" in band.missing[0]


def test_price_band_outputs_p25_p50_p75_and_win_rate_basis() -> None:
    band = build_price_band(
        award_ratios=AWARD_RATIOS,
        decided_outcomes=["won"] * 6 + ["lost"] * 3,
        price_loss_count=2,
        samples_by_source={"bid_result_won": 3, "award_record": 2},
    )
    assert band.status == "ok"
    assert band.p25 == D("0.85")
    assert band.p50 == D("0.88")
    assert band.p75 == D("0.90")
    assert band.base_win_rate == D("0.6666666666666666666666666667")
    assert band.price_loss_count == 2
    assert band.samples_by_source == {"bid_result_won": 3, "award_record": 2}


def test_price_band_without_enough_decided_samples_does_not_fake_win_rate() -> None:
    band = build_price_band(
        award_ratios=AWARD_RATIOS,
        decided_outcomes=["won", "lost"],
        price_loss_count=0,
    )
    assert band.status == "ok"
    assert band.base_win_rate is None
    assert any("已决标样本不足" in note for note in band.notes)


def test_lower_quote_gets_higher_heuristic_win_rate_and_disclaimer() -> None:
    band = build_price_band(
        award_ratios=AWARD_RATIOS,
        decided_outcomes=["won"] * 6 + ["lost"] * 3,
        price_loss_count=1,
    )
    lower = estimate_win_rate(quote_ratio=D("0.78"), band=band)
    middle = estimate_win_rate(quote_ratio=D("0.88"), band=band)
    higher = estimate_win_rate(quote_ratio=D("0.98"), band=band)

    assert lower.status == middle.status == higher.status == "ok"
    assert lower.rate is not None and middle.rate is not None and higher.rate is not None
    assert lower.rate > middle.rate > higher.rate
    assert lower.heuristic is True
    assert "非模型预测" in lower.disclaimer
    assert "非中标承诺" in lower.disclaimer


def test_win_rate_is_none_when_band_insufficient() -> None:
    band = build_price_band(
        award_ratios=AWARD_RATIOS[:2],
        decided_outcomes=["won"] * 8,
        price_loss_count=0,
    )
    estimate = estimate_win_rate(quote_ratio=D("0.8"), band=band)
    assert estimate.status == "insufficient_data"
    assert estimate.rate is None


def test_in_scope_requires_industry_overlap_and_budget_band() -> None:
    assert in_scope(
        target_industries=["软件和信息技术服务业"],
        sample_industries=["软件和信息技术服务业", "政务"],
        target_budget=D("1000000"),
        sample_budget=D("700000"),
    )
    assert in_scope(
        target_industries=["软件和信息技术服务业"],
        sample_industries=["软件和信息技术服务业"],
        target_budget=D("1000000"),
        sample_budget=D("1400000"),
    )
    assert not in_scope(
        target_industries=["软件和信息技术服务业"],
        sample_industries=["建筑工程"],
        target_budget=D("1000000"),
        sample_budget=D("1000000"),
    )
    assert not in_scope(
        target_industries=["软件和信息技术服务业"],
        sample_industries=["软件和信息技术服务业"],
        target_budget=D("1000000"),
        sample_budget=D("1400001"),
    )
