from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.app.db.models import Instrument, Job, RadarSnapshot, ScanResult, TradingCalendar
from backend.app.providers.market_data import MarketBar
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.services import signal_outcomes as signal_outcomes_module
from backend.app.services.signal_outcomes import SignalOutcomeService
from backend.app.signal_outcomes_cli import render

KNOWN = date(2026, 11, 2)  # Monday; T+5 is 11-09, T+10 is 11-16
T5, T10 = date(2026, 11, 9), date(2026, 11, 16)


def calendar(session, start=date(2026, 10, 1), end=date(2026, 11, 30)):
    day = start
    while day <= end:
        session.add(TradingCalendar(exchange="CN", trade_date=day, is_open=day.weekday() < 5))
        day += timedelta(days=1)
    session.flush()


def stock(session, code):
    instrument = Instrument(code=code, ts_code=f"{code}.SZ", exchange="SZSE", name=code, status="active")
    session.add(instrument)
    session.flush()
    return instrument


def closes(session, instrument, prices, source="baostock"):
    repository = IngestionRepository(session)
    data_source = repository.data_source(source, source)
    repository.upsert_bars(
        instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ", data_source_id=data_source.id,
        bars=[MarketBar(datetime.combine(day, datetime.min.time()), price, price, price, price, 100, 1000)
              for day, price in prices.items()],
    )


def scan(session, *, data_as_of=KNOWN, created=datetime(2026, 11, 2, 10), kind="screen.smart"):
    job = Job(kind=kind, status="completed", payload={"scan_side": "buy"},
              result={"data_as_of": data_as_of.isoformat() if data_as_of else None},
              created_at=created, finished_at=created)
    session.add(job)
    session.flush()
    return job


def hit(session, job, instrument, signals):
    session.add(ScanResult(job_id=job.id, instrument_id=instrument.id, scan_kind=job.kind,
                           signal_type=signals[-1]["type"], payload={"code": instrument.code, "signals": signals}))


def buy(day, kind="2"):
    return {"type": kind, "is_buy": True, "date": day, "price": 1.0}


def market(session):
    """Three stocks; the market median over T+5 is +2%."""
    calendar(session)
    signal, flat, weak = stock(session, "000001"), stock(session, "000002"), stock(session, "000003")
    closes(session, signal, {KNOWN: 10.0, T5: 11.0, T10: 12.0})
    closes(session, flat, {KNOWN: 10.0, T5: 10.2, T10: 10.0})
    closes(session, weak, {KNOWN: 10.0, T5: 9.9, T10: 9.0})
    return signal, flat, weak


def overall(report, horizon):
    return report["groups"]["overall"][0]["horizons"][str(horizon)]


def test_signal_is_entered_on_the_scan_date_and_compared_with_the_market_median(session, monkeypatch):
    monkeypatch.setattr(signal_outcomes_module, "MIN_SAMPLE", 1)
    monkeypatch.setattr(signal_outcomes_module, "MIN_ENTRY_DAYS", 1)
    signal, _flat, _weak = market(session)
    # Found on the scan of 11-02 although the buy point is dated 10-29.
    hit(session, scan(session), signal, [buy("2026-10-29")])
    session.commit()

    report = SignalOutcomeService(session).build()

    assert report["data_through"] == "2026-11-16"
    assert report["signals"] == 1
    t5 = overall(report, 5)
    assert round(t5["median_return"], 4) == 0.10
    assert round(t5["median_excess"], 4) == 0.08
    assert t5["beat_market_rate"] == 1.0
    assert overall(report, 20) == {"measured": 0, "entry_days": 0, "pending": 1, "unusable": 0, "sufficient": False}
    assert report["groups"]["freshness"][0]["group"] == "近 3 个交易日内"


def test_small_samples_show_counts_only(session):
    signal, _flat, _weak = market(session)
    hit(session, scan(session), signal, [buy("2026-11-02")])
    session.commit()

    report = SignalOutcomeService(session).build()

    assert overall(report, 5) == {"measured": 1, "entry_days": 1, "pending": 0, "unusable": 0, "sufficient": False}
    assert "样本不足（n=1，入场日 1）" in render(report)


