from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from backend.app.db.models import Bar, Instrument, Job
from backend.app.domain.daily_refresh import (
    INITIAL_HISTORY_DAYS,
    adjustment_base_changed,
    plan_daily_refresh,
)
from backend.app.providers.market_data import CalendarSession, MarketBar
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services.ingestion import MAX_CONSECUTIVE_PROVIDER_OUTAGES, IngestionService
from backend.app.services.job_handlers import JobHandlers

END = date(2026, 7, 30)
HOLIDAY = date(2026, 7, 20)  # Synthetic closed session; do not infer weekdays in production.


def days(start, end):
    return [start + timedelta(days=index) for index in range((end - start).days + 1)]


def trading_days(start, end):
    return [day for day in days(start, end) if day.weekday() < 5 and day != HOLIDAY]


def bar(day, volume=100):
    return MarketBar(datetime.combine(day, datetime.min.time()), 10, 12, 9, 11, volume, 1100)


class HistoryProvider:
    key = "history-test"
    name = "History test provider"

    def __init__(self, omitted=()):
        self.omitted = set(omitted)
        self.requests = []
        self.fail_request = None

    def fetch_calendar(self, *, start_date, end_date):
        return [CalendarSession(day, day.weekday() < 5 and day != HOLIDAY) for day in days(start_date, end_date)]

    def fetch_bars(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == self.fail_request:
            raise RuntimeError("temporary provider failure")
        return [bar(day) for day in trading_days(kwargs["start_date"], kwargs["end_date"]) if day not in self.omitted]


@pytest.fixture
def service(session, monkeypatch):
    monkeypatch.setattr(IngestionService, "_settled_end_date", lambda _self: END)
    return IngestionService(session, HistoryProvider())


def seed(session, *, listed=date(2026, 6, 1), through=None, omitted=(), suspended=(), adjustment="QFQ", exchange="SH"):
    instrument = Instrument(
        code="900001", ts_code=f"900001.{exchange}", exchange=exchange,
        name="自动补全样本", status="active", list_date=listed,
    )
    session.add(instrument)
    session.flush()
    repository = IngestionRepository(session)
    source = repository.data_source(HistoryProvider.key, HistoryProvider.name)
    if through:
        repository.upsert_bars(
            instrument_id=instrument.id, timeframe="DAY", adjustment=adjustment, data_source_id=source.id,
            bars=[bar(day, 0 if day in suspended else 100) for day in trading_days(listed, through) if day not in omitted],
        )
    session.commit()
    return instrument


def stored_dates(session, instrument, adjustment="QFQ"):
    return {value.date() for value in session.scalars(select(Bar.bar_time).where(
        Bar.instrument_id == instrument.id, Bar.timeframe == "DAY", Bar.adjustment == adjustment,
    ))}


def test_auto_catches_up_month_long_absence_with_three_session_overlap(session, service):
    last_update = date(2026, 7, 1)
    instrument = seed(session, through=last_update)

    result = service.refresh_bars()

    assert result["history_complete"] is True
    assert service.provider.requests[0]["start_date"] == trading_days(instrument.list_date, last_update)[-3]
    assert stored_dates(session, instrument) == set(trading_days(instrument.list_date, END))
    assert result["requested_ranges"] == 1


def test_auto_repairs_old_interior_gap_beyond_initialization_window(session, service):
    gap = date(2023, 1, 10)
    instrument = seed(session, listed=date(2023, 1, 2), through=END, omitted=[gap])

    result = service.refresh_bars()

    assert gap in stored_dates(session, instrument)
    assert result["history_complete"] is True
    assert result["requested_ranges"] == 2
    assert service.provider.requests[0]["start_date"] == date(2023, 1, 9)
    assert service.provider.requests[0]["end_date"] == date(2023, 1, 11)
    assert service.provider.requests[1]["start_date"] == date(2026, 7, 28)


def test_empty_history_initializes_two_years_and_second_refresh_only_replays_tail(session, service):
    instrument = seed(session, listed=date(2020, 1, 1))
    expected = set(trading_days(END - timedelta(days=INITIAL_HISTORY_DAYS), END))

    first = service.refresh_bars()
    service.provider.requests.clear()
    second = service.refresh_bars()

    assert first["history_complete"] is second["history_complete"] is True
    assert stored_dates(session, instrument) == expected
    assert second["bar_rows_written"] == 3
    assert service.provider.requests[0]["start_date"] == sorted(expected)[-3]


def test_recent_ipo_initializes_from_listing_date(session, service):
    instrument = seed(session, listed=date(2026, 7, 23))
    result = service.refresh_bars()
    assert result["history_complete"] is True
    assert service.provider.requests[0]["start_date"] == instrument.list_date
    assert stored_dates(session, instrument) == set(trading_days(instrument.list_date, END))


def test_holidays_and_recorded_suspensions_are_not_missing_bars(session, service):
    suspension = date(2026, 7, 6)
    instrument = seed(session, through=END, suspended=[suspension])
    result = service.refresh_bars()

    assert result["remaining_gap_count"] == 0
    assert result["requested_ranges"] == 1
    assert service.provider.requests[0]["start_date"] == date(2026, 7, 28)
    assert HOLIDAY not in stored_dates(session, instrument)
    assert session.scalar(select(Bar.trade_status).where(
        Bar.instrument_id == instrument.id, Bar.bar_time == datetime(2026, 7, 6),
    )) == "suspended"


def test_provider_omission_stays_partial_and_is_never_fabricated_as_suspension(session, service):
    gap = date(2026, 7, 10)
    instrument = seed(session, through=END, omitted=[gap])
    service.provider.omitted.add(gap)
    result = service.refresh_bars()

    assert result["status"] == "partial"
    assert result["history_complete"] is False
    assert result["failed_codes"] == [instrument.code]
    assert result["remaining_gap_count"] == 1
    assert result["gap_details"][0]["sample_dates"] == [gap.isoformat()]
    assert gap not in stored_dates(session, instrument)


def test_successful_repair_is_preserved_when_later_range_fails(session, service):
    gap = date(2026, 7, 3)
    instrument = seed(session, through=END, omitted=[gap])
    service.provider.fail_request = 2
    failed = service.refresh_bars()

    assert failed["status"] == "partial"
    assert gap in stored_dates(session, instrument)
    service.provider.requests.clear()
    service.provider.fail_request = None
    recovered = service.refresh_bars()
    assert recovered["history_complete"] is True
    assert recovered["requested_ranges"] == 1
    assert service.provider.requests[0]["start_date"] == date(2026, 7, 28)


def test_cancellation_between_ranges_keeps_already_repaired_bars(session, service):
    gap = date(2026, 7, 3)
    instrument = seed(session, through=END, omitted=[gap])
    result = service.refresh_bars(cancelled=lambda: len(service.provider.requests) >= 1)
    assert result["status"] == "cancelled"
    assert result["history_complete"] is False
    assert gap in stored_dates(session, instrument)
    assert len(service.provider.requests) == 1


def test_explicit_date_range_is_not_expanded(session, service):
    seed(session, listed=date(2020, 1, 1))
    result = service.refresh_bars(start_date=date(2026, 7, 29), end_date=END)
    assert result["refresh_mode"] == "range"
    assert result["history_complete"] is None
    assert service.provider.requests[0]["start_date"] == date(2026, 7, 29)
    assert result["bar_rows_written"] == 2


def test_explicit_window_opt_out_preserves_single_stock_refresh(session, service):
    seed(session, listed=date(2020, 1, 1))
    result = service.refresh_bars(auto_backfill=False, lookback_days=30)
    assert result["refresh_mode"] == "range"
    assert service.provider.requests[0]["start_date"] == END - timedelta(days=30)


def test_bj_uses_unadjusted_series_for_catch_up(session, service):
    instrument = seed(session, through=date(2026, 7, 1), adjustment="NONE", exchange="BJ")
    result = service.refresh_bars()
    assert result["history_complete"] is True
    assert service.provider.requests[0]["adjustment"] == "NONE"
    assert stored_dates(session, instrument, "NONE") == set(trading_days(instrument.list_date, END))


def test_stale_bar_is_repaired(session, service):
    instrument = seed(session, through=END)
    stale = session.scalar(select(Bar).where(Bar.bar_time == datetime(2026, 7, 10)))
    stale.quality_status = "stale"
    session.commit()
    result = service.refresh_bars()
    session.refresh(stale)
    assert result["history_complete"] is True
    assert stale.quality_status == "ok"
    assert result["requested_ranges"] == 2
    assert len(stored_dates(session, instrument)) == len(trading_days(instrument.list_date, END))


def test_other_adjustment_is_not_mistaken_for_existing_research_history(session, service):
    instrument = seed(session, through=END, adjustment="NONE")
    result = service.refresh_bars()
    assert result["history_complete"] is True
    assert service.provider.requests[0]["start_date"] == instrument.list_date
    assert stored_dates(session, instrument) == set(trading_days(instrument.list_date, END))


def test_unlisted_instrument_is_skipped_without_fetching_or_fake_bars(session, service):
    instrument = seed(session, listed=END + timedelta(days=1))
    result = service.refresh_bars()
    assert result["skipped_not_listed"] == 1
    assert service.provider.requests == []
    assert stored_dates(session, instrument) == set()


def test_api_default_job_reaches_worker_with_automatic_catch_up(client, session, service):
    instrument = seed(session, through=date(2026, 7, 1))
    response = client.post("/api/v1/jobs", json={
        "kind": "data.refresh", "payload": {"codes": [instrument.code]}, "force": True,
    })
    assert response.status_code == 202
    job = JobRepository(session).claim_next()
    assert job.payload["auto_backfill"] is True
    result = JobHandlers(session, ingestion_provider=service.provider).execute(job)
    assert result["history_complete"] is True
    assert stored_dates(session, instrument) == set(trading_days(instrument.list_date, END))


def test_long_holiday_does_not_shrink_overlap_to_calendar_days():
    open_dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 29), END]
    plan = plan_daily_refresh(
        open_dates=open_dates, end=END, first_stored=open_dates[0],
        saved_dates=set(open_dates), list_date=open_dates[0],
    )
    assert plan.ranges == ((date(2026, 7, 2), END),)


