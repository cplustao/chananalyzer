from __future__ import annotations

from datetime import date, datetime, timedelta

from backend.app.db.models import Instrument, Job, ScanResult, TradingCalendar
from backend.app.repositories.jobs import JobRepository
from backend.app.services import job_handlers as job_handlers_module
from backend.app.services.job_handlers import JobHandlers
from backend.app.services.today_candidates import TODAY_CANDIDATE_LIMIT, TodayCandidateService

EXPECTED = date(2026, 11, 6)  # a Friday
GATE_PASSED = {"expected_trade_date": EXPECTED.isoformat(), "decision_usable": True}


def open_sessions(session, end=EXPECTED, days=20):
    for offset in range(days):
        day = end - timedelta(days=offset)
        session.add(TradingCalendar(exchange="CN", trade_date=day, is_open=day.weekday() < 5))
    session.flush()


def stock(session, code, name=None):
    instrument = Instrument(code=code, ts_code=f"{code}.SZ", exchange="SZSE", name=name or code, status="active")
    session.add(instrument)
    session.flush()
    return instrument


def scan(session, *, kind="screen.smart", status="completed", data_as_of=EXPECTED, created=None, coverage=1.0,
         payload=None):
    job = Job(
        kind=kind,
        status=status,
        payload=payload if payload is not None else {"scan_side": "buy"},
        result={"coverage_rate": coverage, "matched": 0,
                "data_as_of": data_as_of.isoformat() if data_as_of else None},
        created_at=created or datetime(2026, 11, 6, 10),
        finished_at=created or datetime(2026, 11, 6, 10),
    )
    session.add(job)
    session.flush()
    return job


def hit(session, job, instrument, signals, latest_price=10.0):
    session.add(ScanResult(
        job_id=job.id,
        instrument_id=instrument.id,
        scan_kind=job.kind,
        signal_type=signals[-1]["type"],
        signal_date=date.fromisoformat(signals[-1]["date"]),
        payload={"code": instrument.code, "name": instrument.name, "industry": "电子",
                 "signals": signals, "latest_price": latest_price},
    ))


def buy(day, kind="2", price=10.0):
    return {"type": kind, "is_buy": True, "date": day, "price": price}


def test_current_scan_lists_recent_buy_signals_newest_first_then_closest_to_signal_price(session):
    open_sessions(session)
    job = scan(session)
    near, far, older, stale, seller = (stock(session, code) for code in ("000001", "000002", "000003", "000004", "000005"))
    hit(session, job, near, [buy("2026-11-06", price=9.9)], latest_price=10.0)
    hit(session, job, far, [buy("2026-11-06", price=9.0)], latest_price=10.0)
    hit(session, job, older, [buy("2026-11-04", "3b")])
    # 11-03 is the fourth session back, outside the three-session window.
    hit(session, job, stale, [buy("2026-11-03")])
    hit(session, job, seller, [{"type": "2", "is_buy": False, "date": "2026-11-06", "price": 10.0}])
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["is_current"] is True
    assert result["reason"] is None
    assert result["signal_window"] == {"start": "2026-11-04", "end": "2026-11-06", "sessions": 3}
    assert [item["code"] for item in result["items"]] == ["000001", "000002", "000003"]
    first = result["items"][0]
    assert first["signal_price"] == 9.9
    assert round(first["distance_pct"], 2) == 1.01
    assert first["scan_kinds"] == ["screen.smart"]


def test_stocks_found_by_both_scans_appear_once_with_both_sources(session):
    open_sessions(session)
    smart = scan(session, kind="screen.smart", created=datetime(2026, 11, 6, 10))
    hot = scan(session, kind="screen.hot", payload={"rank_type": "top_amount"}, created=datetime(2026, 11, 6, 9))
    instrument = stock(session, "000001")
    hit(session, smart, instrument, [buy("2026-11-05")])
    hit(session, hot, instrument, [buy("2026-11-05")])
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["total"] == 1
    assert sorted(result["items"][0]["scan_kinds"]) == ["screen.hot", "screen.smart"]
    assert {item["kind"] for item in result["scans"]} == {"screen.smart", "screen.hot"}


def test_partial_scan_is_shown_with_its_coverage(session):
    open_sessions(session)
    job = scan(session, status="partial", coverage=0.9)
    hit(session, job, stock(session, "000001"), [buy("2026-11-06")])
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["is_current"] is True
    assert result["total"] == 1
    assert result["scans"][0]["status"] == "partial"
    assert result["scans"][0]["coverage_rate"] == 0.9


