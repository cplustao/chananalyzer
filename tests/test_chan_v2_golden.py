from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.chan_core.engine import ChanBar, PureChanEngine
from backend.app.db.models import Bar, Instrument

PROJECT_ROOT = Path(__file__).resolve().parents[1]
V2_DATABASE = PROJECT_ROOT / "data" / "chan_v2.db"
GOLDEN_PATH = Path(__file__).parent / "golden" / "chan_structures.json"


def _assert_subset(actual: dict, expected: dict) -> None:
    direction = {"向上": "up", "向下": "down"}
    actual = {**actual, "dir": direction.get(actual.get("dir"), actual.get("dir"))}
    for key, value in expected.items():
        if isinstance(value, float):
            assert actual[key] == pytest.approx(value, abs=1e-8)
        else:
            assert actual[key] == value


@pytest.mark.skipif(not V2_DATABASE.exists(), reason="v2 migrated database is required")
def test_v2_bars_match_protected_chan_golden_samples():
    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    begin = datetime.fromisoformat(golden["range"]["begin"])
    end = datetime.fromisoformat(golden["range"]["end"])
    engine = create_engine(f"sqlite:///{V2_DATABASE.as_posix()}")

    with Session(engine) as session:
        for code, expected in golden["samples"].items():
            instrument = session.scalar(select(Instrument).where(Instrument.code == code))
            assert instrument is not None
            rows = session.scalars(
                select(Bar)
                .where(
                    Bar.instrument_id == instrument.id,
                    Bar.timeframe == "DAY",
                    Bar.adjustment == "QFQ",
                    Bar.bar_time >= begin,
                    Bar.bar_time <= end,
                )
                .order_by(Bar.bar_time)
            ).all()
            bars = [
                ChanBar(
                    bar_time=row.bar_time,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                    amount=row.amount,
                    turnover_rate=row.turnover_rate,
                )
                for row in rows
            ]
            result = PureChanEngine().analyze_bars(code, bars)

            assert result["kline_count"] == expected["kline_count"]
            assert len(result["bi_list"]) == expected["bi_count"]
            assert len(result["seg_list"]) == expected["seg_count"]
            assert len(result["zs_list"]) == expected["zs_count"]
            assert len(result["buy_signals"]) + len(result["sell_signals"]) == expected["bsp_count"]
            _assert_subset(result["bi_list"][-1], expected["last_bi"])
            _assert_subset(result["seg_list"][-1], expected["last_seg"])
            if expected["last_zs"] is None:
                assert result["zs_list"] == []
            else:
                _assert_subset(result["zs_list"][-1], expected["last_zs"])