def test_missing_listing_date_does_not_invent_pre_listing_gaps(session, service):
    instrument = seed(session)
    instrument.list_date = None
    session.commit()
    observed_start = date(2026, 7, 23)
    service.provider.omitted.update(trading_days(END - timedelta(days=INITIAL_HISTORY_DAYS), observed_start - timedelta(days=1)))

    first = service.refresh_bars()
    service.provider.requests.clear()
    second = service.refresh_bars()

    assert first["history_complete"] is second["history_complete"] is True
    assert first["listing_date_unknown_codes"] == [instrument.code]
    assert stored_dates(session, instrument) == set(trading_days(observed_start, END))
    assert service.provider.requests[0]["start_date"] == date(2026, 7, 28)


def stored_closes(session, instrument):
    return {
        row.bar_time.date(): row.close
        for row in session.execute(select(Bar.bar_time, Bar.close).where(
            Bar.instrument_id == instrument.id, Bar.adjustment == "QFQ",
        ))
    }


def test_ex_rights_base_change_replaces_the_whole_saved_series(session, service, monkeypatch):
    instrument = seed(session, through=date(2026, 7, 28))
    assert set(stored_closes(session, instrument).values()) == {11}
    rebased = 5.5

    def fetch_on_new_base(**kwargs):
        service.provider.requests.append(kwargs)
        return [
            MarketBar(datetime.combine(day, datetime.min.time()), 5, 6, 4.5, rebased, 100, 550)
            for day in trading_days(kwargs["start_date"], kwargs["end_date"])
        ]

    monkeypatch.setattr(service.provider, "fetch_bars", fetch_on_new_base)
    result = service.refresh_bars()

    assert result["rebased_codes"] == [instrument.code]
    assert result["history_complete"] is True
    assert service.provider.requests[-1]["start_date"] == instrument.list_date
    # No bar may be left on the old base, or the series would carry a fake gap.
    assert set(stored_closes(session, instrument).values()) == {rebased}