def test_older_scan_is_labelled_with_its_own_date_not_today(session):
    open_sessions(session)
    job = scan(session, data_as_of=date(2026, 11, 3), created=datetime(2026, 11, 3, 10))
    hit(session, job, stock(session, "000001"), [buy("2026-11-03")])
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["is_current"] is False
    assert result["reason"] == "stale_scan"
    assert result["basis_trade_date"] == "2026-11-03"
    assert result["signal_window"]["end"] == "2026-11-03"
    assert [item["code"] for item in result["items"]] == ["000001"]


def test_older_scans_of_the_same_data_date_are_merged(session):
    open_sessions(session)
    old_day = date(2026, 11, 5)
    smart = scan(session, kind="screen.smart", data_as_of=old_day, created=datetime(2026, 11, 5, 10))
    hot = scan(session, kind="screen.hot", payload={"rank_type": "top_amount"}, data_as_of=old_day,
               created=datetime(2026, 11, 5, 9))
    older = scan(session, kind="screen.hot", data_as_of=date(2026, 11, 4), created=datetime(2026, 11, 4, 9))
    hit(session, smart, stock(session, "000001"), [buy("2026-11-05")])
    hit(session, hot, stock(session, "000002"), [buy("2026-11-04")])
    hit(session, older, stock(session, "000003"), [buy("2026-11-04")])
    session.commit()

    result = TodayCandidateService(session).build({"expected_trade_date": EXPECTED.isoformat(), "decision_usable": False})

    assert result["is_current"] is False
    assert result["basis_trade_date"] == "2026-11-05"
    assert {item["job_id"] for item in result["scans"]} == {smart.id, hot.id}
    assert [item["code"] for item in result["items"]] == ["000001", "000002"]
    assert [item["unconfirmed"] for item in result["items"]] == [True, False]


def test_scan_without_a_recorded_data_date_is_never_current(session):
    open_sessions(session)
    job = scan(session, data_as_of=None, created=datetime(2026, 11, 6, 2))
    hit(session, job, stock(session, "000001"), [buy("2026-11-06")])
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["is_current"] is False
    assert result["basis_trade_date"] == "2026-11-06"


def test_blocked_gate_never_marks_candidates_as_current(session):
    open_sessions(session)
    job = scan(session)
    hit(session, job, stock(session, "000001"), [buy("2026-11-06")])
    session.commit()

    result = TodayCandidateService(session).build({"expected_trade_date": EXPECTED.isoformat(), "decision_usable": False})

    assert result["is_current"] is False
    assert result["reason"] == "gate_blocked"


def test_sell_scans_are_ignored_and_pending_scans_are_reported(session):
    open_sessions(session)
    sell = scan(session, payload={"scan_side": "sell"})
    hit(session, sell, stock(session, "000001"), [buy("2026-11-06")])
    queued = Job(kind="screen.smart", status="queued", payload={"scan_side": "buy"},
                 created_at=datetime(2026, 11, 6, 11))
    session.add(queued)
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["reason"] == "no_scan"
    assert result["items"] == []
    assert result["pending_scan_job_ids"] == [queued.id]


def test_list_is_capped_but_reports_the_full_count(session):
    open_sessions(session)
    job = scan(session)
    for index in range(TODAY_CANDIDATE_LIMIT + 5):
        hit(session, job, stock(session, f"{index:06d}"), [buy("2026-11-06")])
    session.commit()

    result = TodayCandidateService(session).build(GATE_PASSED)

    assert result["total"] == TODAY_CANDIDATE_LIMIT + 5
    assert len(result["items"]) == TODAY_CANDIDATE_LIMIT


def test_decision_today_returns_candidates_and_watchlist_hits(client, session, monkeypatch):
    open_sessions(session)
    job = scan(session)
    instrument = stock(session, "000001", "平安银行")
    hit(session, job, instrument, [buy("2026-11-06")])
    session.commit()
    monkeypatch.setattr(
        "backend.app.services.decision.DataHealthService.cached_snapshot",
        lambda self, **_kwargs: {**GATE_PASSED, "categories": [], "blocking_reasons": []},
    )
    added = client.post("/api/v1/watchlists/default/items", json={"instrument_id": instrument.id, "tag_names": []})
    assert added.status_code in (200, 201)

    today = client.get("/api/v1/decision/today").json()

    assert today["today_candidates"]["is_current"] is True
    assert today["today_candidates"]["items"][0]["code"] == "000001"
    assert today["watchlist_hits"][0]["instrument_id"] == instrument.id
    assert "today_candidates" not in today["missing_or_stale_modules"]


