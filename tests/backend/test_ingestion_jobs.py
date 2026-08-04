from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.core.security import ensure_identity
from backend.app.db.models import IngestionRun, TradingCalendar
from backend.app.repositories.jobs import JobRepository
from backend.app.services.event_ingestion import EventIngestionService
from backend.app.services.ingestion import IngestionService
from backend.app.services.job_handlers import JobHandlers
from tests.backend.test_ingestion import FakeMarketDataProvider, _instrument_with_bar


def test_data_refresh_job_uses_native_ingestion_service(session):
    instrument = _instrument_with_bar(session, "900004", "900004.SH")
    user = ensure_identity(session, get_settings())
    jobs = JobRepository(session)
    job, _ = jobs.create(
        "data.refresh",
        {
            "codes": [instrument.code],
            "start_date": "2026-07-27",
            "end_date": "2026-07-28",
            "timeframe": "DAY",
            "adjustment": "QFQ",
        },
        user.id,
        force=True,
        max_attempts=3,
    )
    claimed = jobs.claim_next()
    assert claimed is not None and claimed.id == job.id

    result = JobHandlers(session, ingestion_provider=FakeMarketDataProvider()).execute(job)
    if job.status == "running":
        jobs.complete(job, result)
    session.refresh(job)

    assert result["status"] == "completed"
    assert (job.status, job.total, job.completed, job.failed) == ("completed", 1, 1, 0)
    run = session.scalar(select(IngestionRun).where(IngestionRun.id == result["ingestion_run_id"]))
    assert run is not None and run.status == "completed"


def test_api_to_worker_to_sqlite_smoke(client, session):
    instrument = _instrument_with_bar(session, "900006", "900006.SH")
    accepted = client.post(
        "/api/v1/jobs",
        json={
            "kind": "data.refresh",
            "payload": {
                "codes": [instrument.code],
                "start_date": "2026-07-27",
                "end_date": "2026-07-28",
                "timeframe": "DAY",
                "adjustment": "QFQ",
            },
            "force": True,
        },
    )
    assert accepted.status_code == 202

    jobs = JobRepository(session)
    job = jobs.claim_next()
    assert job is not None and job.id == accepted.json()["job_id"]
    result = JobHandlers(session, ingestion_provider=FakeMarketDataProvider()).execute(job)
    if job.status == "running":
        jobs.complete(job, result)

    stored = client.get(f"/api/v1/jobs/{job.id}")
    assert stored.status_code == 200
    assert stored.json()["status"] == "completed"
    assert stored.json()["result"]["bar_rows_written"] == 2


def test_native_ingestion_honors_cancellation(session):
    instrument = _instrument_with_bar(session, "900005", "900005.SH")
    result = IngestionService(session, FakeMarketDataProvider()).refresh_bars(
        codes=[instrument.code],
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
        cancelled=lambda: True,
    )

    assert result["status"] == "cancelled"
    assert (result["succeeded"], result["failed"], result["bar_rows_written"]) == (0, 0, 0)


def test_limit_up_refresh_defaults_to_latest_confirmed_open_day(session, monkeypatch):
    latest_open = date.today() - timedelta(days=1)
    session.add_all(
        [
            TradingCalendar(
                trade_date=latest_open,
                exchange="CN",
                is_open=True,
                source="test",
            ),
            TradingCalendar(
                trade_date=date.today(),
                exchange="CN",
                is_open=False,
                source="test",
            ),
        ]
    )
    user = ensure_identity(session, get_settings())
    jobs = JobRepository(session)
    job, _ = jobs.create("limit_up.refresh", {}, user.id, force=True, max_attempts=1)
    claimed = jobs.claim_next()
    assert claimed is not None and claimed.id == job.id
    captured: dict[str, list[date]] = {}

    def fake_refresh(_service, dates):
        captured["dates"] = list(dates)
        return {"status": "completed", "succeeded": 1, "failed": 0, "rows_written": 0}

    monkeypatch.setattr(EventIngestionService, "refresh_limit_ups", fake_refresh)

    result = JobHandlers(session).execute(job)

    assert captured["dates"] == [latest_open]
    assert result["status"] == "completed"