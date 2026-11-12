"""One place that turns saved radar snapshots into executable positions.

The today page and the radar page used to build this separately: one fed
partial snapshots into the positions, the other only the most recent rows of
a page. Both now compute over the whole history of one algorithm version,
counting real trading sessions from the calendar.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import RadarSnapshot, TradingCalendar
from backend.app.domain.market_positions import build_executable_positions, risk_inputs_from_snapshot


def dedupe_rows(rows: list[RadarSnapshot]) -> list[RadarSnapshot]:
    """Keep the latest calculation for each trade date."""
    by_date: dict[object, RadarSnapshot] = {}
    for row in sorted(rows, key=lambda item: (item.trade_date, item.calculated_at)):
        by_date[row.trade_date] = row
    return list(by_date.values())


def _open_sessions(session: Session, start: date, end: date) -> list[date]:
    return list(
        session.scalars(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.is_open.is_(True),
                TradingCalendar.trade_date >= start,
                TradingCalendar.trade_date <= end,
            )
            .order_by(TradingCalendar.trade_date)
        )
    )


def backfilled(row: RadarSnapshot) -> bool:
    """Computed more than a day after its trade date, so it was not available at the time."""
    return (row.calculated_at.date() - row.trade_date).days > 3


def history_with_positions(session: Session, rows: list[RadarSnapshot]) -> list[dict[str, Any]]:
    summaries = [
        {
            "trade_date": row.trade_date.isoformat(),
            "algorithm_version": row.algorithm_version,
            "score": row.score,
            "status": row.status,
            "status_label": row.status_label,
            "freshness": row.freshness,
            "backfilled": backfilled(row),
            "component_scores": {
                str(component.get("key")): component.get("score")
                for component in ((row.snapshot or {}).get("components") or [])
                if isinstance(component, dict) and component.get("key")
            },
            "risk_inputs": risk_inputs_from_snapshot(row.snapshot),
            "calculated_at": row.calculated_at.isoformat(),
        }
        for row in dedupe_rows(rows)
    ]
    if not summaries:
        return []
    fresh_indexes = [index for index, item in enumerate(summaries) if item["freshness"] == "fresh"]
    sessions = _open_sessions(
        session, date.fromisoformat(summaries[0]["trade_date"]), date.fromisoformat(summaries[-1]["trade_date"])
    )
    positions = build_executable_positions([summaries[index] for index in fresh_indexes], sessions)
    positions_by_index = dict(zip(fresh_indexes, positions, strict=False))
    for index, item in enumerate(summaries):
        item["executable_position"] = positions_by_index.get(index)
    return summaries


def version_history(session: Session, algorithm_version: str) -> list[dict[str, Any]]:
    rows = list(
        session.scalars(
            select(RadarSnapshot)
            .where(RadarSnapshot.algorithm_version == algorithm_version)
            .order_by(RadarSnapshot.trade_date, RadarSnapshot.calculated_at)
        )
    )
    return history_with_positions(session, rows)
