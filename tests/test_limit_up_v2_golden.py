from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.db.models import Bar, Instrument
from backend.app.domain.limit_up_scoring import (
    aggregate_weekly,
    risk_metrics,
    score_daily_main_wave,
    score_weekly_main_wave,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_DATABASE = PROJECT_ROOT / "data" / "chan_v2.db"
BASELINE_AS_OF = datetime(2026, 7, 24)


@pytest.mark.skipif(not V2_DATABASE.exists(), reason="v2 migrated database is required")
def test_limit_up_scoring_matches_protected_000997_baseline():
    engine = create_engine(f"sqlite:///{V2_DATABASE.as_posix()}")
    with Session(engine) as session:
        instrument = session.scalar(select(Instrument).where(Instrument.code == "000997"))
        rows = list(
            reversed(
                list(
                    session.scalars(
                        select(Bar)
                        .where(
                            Bar.instrument_id == instrument.id,
                            Bar.timeframe == "DAY",
                            Bar.adjustment == "QFQ",
                            Bar.bar_time <= BASELINE_AS_OF,
                        )
                        .order_by(Bar.bar_time.desc())
                        .limit(250)
                    ).all()
                )
            )
        )
    daily = pd.DataFrame(
        [
            {
                "date": row.bar_time,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "amount": row.amount or 0,
                "turnover_rate": row.turnover_rate,
            }
            for row in rows
        ]
    ).set_index("date")

    day = score_daily_main_wave(daily.copy())
    week = score_weekly_main_wave(aggregate_weekly(daily.copy()))
    risk = risk_metrics(daily.copy(), day["chan_support"])

    assert day["score"] == 29.8
    assert sum(rule["hit"] for rule in day["rules"]) == 7
    assert week["score"] == 10.0
    assert sum(rule["hit"] for rule in week["rules"]) == 1
    assert risk["score"] == 68.1
    assert risk["plan"]["stop_price"] == 14.97
