"""Audit stored daily bars against AKShare (Eastmoney).

python -m backend.app.bar_audit_cli [--repair]
    Moves beyond the price limit. With --repair, confirmed series are marked
    stale and one data.refresh job re-downloads them from the primary source.

python -m backend.app.bar_audit_cli --suspensions [--codes 920058 ...]
    Gaps the refresh keeps reporting (by default, those of the latest daily
    prepare). Days AKShare also has no trading on are written as suspension
    bars, so the refresh stops asking for them.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from backend.app.core.config import get_settings
from backend.app.db.models import Instrument, Job
from backend.app.db.session import SessionLocal
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services.bar_access import preferred_adjustment
from backend.app.services.bar_audit import (
    confirm_with_other_source,
    fill_confirmed_suspensions,
    find_beyond_limit_jumps,
    is_known_source_error,
    series_gaps,
)
from backend.app.services.data_health import expected_trade_date


def _latest_gap_codes(session) -> list[str]:
    for job in session.query(Job).filter(
        Job.kind.in_(("daily.prepare", "data.refresh")), Job.status.in_(("completed", "partial"))
    ).order_by(Job.created_at.desc()).limit(5):
        result = job.result or {}
        market = (result.get("stages") or {}).get("market_data") or result
        details = market.get("gap_details") if isinstance(market, dict) else None
        if details is not None:
            return [str(item["code"]) for item in details]
    return []


def suspensions(codes: list[str] | None) -> dict:
    from backend.app.providers.market_data import AkShareMarketDataProvider

    provider = AkShareMarketDataProvider()
    filled: dict[str, list[str]] = {}
    unconfirmed: dict[str, list[str]] = {}
    with SessionLocal() as session:
        end = expected_trade_date(session) or date.today()
        for code in codes or _latest_gap_codes(session):
            instrument = session.query(Instrument).filter(Instrument.code == code).one_or_none()
            if instrument is None:
                continue
            gaps = series_gaps(session, instrument, end=end)
            if not gaps:
                continue
            try:
                bars = provider.fetch_bars(
                    ts_code=instrument.ts_code or code, timeframe="DAY",
                    adjustment=preferred_adjustment(instrument.exchange),
                    start_date=min(gaps) - timedelta(days=10), end_date=max(gaps) + timedelta(days=10),
                )
            except Exception:
                unconfirmed[code] = [day.isoformat() for day in gaps]
                continue
            traded = {bar.bar_time.date() for bar in bars}
            if not traded:
                # An empty answer proves nothing about suspension.
                unconfirmed[code] = [day.isoformat() for day in gaps]
                continue
            written = fill_confirmed_suspensions(session, instrument, gaps, traded)
            session.commit()
            if written:
                filled[code] = [day.isoformat() for day in written]
            left = [day.isoformat() for day in gaps if day not in written]
            if left:
                unconfirmed[code] = left
    return {"filled_as_suspended": filled, "still_missing": unconfirmed}


def run() -> None:
    parser = argparse.ArgumentParser(prog="chan-bar-audit", description="Find broken daily-bar series")
    parser.add_argument("--years", type=int, default=5, help="how far back to check (default 5)")
    parser.add_argument("--repair", action="store_true", help="mark confirmed series stale and queue a refresh")
    parser.add_argument("--suspensions", action="store_true", help="fill gaps AKShare confirms as suspensions")
    parser.add_argument("--codes", nargs="*", help="limit --suspensions to these codes")
    arguments = parser.parse_args()
    if arguments.suspensions:
        print(json.dumps(suspensions(arguments.codes), ensure_ascii=False, indent=2))
        return
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

        confirmed = confirm_with_other_source(suspects, fetch_closes)
        broken = [jump for jump in confirmed if not is_known_source_error(jump)]
        report = {
            "suspects": len(suspects),
            "broken": [jump.as_dict() for jump in broken],
            "known_source_errors": [jump.as_dict() for jump in confirmed if is_known_source_error(jump)],
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
