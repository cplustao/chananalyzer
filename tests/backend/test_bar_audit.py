from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.app.db.models import Instrument
from backend.app.providers.market_data import MarketBar
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.services.bar_audit import confirm_with_other_source, find_beyond_limit_jumps, price_limit

START = date(2026, 6, 1)


def series(session, code, closes, name=None, exchange="SZSE"):
    instrument = Instrument(code=code, ts_code=f"{code}.SZ", exchange=exchange, name=name or code, status="active")
    session.add(instrument)
    session.flush()
    repository = IngestionRepository(session)
    source = repository.data_source("baostock", "baostock")
    bars = [MarketBar(datetime.combine(START + timedelta(days=i), datetime.min.time()), c, c, c, c, 100, 1000)
            for i, c in enumerate(closes)]
    repository.upsert_bars(instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ",
                           data_source_id=source.id, bars=bars)
    return instrument


def test_limits_by_board():
    assert price_limit("600000", "SH") == 0.10
    assert price_limit("300750", "SZ") == 0.20
    assert price_limit("688501", "SH") == 0.20
    assert price_limit("920001", "BJ") == 0.30


def test_only_moves_beyond_the_limit_after_the_first_sessions_are_suspects(session):
    # An old ex-rights base: the history before day 8 is 1.4x too high.
    series(session, "000001", [14.0] * 8 + [10.0] * 4)
    series(session, "000002", [10.0] * 8 + [11.0] * 4)          # a 10% limit-up is fine
    series(session, "300001", [10.0] * 8 + [11.9] * 4)          # 19% on ChiNext is fine
    series(session, "000003", [10.0, 20.0] + [20.0] * 10)       # listing days have no limit
    series(session, "000004", [3.0] * 8 + [0.3] * 4, name="某某退")  # first delisting-period day
    series(session, "000005", [0.28] * 8 + [0.31] * 4)          # one tick on a cheap stock
    session.commit()

    jumps = find_beyond_limit_jumps(session, since=START)

    assert [(jump.code, jump.day) for jump in jumps] == [("000001", START + timedelta(days=8))]
    assert jumps[0].change_pct == -28.57


def test_a_jump_the_other_source_also_shows_is_real(session):
    series(session, "000001", [14.0] * 8 + [10.0] * 4)
    series(session, "000002", [10.0] * 8 + [5.0] * 4)
    session.commit()
    jumps = find_beyond_limit_jumps(session, since=START)
    other = {
        "000001": {START + timedelta(days=i): 10.0 for i in range(12)},  # flat: the stored base is wrong
        "000002": {START + timedelta(days=i): 10.0 if i < 8 else 5.0 for i in range(12)},  # real halving
    }

    broken = confirm_with_other_source(jumps, lambda code, _start, _end: other[code])

    assert [(jump.code, jump.other_change_pct) for jump in broken] == [("000001", 0.0)]


def test_unreachable_other_source_confirms_nothing(session):
    series(session, "000001", [14.0] * 8 + [10.0] * 4)
    session.commit()

    assert confirm_with_other_source(find_beyond_limit_jumps(session, since=START), lambda *_: {}) == []


def test_gaps_the_other_source_also_lacks_become_suspension_bars(session):
    from backend.app.db.models import Bar, TradingCalendar
    from backend.app.services.bar_access import analysis_series_summary
    from backend.app.services.bar_audit import fill_confirmed_suspensions, series_gaps

    days = [START + timedelta(days=i) for i in range(10)]
    for day in days:
        session.add(TradingCalendar(exchange="CN", trade_date=day, is_open=True))
    instrument = Instrument(code="920058", ts_code="920058.BJ", exchange="BJ", name="华洋赛车", status="active")
    session.add(instrument)
    session.flush()
    repository = IngestionRepository(session)
    source = repository.data_source("tushare", "tushare")
    saved = [day for i, day in enumerate(days) if i not in (3, 4, 6)]
    repository.upsert_bars(
        instrument_id=instrument.id, timeframe="DAY", adjustment="NONE", data_source_id=source.id,
        bars=[MarketBar(datetime.combine(day, datetime.min.time()), 10 + i, 11 + i, 9 + i, 10 + i, 100, 1000)
              for i, day in enumerate(saved)],
    )
    session.commit()

    gaps = series_gaps(session, instrument, end=days[-1])
    assert gaps == [days[3], days[4], days[6]]
    # The other source traded on day 6, so only days 3 and 4 are confirmed suspensions.
    written = fill_confirmed_suspensions(session, instrument, gaps, {day for day in days if day not in (days[3], days[4])})
    session.commit()

    assert written == [days[3], days[4]]
    marker = session.query(Bar).filter(Bar.instrument_id == instrument.id, Bar.bar_time == datetime.combine(days[3], datetime.min.time())).one()
    assert (marker.open, marker.close, marker.volume, marker.trade_status, marker.data_source_id) == (12.0, 12.0, 0.0, "suspended", source.id)
    assert series_gaps(session, instrument, end=days[-1]) == [days[6]]
    # Suspension bars are not analysis input.
    assert analysis_series_summary(session, instrument.id)[0] == 7


def test_known_source_errors_cover_the_bad_bar_and_the_rebound_after_it():
    from backend.app.services.bar_audit import Jump, is_known_source_error

    assert is_known_source_error(Jump(1, "000981", "", date(2022, 2, 25), -42.9))
    assert is_known_source_error(Jump(1, "000981", "", date(2022, 2, 28), 58.3))
    assert not is_known_source_error(Jump(1, "000981", "", date(2026, 2, 25), -42.9))
    assert not is_known_source_error(Jump(2, "688501", "", date(2022, 2, 25), -27.1))
