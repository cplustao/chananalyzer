from datetime import datetime, timedelta

from sqlalchemy import select

from backend.app.db.models import Bar, Instrument, LimitUpEvent, RadarSnapshot
from backend.app.services.market_radar import ALGORITHM_VERSION, MarketRadarService


def test_market_radar_is_calculated_and_persisted_from_v2_tables(session):
    end = datetime(2026, 7, 24)
    for number, direction in ((1, 1), (2, -1)):
        instrument = Instrument(code=f"91000{number}", ts_code=f"91000{number}.SZ", status="active")
        session.add(instrument)
        session.flush()
        for offset in range(21):
            close = 10 + direction * (20 - offset) * 0.05
            session.add(
                Bar(
                    instrument_id=instrument.id,
                    timeframe="DAY",
                    adjustment="QFQ",
                    bar_time=end - timedelta(days=offset),
                    open=close - 0.02,
                    high=close + 0.1,
                    low=close - 0.1,
                    close=close,
                    volume=1000,
                    amount=100000 + offset,
                )
            )
    session.add(
        LimitUpEvent(
            instrument_id=None,
            legacy_code="919999",
            trade_date=end.date(),
            consecutive_boards=2,
            raw_payload={"industry": "软件", "amount": 100000000},
        )
    )
    session.commit()

    snapshot = MarketRadarService(session).refresh()

    assert snapshot["trade_date"] == "2026-07-24"
    assert snapshot["coverage"]["current_count"] == 2
    assert snapshot["limit_ecology"]["limit_up_count"] == 1
    assert snapshot["limit_ecology"]["industry_count"] == 1
    assert snapshot["limit_ecology"]["industries"][0]["name"] == "软件"
    row = session.scalar(
        select(RadarSnapshot).where(RadarSnapshot.algorithm_version == ALGORITHM_VERSION)
    )
    assert row is not None
    assert row.snapshot["source"] == "v2 日线 + v2 涨停事件"


def test_market_radar_rejects_stale_limit_up_events(session):
    instrument = Instrument(code="910003", ts_code="910003.SZ", status="active")
    session.add(instrument)
    session.flush()
    session.add(
        Bar(
            instrument_id=instrument.id,
            timeframe="DAY",
            adjustment="QFQ",
            bar_time=datetime(2026, 7, 29),
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=1000,
            amount=100000,
        )
    )
    session.add(
        LimitUpEvent(
            legacy_code="919998",
            trade_date=datetime(2026, 7, 28).date(),
            consecutive_boards=1,
        )
    )
    session.commit()

    try:
        MarketRadarService(session).refresh()
    except ValueError as exc:
        assert "落后于行情" in str(exc)
    else:
        raise AssertionError("stale limit-up events must block radar refresh")

def test_market_radar_includes_bj_raw_series_and_discloses_methodology(session):
    end = datetime(2026, 7, 29)
    instrument = Instrument(
        code="920001",
        ts_code="920001.BJ",
        exchange="BJ",
        status="active",
    )
    session.add(instrument)
    session.flush()
    for offset in range(21):
        session.add(
            Bar(
                instrument_id=instrument.id,
                timeframe="DAY",
                adjustment="NONE",
                bar_time=end - timedelta(days=offset),
                open=10,
                high=10.2,
                low=9.8,
                close=10 + (20 - offset) * 0.01,
                volume=1000,
                amount=50000,
            )
        )
    session.add(
        LimitUpEvent(
            legacy_code="920001",
            trade_date=end.date(),
            consecutive_boards=1,
        )
    )
    session.commit()

    snapshot = MarketRadarService(session).refresh()

    assert snapshot["coverage"]["current_count"] == 1
    assert snapshot["methodology"]["bar_adjustments"] == {"QFQ": 0, "NONE": 1}
    assert snapshot["methodology"]["trend_benchmark"] == "equal_weight_proxy"
