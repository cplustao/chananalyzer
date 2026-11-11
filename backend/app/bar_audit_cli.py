"""Audit stored daily bars for moves beyond the price limit: python -m backend.app.bar_audit_cli [--repair]

Suspects are checked against AKShare (Eastmoney). With --repair, the confirmed
series are marked stale and one data.refresh job is queued for them, so the
worker downloads each whole series again from the primary source.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from backend.app.core.config import get_settings
from backend.app.db.models import Instrument
from backend.app.db.session import SessionLocal
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services.bar_access import preferred_adjustment
from backend.app.services.bar_audit import confirm_with_other_source, find_beyond_limit_jumps


def run() -> None:
    parser = argparse.ArgumentParser(prog="chan-bar-audit", description="Find broken daily-bar series")
    parser.add_argument("--years", type=int, default=5, help="how far back to check (default 5)")
    parser.add_argument("--repair", action="store_true", help="mark confirmed series stale and queue a refresh")
    arguments = parser.parse_args()
    from backend.app.providers.market_data import AkShareMarketDataProvider

    provider = AkShareMarketDataProvider()
    with SessionLocal() as session:
        suspects = find_beyond_limit_jumps(session, since=date.today() - timedelta(days=365 * arguments.years))
        instruments = {
            item.code: item
            for item in session.query(Instrument).filter(Instrument.code.in_({jump.code for jump in suspects}))
        }

        def fetch_closes(code: str, start: date, end: date) -> dict[date, float]:
            instrument = instruments[code]
            try:
                bars = provider.fetch_bars(
                    ts_code=instrument.ts_code or code, timeframe="DAY",
                    adjustment=preferred_adjustment(instrument.exchange), start_date=start, end_date=end,
                )
            except Exception:
                return {}
            return {bar.bar_time.date(): bar.close for bar in bars}

        broken = confirm_with_other_source(suspects, fetch_closes)
        report = {
            "suspects": len(suspects),
            "broken": [jump.as_dict() for jump in broken],
            "repair_job_id": None,
        }
        if arguments.repair and broken:
            repository = IngestionRepository(session)
            for code in sorted({jump.code for jump in broken}):
                instrument = instruments[code]
                repository.mark_series_stale(
                    instrument_id=instrument.id, timeframe="DAY", adjustment=preferred_adjustment(instrument.exchange)
                )
            job, _ = JobRepository(session).create(
                "data.refresh", {"codes": sorted({jump.code for jump in broken})}, None, False,
                get_settings().worker_max_attempts,
            )
            session.commit()
            report["repair_job_id"] = job.id
        else:
            session.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    run()
