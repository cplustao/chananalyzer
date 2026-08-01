import numpy as np
import pandas as pd

from backend.app.domain.limit_up_scoring import (
    aggregate_weekly,
    classify_score,
    risk_metrics,
    score_daily_main_wave,
    score_weekly_main_wave,
    weighted_score,
)


def make_trend(periods=250):
    index = pd.bdate_range("2025-01-01", periods=periods)
    close = np.linspace(10, 28, periods)
    frame = pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.02,
            "low": close * 0.98,
            "close": close,
            "volume": np.linspace(1_000_000, 2_000_000, periods),
            "amount": np.linspace(50_000_000, 150_000_000, periods),
            "turnover_rate": np.full(periods, 8.0),
        },
        index=index,
    )
    frame.loc[index[-1], "close"] *= 1.03
    frame.loc[index[-1], "high"] = frame.loc[index[-1], "close"]
    frame.loc[index[-1], "volume"] *= 1.5
    return frame


def test_main_wave_scores_are_bounded_and_explainable():
    daily = make_trend()
    day = score_daily_main_wave(daily)
    week = score_weekly_main_wave(aggregate_weekly(daily))
    assert 0 <= day["score"] <= 100
    assert 0 <= week["score"] <= 100
    assert sum(rule["max"] for rule in day["rules"]) == 100
    assert sum(rule["max"] for rule in week["rules"]) == 100


def test_insufficient_samples_are_not_zero_scores():
    daily = make_trend(80)
    assert score_daily_main_wave(daily)["score"] is None
    assert score_weekly_main_wave(aggregate_weekly(daily))["score"] is None


def test_score_classifications():
    assert classify_score(80) == "强主升"
    assert classify_score(65) == "主升候选"
    assert classify_score(50) == "转强观察"
    assert classify_score(49.9) == "非主升"
    assert classify_score(None) == "样本不足"


def test_missing_components_are_rescaled_not_zeroed():
    result = weighted_score([
        ("available", 60, 0.5, {}),
        ("missing", 40, None, {}),
    ])
    assert result["score"] == 50
    assert result["coverage"] == 0.6


def test_risk_plan_respects_budget_and_targets():
    result = risk_metrics(make_trend(), None)
    plan = result["plan"]
    assert plan["position_pct"] <= plan["position_cap_pct"]
    assert plan["stop_price"] < plan["reference_price"] < plan["target_2r"] < plan["target_3r"]