def test_unchanged_overlap_keeps_the_tail_only_update(session, service):
    instrument = seed(session, through=date(2026, 7, 28))
    result = service.refresh_bars()
    assert result["rebased_codes"] == []
    assert len(service.provider.requests) == 1
    assert service.provider.requests[0]["start_date"] > instrument.list_date


def test_tiny_rounding_differences_are_not_a_base_change():
    day = date(2026, 7, 1)
    assert adjustment_base_changed({day: 10.00}, {day: 10.005}) is False
    assert adjustment_base_changed({day: 1000.0}, {day: 1000.5}) is False
    assert adjustment_base_changed({day: 10.00}, {day: 9.80}) is True
    assert adjustment_base_changed({}, {day: 9.80}) is False


def test_provider_outage_stops_the_batch_early_and_keeps_saved_bars(session, service, monkeypatch):
    healthy = seed(session, through=date(2026, 7, 1))
    for index in range(MAX_CONSECUTIVE_PROVIDER_OUTAGES + 10):
        code = f"60{index:04d}"
        session.add(Instrument(
            code=code, ts_code=f"{code}.SH", exchange="SH", name=code, status="active", list_date=date(2026, 6, 1),
        ))
    session.commit()
    real_fetch = service.provider.fetch_bars
    outage_calls = []

    def down_after_first_stock(**kwargs):
        if kwargs["ts_code"] == healthy.ts_code:
            return real_fetch(**kwargs)
        outage_calls.append(kwargs["ts_code"])
        raise RuntimeError("all daily market-data providers failed (baostock: 网络接收错误。)")

    monkeypatch.setattr(service.provider, "fetch_bars", down_after_first_stock)
    monkeypatch.setattr(IngestionRepository, "refresh_targets", lambda _self, _codes: [healthy, *session.scalars(
        select(Instrument).where(Instrument.id != healthy.id).order_by(Instrument.code)
    ).all()])

    result = service.refresh_bars()

    assert len(outage_calls) == MAX_CONSECUTIVE_PROVIDER_OUTAGES
    assert result["status"] == "partial"
    assert result["succeeded"] == 1
    assert "提前停止" in result["errors"][0]["error"]
    assert set(trading_days(healthy.list_date, END)) == stored_dates(session, healthy)


