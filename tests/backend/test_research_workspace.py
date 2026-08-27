from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from backend.app.db.models import (
    AnalysisReport,
    AnalysisRun,
    AutomationSchedule,
    Instrument,
    Job,
    LimitUpEvent,
    LimitUpMetric,
    ScanResult,
)


def test_analysis_history_and_detail(client, session):
    instrument = Instrument(
        code="000858",
        ts_code="000858.SZ",
        exchange="SZSE",
        name="五粮液",
        status="active",
    )
    session.add(instrument)
    session.flush()
    event = LimitUpEvent(
        instrument_id=instrument.id,
        legacy_code=instrument.code,
        trade_date=date(2026, 7, 18),
        consecutive_boards=2,
    )
    session.add(event)
    session.flush()
    run = AnalysisRun(
        kind="limit_up",
        instrument_id=instrument.id,
        subject_date=date(2026, 7, 18),
        status="completed",
        algorithm_version="test-1.0",
        input_snapshot={"code": instrument.code, "evidence": ["量价结构"]},
        finished_at=datetime(2026, 7, 18),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            AnalysisReport(
                analysis_run_id=run.id,
                role="analyst",
                content="分析师报告",
            ),
            AnalysisReport(
                analysis_run_id=run.id,
                role="decision",
                content="决策报告",
            ),
            LimitUpMetric(
                analysis_run_id=run.id,
                limit_up_event_id=event.id,
                overall_score=81.5,
                risk_score=72,
            ),
        ]
    )
    session.commit()

    history = client.get(
        "/api/v1/analyses?kind=limit_up&start_date=2026-07-01&end_date=2026-07-31"
    )
    assert history.status_code == 200
    item = next(value for value in history.json()["items"] if value["id"] == run.id)
    assert item["instrument"]["code"] == "000858"
    assert item["report_count"] == 2
    assert item["overall_score"] == 81.5

    detail = client.get(f"/api/v1/analyses/{run.id}")
    assert detail.status_code == 200
    assert [report["role"] for report in detail.json()["reports"]] == [
        "analyst",
        "decision",
    ]
    assert detail.json()["metrics"]["risk_score"] == 72


def test_watchlist_update_and_duplicate_add_preserves_research(client, session):
    instrument = Instrument(
        code="300750",
        ts_code="300750.SZ",
        exchange="SZSE",
        name="宁德时代",
        status="active",
    )
    session.add(instrument)
    session.commit()
    created = client.post(
        "/api/v1/watchlists/default/items",
        json={
            "instrument_id": instrument.id,
            "note": "观察周线中枢",
            "tag_names": ["新能源"],
        },
    )
    assert created.status_code == 200
    item = next(
        value
        for value in created.json()["items"]
        if value["instrument"]["id"] == instrument.id
    )
    updated = client.put(
        f"/api/v1/watchlists/default/items/{item['id']}",
        json={
            "note": "等待三买确认",
            "tag_names": ["新能源", "三买候选"],
            "position": 1,
        },
    )
    assert updated.status_code == 200
    duplicate = client.post(
        "/api/v1/watchlists/default/items",
        json={"instrument_id": instrument.id, "tag_names": []},
    )
    saved = next(
        value
        for value in duplicate.json()["items"]
        if value["instrument"]["id"] == instrument.id
    )
    assert saved["note"] == "等待三买确认"
    assert set(saved["tags"]) == {"新能源", "三买候选"}


def test_scan_history_includes_saved_result_count(client, session):
    instrument = Instrument(
        code="002594",
        ts_code="002594.SZ",
        exchange="SZSE",
        name="比亚迪",
        status="active",
    )
    session.add(instrument)
    session.flush()
    job = Job(
        kind="screen.smart",
        status="completed",
        payload={"scan_side": "buy", "industries": ["汽车"]},
        progress=100,
        completed=1,
        total=1,
        created_at=datetime(2026, 7, 20),
        finished_at=datetime(2026, 7, 20),
    )
    session.add(job)
    session.flush()
    session.add(
        ScanResult(
            job_id=job.id,
            instrument_id=instrument.id,
            scan_kind="buy",
            signal_type="2",
            signal_date=date(2026, 7, 18),
            rank=1,
            score=88,
        )
    )
    session.commit()
    history = client.get("/api/v1/scans/history?limit=10")
    assert history.status_code == 200
    saved = next(item for item in history.json() if item["id"] == job.id)
    assert saved["result_count"] == 1
    assert saved["payload"]["industries"] == ["汽车"]

