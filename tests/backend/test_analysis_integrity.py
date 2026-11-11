"""Chan output must be based on a full series, and say so when it is not."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import sin

from backend.app.chan_core.engine import ALGORITHM_VERSION as CHAN_ALGORITHM_VERSION
from backend.app.core.config import get_settings
from backend.app.db.models import AnalysisRun, Instrument, Job
from backend.app.providers.market_data import MarketBar
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.services.bar_access import (
    MIN_ANALYSIS_BARS,
    analysis_series_coverage,
    analysis_series_summary,
)
from backend.app.services.chan_analysis import DatabaseChanEngine
from backend.app.services.data_health import DataHealthService
from backend.app.services.decision import DecisionService


def stock(session, code: str, *, listed: date | None = date(2020, 1, 2)) -> Instrument:
    instrument = Instrument(
        code=code, ts_code=f"{code}.SH", exchange="SH", name=f"样本{code}", status="active", list_date=listed,
    )
    session.add(instrument)
    session.flush()
    return instrument


def wave_bars(count: int, end: date | None = None) -> list[MarketBar]:
    """Weekday bars ending at ``end``, oscillating enough to form real strokes."""
    current = end or date.today() - timedelta(days=1)
    days: list[date] = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current -= timedelta(days=1)
    bars = []
    for index, day in enumerate(reversed(days)):
        close = 20 + 6 * sin(index / 6) + 2 * sin(index / 2.3)
        bars.append(
            MarketBar(datetime.combine(day, datetime.min.time()), close - 0.2, close + 0.5, close - 0.5, close, 1000, 20000)
        )
    return bars


def save(session, instrument: Instrument, bars: list[MarketBar], source: str = "provider-a") -> None:
    repository = IngestionRepository(session)
    repository.upsert_bars(
        instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ",
        data_source_id=repository.data_source(source, source).id, bars=bars,
    )
    session.commit()


def test_coverage_counts_only_bars_on_each_stocks_newest_provider(session):
    complete = stock(session, "600001")
    save(session, complete, wave_bars(MIN_ANALYSIS_BARS))

    # 200 stored bars, but a recent refresh from another provider leaves analysis only 5.
    truncated = stock(session, "600002")
    history = wave_bars(200)
    save(session, truncated, history[:-5], source="legacy")
    save(session, truncated, history[-5:], source="provider-a")
    assert analysis_series_summary(session, truncated.id)[0] == 5

    coverage = analysis_series_coverage(session)

    assert coverage["eligible_count"] == 2
    assert coverage["usable_count"] == 1
    assert coverage["insufficient_count"] == 1
    assert coverage["coverage_rate"] == 0.5


def test_recent_listings_are_not_counted_against_coverage(session):
    save(session, stock(session, "600001"), wave_bars(MIN_ANALYSIS_BARS))
    save(session, stock(session, "600003", listed=date.today() - timedelta(days=30)), wave_bars(20))

    coverage = analysis_series_coverage(session)

    assert coverage["eligible_count"] == 1
    assert coverage["coverage_rate"] == 1.0


def test_truncated_series_blocks_the_decision_gate_even_with_a_bar_on_the_latest_day(session):
    latest = date.today() - timedelta(days=1)
    truncated = stock(session, "600002")
    save(session, truncated, wave_bars(5, end=latest))

    snapshot = DataHealthService(session, get_settings()).snapshot()
    categories = {item["key"]: item for item in snapshot["categories"]}

    assert categories["analysis_series"]["status"] == "stale"
    assert "analysis_series" in snapshot["blocking_reasons"]
    assert snapshot["decision_usable"] is False
    assert f"不足 {MIN_ANALYSIS_BARS} 根" in categories["analysis_series"]["recommendation"]


def test_complete_series_passes_the_analysis_series_check(session):
    save(session, stock(session, "600001"), wave_bars(MIN_ANALYSIS_BARS))
    health = DataHealthService(session, get_settings())
    health.refresh_analysis_series()

    category = next(item for item in health.snapshot()["categories"] if item["key"] == "analysis_series")

    assert category["status"] == "fresh"
    assert category["usable_count"] == 1


def test_review_context_withholds_next_session_output_for_truncated_series(session):
    service = DecisionService(session, get_settings(), "local-user")
    health = {
        "expected_trade_date": "2026-07-29",
        "categories": [
            {"key": "daily_bars", "status": "fresh"},
            {"key": "analysis_series", "status": "stale"},
            {"key": "master_data", "status": "fresh"},
            {"key": "trading_calendar", "status": "fresh"},
        ],
    }
    radar = {"trade_date": "2026-07-29", "freshness": "fresh", "generated_at": "2026-07-29T09:00:00Z"}

    context = service._review_context(health, radar)

    assert context["usable_for_next_session"] is False
    assert context["blocking_modules"] == ["analysis_series"]


def test_scan_reports_short_series_as_insufficient_not_as_no_signal(session):
    short = stock(session, "600002")
    save(session, short, wave_bars(MIN_ANALYSIS_BARS - 1))
    long = stock(session, "600001")
    save(session, long, wave_bars(MIN_ANALYSIS_BARS * 2))

    outcome = DatabaseChanEngine(session).scan(
        stock_codes=["600002", "600001"], buy_types=[], sell_types=[],
    )

    statuses = {subject.code: subject.status for subject in outcome.subjects}
    assert statuses == {"600002": "insufficient_history", "600001": "no_signal"}


def test_analysis_records_how_many_bars_it_used(session):
    instrument = stock(session, "600001")
    save(session, instrument, wave_bars(MIN_ANALYSIS_BARS - 20))

    analysis = DatabaseChanEngine(session).analyze(instrument.code)

    assert analysis["analysis_bar_count"] == MIN_ANALYSIS_BARS - 20
    assert analysis["history_sufficient"] is False
    assert analysis["bi_list"], "fixture must produce real strokes"


def add_run(session, instrument: Instrument, **snapshots) -> None:
    session.add(AnalysisRun(
        kind="stock", instrument_id=instrument.id, status="completed", algorithm_version="test",
        finished_at=datetime.now(), **snapshots,
    ))
    session.commit()


def structure(client, instrument: Instrument) -> dict:
    response = client.get(f"/api/v1/instruments/{instrument.id}/chan-structure")
    assert response.status_code == 200
    return response.json()


def test_structure_is_current_when_computed_from_the_latest_full_series(client, session):
    instrument = stock(session, "600001")
    bars = wave_bars(MIN_ANALYSIS_BARS)
    save(session, instrument, bars)
    add_run(session, instrument, structure_snapshot={
        "bi_list": [], "end_date": bars[-1].bar_time.strftime("%Y/%m/%d"), "analysis_bar_count": len(bars),
        "algorithm_version": CHAN_ALGORITHM_VERSION,
    })

    body = structure(client, instrument)

    assert body["is_stale"] is False
    assert body["available_bar_count"] == len(bars)


def test_structure_from_an_older_engine_version_is_recomputed(client, session):
    instrument = stock(session, "600001")
    bars = wave_bars(MIN_ANALYSIS_BARS)
    save(session, instrument, bars)
    add_run(session, instrument, structure_snapshot={
        "bi_list": [], "end_date": bars[-1].bar_time.strftime("%Y/%m/%d"), "analysis_bar_count": len(bars),
        "algorithm_version": "chan-core-v2-memory-1",
    })

    assert structure(client, instrument)["is_stale"] is True


def test_structure_goes_stale_when_newer_bars_arrive(client, session):
    instrument = stock(session, "600001")
    bars = wave_bars(MIN_ANALYSIS_BARS)
    save(session, instrument, bars)
    add_run(session, instrument, structure_snapshot={
        "bi_list": [], "end_date": bars[-2].bar_time.strftime("%Y/%m/%d"), "analysis_bar_count": len(bars) - 1,
    })

    assert structure(client, instrument)["is_stale"] is True


def test_structure_goes_stale_when_backfill_lengthens_history_without_new_dates(client, session):
    # The 002230 case: 22 bars analysed, then history repaired up to the same last date.
    instrument = stock(session, "600001")
    bars = wave_bars(MIN_ANALYSIS_BARS)
    save(session, instrument, bars)
    add_run(session, instrument, structure_snapshot={
        "bi_list": [], "end_date": bars[-1].bar_time.strftime("%Y/%m/%d"), "analysis_bar_count": 22,
    })

    assert structure(client, instrument)["is_stale"] is True


def test_runs_from_before_structure_snapshot_still_load_and_get_recomputed(client, session):
    instrument = stock(session, "600001")
    bars = wave_bars(MIN_ANALYSIS_BARS)
    save(session, instrument, bars)
    add_run(session, instrument, input_snapshot={
        "bi_list": [{"idx": 1}], "end_date": bars[-1].bar_time.strftime("%Y/%m/%d"),
    })

    body = structure(client, instrument)

    assert body["analysis"]["bi_list"] == [{"idx": 1}]
    assert body["is_stale"] is True


def test_compact_ai_input_is_never_returned_as_chart_structure(client, session):
    instrument = stock(session, "600001")
    save(session, instrument, wave_bars(MIN_ANALYSIS_BARS))
    add_run(session, instrument, input_snapshot={"recent_strokes": [{"idx": 1}], "end_date": "2026/01/01"})

    body = structure(client, instrument)

    # A missing structure makes the page compute one instead of drawing an empty chart.
    assert body["analysis"] is None
    assert body["is_stale"] is False


def test_refresh_route_can_still_use_an_explicit_window(client, session):
    instrument = stock(session, "600001")
    session.commit()

    response = client.post(f"/api/v1/instruments/{instrument.id}/refresh?lookback_days=30&force=true")

    assert response.status_code == 202
    payload = session.get(Job, response.json()["job_id"]).payload
    assert payload["auto_backfill"] is False
    assert payload["lookback_days"] == 30
