"""Rebuild daily market radar snapshots from saved bars: python -m backend.app.radar_backfill_cli --start 2026-01-02

Every open session in the range is computed with the current algorithm from
the bars in the database and saved under the current version; snapshots of
other versions are left alone. Snapshots computed this way are marked as
backfilled wherever they are shown, because they were not available at the
time.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any

from backend.app.db.session import SessionLocal
from backend.app.services.data_health import expected_trade_date
from backend.app.services.market_radar import ALGORITHM_VERSION, MarketRadarService


def run() -> None:
    parser = argparse.ArgumentParser(prog="chan-radar-backfill", description="Rebuild market radar snapshots")
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="first trade date, YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, help="last trade date (default: latest settled session)")
    arguments = parser.parse_args()

    def progress(trade_date: date, snapshot: dict[str, Any]) -> None:
        regime = snapshot["regime"]
        print(f"{trade_date} {regime['score']:5.1f} {regime['status_label']}", flush=True)

    with SessionLocal() as session:
        end = arguments.end or expected_trade_date(session) or date.today()
        computed = MarketRadarService(session).backfill(arguments.start, end, progress)
    print(f"radar {ALGORITHM_VERSION}: {len(computed)} sessions from {arguments.start} to {end}", file=sys.stderr)


if __name__ == "__main__":
    run()