def test_saved_calendar_fallback_cannot_claim_verified_history(session, service, monkeypatch):
    seed(session, through=END)
    repository = IngestionRepository(session)
    repository.upsert_calendar(service.provider.fetch_calendar(start_date=END - timedelta(days=730), end_date=END), source="saved")
    session.commit()

    def offline_calendar(**_kwargs):
        raise RuntimeError("calendar offline")

    monkeypatch.setattr(service.provider, "fetch_calendar", offline_calendar)
    result = service.refresh_bars()
    assert result["status"] == "partial"
    assert result["history_complete"] is False
    assert "交易日历刷新失败" in result["errors"][0]["error"]


def test_cancelled_daily_preparation_stops_at_market_data(session):
    job = Job(kind="daily.prepare", status="running", payload={})
    session.add(job)
    session.commit()
    handler = JobHandlers.__new__(JobHandlers)
    handler.session = session
    handler.jobs = JobRepository(session)

    def cancelled_market_data(current_job, **_kwargs):
        handler.jobs.mark_cancelled(current_job)
        return {"status": "cancelled"}

    handler._data_refresh = cancelled_market_data
    result = handler._daily_prepare(job)
    assert result["blocking_stage"] == "market_data"
    assert job.checkpoint["market_data"]["status"] == "failed"
