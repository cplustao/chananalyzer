from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.app.db.models import Bar, Instrument


def preferred_adjustment(exchange: str | None) -> str:
    """Return the best available, honestly labelled daily-bar adjustment."""

    return "NONE" if str(exchange or "").upper() == "BJ" else "QFQ"


def preferred_bar_condition():
    """SQL condition selecting one canonical series per exchange."""

    return or_(
        and_(Instrument.exchange == "BJ", Bar.adjustment == "NONE"),
        and_(
            or_(Instrument.exchange.is_(None), Instrument.exchange != "BJ"),
            Bar.adjustment == "QFQ",
        ),
    )


def load_daily_bars(
    session: Session,
    instrument_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
    descending: bool = False,
    quality_only: bool = True,
) -> tuple[list[Bar], str]:
    exchange = session.scalar(select(Instrument.exchange).where(Instrument.id == instrument_id))
    adjustment = preferred_adjustment(exchange)
    source_conditions = [
        Bar.instrument_id == instrument_id,
        Bar.timeframe == "DAY",
        Bar.adjustment == adjustment,
    ]
    if end is not None:
        source_conditions.append(Bar.bar_time <= end)
    if quality_only:
        source_conditions.append(Bar.quality_status == "ok")
    # Pick the provider of the newest usable bar first, then keep the whole
    # requested research window on that provider. Older fallback data may stay
    # in the audit trail, but it is never silently stitched into one series.
    source_id = session.scalar(
        select(Bar.data_source_id)
        .where(*source_conditions)
        .order_by(Bar.bar_time.desc())
        .limit(1)
    )
    conditions = [*source_conditions, Bar.data_source_id == source_id]
    if start is not None:
        conditions.append(Bar.bar_time >= start)
    statement = select(Bar).where(*conditions).order_by(
        Bar.bar_time.desc() if descending else Bar.bar_time
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all()), adjustment
