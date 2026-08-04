from pathlib import Path

import pandas as pd

from backend.app.domain.limit_up_scoring import (
    aggregate_weekly,
    risk_metrics,
    score_daily_main_wave,
    score_weekly_main_wave,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_BARS = PROJECT_ROOT / "tests" / "golden" / "limit_up_000997_20260724.csv"


def test_limit_up_scoring_matches_protected_000997_baseline():
    daily = pd.read_csv(GOLDEN_BARS, parse_dates=["date"]).set_index("date")

    day = score_daily_main_wave(daily.copy())
    week = score_weekly_main_wave(aggregate_weekly(daily.copy()))
    risk = risk_metrics(daily.copy(), day["chan_support"])

    assert day["score"] == 20.8
    assert sum(rule["hit"] for rule in day["rules"]) == 5
    assert week["score"] == 10.0
    assert sum(rule["hit"] for rule in week["rules"]) == 1
    assert risk["score"] == 73.1
    assert risk["plan"]["stop_price"] == 14.97