def test_same_signal_in_several_scans_counts_once_from_the_earliest_scan(session):
    signal, _flat, _weak = market(session)
    later = scan(session, data_as_of=T5, created=datetime(2026, 11, 9, 10), kind="screen.hot")
    hit(session, later, signal, [buy("2026-10-29")])
    hit(session, scan(session), signal, [buy("2026-10-29")])
    session.commit()

    report = SignalOutcomeService(session).build()

    assert report["signals"] == 1
    assert report["groups"]["known_date"][0]["group"] == "2026-11-02"
    assert overall(report, 5)["measured"] == 1


def test_sell_signals_and_signals_after_the_scan_date_are_ignored(session):
    signal, flat, _weak = market(session)
    job = scan(session)
    hit(session, job, signal, [{"type": "2", "is_buy": False, "date": "2026-11-02", "price": 1.0}])
    hit(session, job, flat, [buy("2026-11-03")])
    session.commit()

    assert SignalOutcomeService(session).build()["signals"] == 0


def test_prices_from_two_sources_are_not_compared(session):
    signal, _flat, _weak = market(session)
    # The exit bar was later rewritten by another source with its own adjustment base.
    closes(session, signal, {T5: 30.0}, source="akshare")
    hit(session, scan(session), signal, [buy("2026-11-02")])
    session.commit()

    assert overall(SignalOutcomeService(session).build(), 5)["unusable"] == 1


def test_old_scan_without_a_data_date_uses_the_last_session_before_it_ran(session):
    signal, _flat, _weak = market(session)
    # Finished on Tuesday 00:30 Beijing time, before that day settles, so its data ends on Monday 11-02.
    hit(session, scan(session, data_as_of=None, created=datetime(2026, 11, 2, 16, 30)), signal, [buy("2026-10-30")])
    session.commit()

    report = SignalOutcomeService(session).build()

    assert report["groups"]["known_date"][0]["group"] == "2026-11-02"


def test_radar_states_report_the_market_median_after_them(session, monkeypatch):
    monkeypatch.setattr(signal_outcomes_module, "MIN_SAMPLE", 1)
    monkeypatch.setattr(signal_outcomes_module, "MIN_ENTRY_DAYS", 1)
    market(session)
    session.add_all([
        RadarSnapshot(trade_date=KNOWN, algorithm_version="2.1", score=57, status="neutral", status_label="震荡",
                      snapshot={}, calculated_at=datetime(2026, 11, 2, 10), coverage_rate=1.0),
        RadarSnapshot(trade_date=T5, algorithm_version="2.1", score=70, status="bull", status_label="进攻",
                      snapshot={}, calculated_at=datetime(2026, 11, 13, 10), coverage_rate=1.0),
    ])
    session.commit()

    radar = {row["status_label"]: row for row in SignalOutcomeService(session).build()["radar"]}

    assert round(radar["震荡"]["horizons"]["5"]["mean_market_median"], 4) == 0.02
    assert radar["震荡"]["backfilled_days"] == 0
    # Computed four sessions after its trade date.
    assert radar["进攻"]["backfilled_days"] == 1
    assert radar["进攻"]["horizons"]["10"] == {"measured_days": 0, "sufficient": False}


def test_many_signals_from_one_entry_day_are_still_one_sample(session, monkeypatch):
    monkeypatch.setattr(signal_outcomes_module, "MIN_SAMPLE", 1)
    signal, flat, weak = market(session)
    job = scan(session)
    for instrument in (signal, flat, weak):
        hit(session, job, instrument, [buy("2026-11-02")])
    session.commit()

    t5 = overall(SignalOutcomeService(session).build(), 5)

    assert (t5["measured"], t5["entry_days"], t5["sufficient"]) == (3, 1, False)
    assert "median_return" not in t5