def test_scan_records_the_trade_date_its_data_stood_for(session, monkeypatch):
    instrument = stock(session, "000001")
    job = Job(kind="screen.smart", status="running", payload={"scan_side": "buy", "types": ["2"]})
    session.add(job)
    session.commit()

    class FakeHealth:
        def __init__(self, *_args):
            pass

        def cached_snapshot(self, **_kwargs):
            return {"decision_usable": True, "as_of_trade_date": "2026-11-06", "expected_trade_date": "2026-11-06"}

    class Engine:
        def scan(self, **_kwargs):
            return [{"code": instrument.code, "signals": [buy("2026-11-06")], "latest_price": 10.0}]

    monkeypatch.setattr(job_handlers_module, "DataHealthService", FakeHealth)
    handler = JobHandlers.__new__(JobHandlers)
    handler.session = session
    handler.jobs = JobRepository(session)
    handler.chan_engine = Engine()

    result = handler._scan(job)

    assert result["data_as_of"] == "2026-11-06"
    assert result["decision_usable"] is True


def test_daily_prepare_queues_screens_by_default(session, monkeypatch):
    job = Job(kind="daily.prepare", status="running", payload={})
    session.add(job)
    session.commit()
    handler = JobHandlers.__new__(JobHandlers)
    handler.session = session
    handler.jobs = JobRepository(session)
    handler._data_refresh = lambda _job, **_kwargs: {"status": "completed"}
    handler._limit_up_refresh = lambda _job: {"status": "completed"}
    handler._market_radar = lambda _job: {"status": "completed"}
    categories = [
        {"key": "master_data", "status": "fresh"},
        {"key": "daily_bars", "status": "fresh", "coverage_rate": 1.0},
        {"key": "trading_calendar", "status": "fresh"},
    ]

    class FakeHealth:
        def __init__(self, *_args):
            pass

        def snapshot(self):
            return {"categories": categories, "blocking_reasons": []}

        def refresh_cache(self, **_kwargs):
            return {"categories": categories, "blocking_reasons": [], "decision_usable": True}

    monkeypatch.setattr(job_handlers_module, "DataHealthService", FakeHealth)

    result = handler._daily_prepare(job)

    queued = [session.get(Job, job_id) for job_id in result["queued_screening_jobs"]]
    assert [item.kind for item in queued] == ["screen.hot", "screen.smart"]
    assert all(item.payload["scan_side"] == "buy" for item in queued)


def test_decision_today_reports_daily_preparation_automation(client, session):
    enabled = client.put(
        "/api/v1/settings/schedules/weekday-daily-prepare",
        json={
            "job_kind": "daily.prepare", "hour": 17, "minute": 0, "weekdays": [1, 2, 3, 4, 5],
            "enabled": True, "payload": {"run_screeners": True, "screening_preset": "balanced"},
        },
    )
    assert enabled.status_code == 200
    succeeded = Job(
        kind="daily.prepare", status="completed", failed=15,
        payload={"automation_schedule": "weekday-daily-prepare"},
        result={"warnings": [{"stage": "market_data", "failed": 15, "codes": ["920023"], "message": "x"}]},
        created_at=datetime(2026, 11, 6, 9), finished_at=datetime(2026, 11, 6, 9, 30),
    )
    failed = Job(
        kind="daily.prepare", status="failed", failure_summary="行情质量门禁未通过：daily_bars",
        payload={}, created_at=datetime(2026, 11, 9, 9), finished_at=datetime(2026, 11, 9, 9, 20),
    )
    session.add_all([succeeded, failed])
    session.commit()

    automation = client.get("/api/v1/decision/today").json()["automation"]

    assert automation["enabled"] is True
    assert (automation["hour"], automation["minute"]) == (17, 0)
    assert automation["next_run_at"].endswith("+08:00")
    assert automation["run_screeners"] is True
    assert automation["last_success"]["id"] == succeeded.id
    assert automation["last_success"]["scheduled"] is True
    assert automation["last_success"]["finished_at"] == "2026-11-06T17:30:00+08:00"
    assert automation["last_success"]["warnings"][0]["failed"] == 15
    assert automation["latest_run"]["id"] == failed.id
    assert automation["latest_run"]["error"] == "行情质量门禁未通过：daily_bars"


def test_decision_today_reports_automation_off_without_runs(client):
    automation = client.get("/api/v1/decision/today").json()["automation"]

    assert automation["enabled"] is False
    assert automation["latest_run"] is None
    assert automation["last_success"] is None
