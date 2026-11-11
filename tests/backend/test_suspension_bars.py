from __future__ import annotations

from datetime import datetime, timedelta

from backend.app.db.models import Instrument
from backend.app.providers.market_data import MarketBar
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.services.bar_access import analysis_series_summary, load_daily_bars
from backend.app.services.chan_analysis import DatabaseChanEngine


class RecordingCore:
    algorithm_version = "recording"

    def __init__(self):
        self.bars = []

    def analyze_bars(self, code, bars):
        self.bars = bars
        return {"buy_signals": [], "sell_signals": [], "end_date": bars[-1].bar_time.strftime("%Y/%m/%d")}


def test_suspension_days_are_not_fed_to_the_chan_engine(session):
    instrument = Instrument(code="000838", ts_code="000838.SZ", exchange="SZSE", name="停牌股", status="active")
    session.add(instrument)
    session.flush()
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    bars = [MarketBar(start + timedelta(days=i), 10 + i, 11 + i, 9 + i, 10.5 + i, 1000, 10_000) for i in range(10)]
    # BaoStock writes a flat zero-volume bar for each suspended day.
    bars += [MarketBar(start + timedelta(days=i), 19.5, 19.5, 19.5, 19.5, 0, 0) for i in range(10, 15)]
    bars.append(MarketBar(start + timedelta(days=15), 20, 21, 19, 20.5, 1000, 10_000))
    repository = IngestionRepository(session)
    source = repository.data_source("baostock", "baostock")
    repository.upsert_bars(instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ",
                           data_source_id=source.id, bars=bars)
    session.commit()

    core = RecordingCore()
    DatabaseChanEngine(session, core=core).analyze("000838")

    assert len(core.bars) == 11
    assert all(bar.volume > 0 for bar in core.bars)
    count, latest = analysis_series_summary(session, instrument.id)
    assert count == 11
    assert latest == (start + timedelta(days=15)).date()
    # Other readers still see the full calendar series.
    assert len(load_daily_bars(session, instrument.id)[0]) == 16


def test_worker_start_closes_ingestion_runs_left_running_by_a_stopped_worker(session):
    from backend.app.db.models import IngestionRun, Job
    from backend.app.db.models.common import utcnow

    stale = IngestionRun(kind="bars", status="running", started_at=utcnow() - timedelta(days=6))
    session.add(stale)
    session.commit()
    repository = IngestionRepository(session)

    # A live job elsewhere may still own the run: leave it alone.
    live = Job(kind="data.refresh", status="running", payload={}, heartbeat_at=utcnow())
    session.add(live)
    session.commit()
    assert repository.close_abandoned_runs(live_job_since=utcnow() - timedelta(minutes=5)) == 0

    live.status = "completed"
    session.commit()
    assert repository.close_abandoned_runs(live_job_since=utcnow() - timedelta(minutes=5)) == 1
    session.refresh(stale)
    assert stale.status == "interrupted"
    assert stale.finished_at is not None