def test_secret_connection_reports_health_without_exposing_value(client, monkeypatch):
    captured: dict = {}

    class FakeResponse:
        is_success = True
        status_code = 200

        @staticmethod
        def json():
            return {"code": 0, "msg": None}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def request(self, *_args, **kwargs):
            if kwargs.get("json"):
                captured.update(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(
        "backend.app.api.routes.settings.httpx.AsyncClient",
        FakeClient,
    )
    saved = client.put(
        "/api/v1/settings/secrets/DEEPSEEK_API_KEY",
        json={"value": "connection-test-secret"},
    )
    assert saved.status_code == 200
    result = client.post("/api/v1/settings/secrets/DEEPSEEK_API_KEY/test")
    assert result.status_code == 200
    assert result.json()["ok"] is True
    assert "connection-test-secret" not in result.text

    tushare_saved = client.put(
        "/api/v1/settings/secrets/TUSHARE_TOKEN",
        json={"value": "tushare-test-secret"},
    )
    assert tushare_saved.status_code == 200
    tushare_result = client.post("/api/v1/settings/secrets/TUSHARE_TOKEN/test")
    assert tushare_result.status_code == 200
    assert tushare_result.json()["ok"] is True
    assert tushare_result.json()["message"] == "基础日线连接成功"
    assert captured["api_name"] == "daily"
    assert "trade_cal" not in tushare_result.text
    assert "tushare-test-secret" not in tushare_result.text
    assert client.post("/api/v1/settings/secrets/UNKNOWN/test").status_code == 400

def test_automation_schedule_can_be_configured_run_and_enqueued(client, session):
    schedules = client.get("/api/v1/settings/schedules")
    assert schedules.status_code == 200
    assert {item["key"] for item in schedules.json()} == {"weekday-daily-prepare"}
    by_key = {item["key"]: item for item in schedules.json()}
    assert by_key["weekday-daily-prepare"]["job_kind"] == "daily.prepare"
    assert by_key["weekday-daily-prepare"]["hour"] == 17
    assert by_key["weekday-daily-prepare"]["payload"]["run_screeners"] is False
    updated = client.put(
        "/api/v1/settings/schedules/weekday-daily-prepare",
        json={
            "job_kind": "daily.prepare",
            "hour": 17,
            "minute": 25,
            "weekdays": [1, 2, 3, 4, 5],
            "enabled": True,
            "payload": {},
        },
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert updated.json()["next_run_at"].endswith("+08:00")
    run_now = client.post(
        "/api/v1/settings/schedules/weekday-daily-prepare/run"
    )
    assert run_now.status_code == 202

    from backend.app.services.schedules import enqueue_due_schedules, upsert_schedule

    data_schedule = upsert_schedule(
        session,
        "weekday-daily-prepare",
        "daily.prepare",
        17,
        0,
        [1, 2, 3, 4, 5],
        True,
        {},
    )
    data_schedule.next_run_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    session.commit()
    # The manual run is still queued with the same canonical payload, so the
    # due schedule advances without creating a duplicate active preparation.
    assert enqueue_due_schedules(session, max_attempts=3) == 0
    refreshed = session.scalar(
        select(AutomationSchedule).where(
            AutomationSchedule.key == "weekday-daily-prepare"
        )
    )
    assert refreshed is not None
    assert refreshed.last_run_at is not None
    assert refreshed.next_run_at > refreshed.last_run_at

def test_unlinked_analysis_uses_snapshot_subject(client, session):
    run = AnalysisRun(
        kind="ipo",
        instrument_id=None,
        subject_date=date(2026, 7, 21),
        status="completed",
        algorithm_version="1.0",
        input_snapshot={"code": "688806", "name": "泰诺麦博"},
        finished_at=datetime(2026, 7, 21),
    )
    session.add(run)
    session.commit()
    history = client.get("/api/v1/analyses?kind=ipo&page_size=100")
    item = next(value for value in history.json()["items"] if value["id"] == run.id)
    assert item["instrument"] is None
    assert item["subject_code"] == "688806"
    assert item["subject_name"] == "泰诺麦博"
