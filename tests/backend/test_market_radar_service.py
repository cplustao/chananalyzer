from datetime import date, datetime, timedelta

from sqlalchemy import select

from backend.app.db.models import Bar, Industry, Instrument, RadarSnapshot, TradingCalendar
from backend.app.services.market_radar import ALGORITHM_VERSION, MarketRadarService

END = date(2026, 7, 24)


def weekdays(end: date, count: int) -> list[date]:
    days, day = [], end
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day -= timedelta(days=1)
    return days[::-1]


def stock(session, code, closes, *, industry="软件", exchange=None, adjustment="QFQ", volumes=None, highs=None, name=None):
    sector = session.scalar(select(Industry).where(Industry.name == industry))
    if sector is None:
        sector = Industry(name=industry)
        session.add(sector)
        session.flush()
    instrument = Instrument(code=code, ts_code=f"{code}.SZ", exchange=exchange, status="active", industry_id=sector.id,
                            name=name or code)
    session.add(instrument)
    session.flush()
    days = weekdays(END, len(closes))
    for index, (day, close) in enumerate(zip(days, closes, strict=True)):
        session.add(Bar(
            instrument_id=instrument.id, timeframe="DAY", adjustment=adjustment,
            bar_time=datetime.combine(day, datetime.min.time()), open=close, low=close * 0.98,
            high=(highs or {}).get(index, close * 1.01), close=close,
            volume=(volumes or {}).get(index, 1000), amount=100000,
        ))
    return instrument


def test_radar_is_computed_from_bars_alone(session):
    flat = [10.0] * 70
    # Two limit-up closes in a row at the high: a 2-board streak.
    streak = [10.0] * 68 + [11.0, 12.1]
    stock(session, "000001", streak, highs={68: 11.0, 69: 12.1})
    stock(session, "000002", flat, industry="银行")
    # Suspended today: present, but not part of the day's moves.
    stock(session, "000003", flat, volumes={69: 0})
    session.commit()

    snapshot = MarketRadarService(session).refresh()

    assert snapshot["trade_date"] == END.isoformat()
    assert snapshot["algorithm_version"] == ALGORITHM_VERSION == "2.2"
    assert snapshot["coverage"]["current_count"] == 3
    assert snapshot["breadth"]["advancers"] + snapshot["breadth"]["decliners"] + snapshot["breadth"]["flat"] == 2
    ecology = snapshot["limit_ecology"]
    assert (ecology["limit_up_count"], ecology["highest_board"], ecology["multi_board_count"]) == (1, 2, 1)
    assert ecology["industries"][0]["name"] == "软件"
    assert ecology["previous_limit_up_count"] == 1
    trend = snapshot["components"][0]["metrics"]
    assert trend["above_ma60_rate"] == 1.0
    assert "above_ma60_rate" in snapshot["breadth"]
    row = session.scalar(select(RadarSnapshot).where(RadarSnapshot.algorithm_version == ALGORITHM_VERSION))
    assert row is not None and row.score == snapshot["regime"]["score"]


def test_a_near_limit_close_below_the_high_is_not_a_limit_up(session):
    # +9.9% but closed off the high: not sealed at the limit.
    stock(session, "000001", [10.0] * 69 + [10.99], highs={69: 11.0})
    session.commit()

    assert MarketRadarService(session).refresh()["limit_ecology"]["limit_up_count"] == 0


def test_market_radar_includes_bj_raw_series_and_discloses_methodology(session):
    stock(session, "920001", [10 + index * 0.01 for index in range(21)], exchange="BJ", adjustment="NONE")
    session.commit()

    snapshot = MarketRadarService(session).refresh()

    assert snapshot["coverage"]["current_count"] == 1
    assert snapshot["methodology"]["bar_adjustments"] == {"QFQ": 0, "NONE": 1}
    assert snapshot["methodology"]["trend_benchmark"] == "equal_weight_proxy"


def test_backfill_computes_every_open_session_from_the_same_bars(session):
    stock(session, "000001", [10 + index * 0.05 for index in range(80)])
    stock(session, "000002", [14 - index * 0.05 for index in range(80)])
    for day in weekdays(END, 30):
        session.add(TradingCalendar(exchange="CN", trade_date=day, is_open=True))
    session.commit()
    start = weekdays(END, 10)[0]

    computed = MarketRadarService(session).backfill(start, END)

    assert computed == weekdays(END, 10)
    rows = session.scalars(select(RadarSnapshot).where(RadarSnapshot.algorithm_version == ALGORITHM_VERSION)).all()
    assert len(rows) == 10
    # A day computed in the backfill matches a direct refresh of that day.
    direct = MarketRadarService(session).refresh(END)
    assert next(row for row in rows if row.trade_date == END).score == direct["regime"]["score"]
