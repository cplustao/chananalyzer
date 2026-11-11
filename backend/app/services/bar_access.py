from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from backend.app.db.models import Bar, Instrument

# Daily bars the Chan engine reads, and the fewest it may read before its output
# is reported as insufficient. Roughly six months of sessions: a shorter series
# yields a handful of strokes and no centers, which looks like "no signal".
ANALYSIS_WINDOW_DAYS = 1825
MIN_ANALYSIS_BARS = 120
# Instruments listed more recently than this cannot have MIN_ANALYSIS_BARS yet.
MATURE_LISTING_DAYS = 200


def preferred_adjustment(exchange: str | None) -> str:
    """Return the best available, honestly labelled daily-bar adjustment."""

    return "NONE" if str(exchange or "").upper() == "BJ" else "QFQ"


def preferred_bar_condition(bar: Any = Bar):
    """SQL condition selecting one canonical series per exchange."""

    return or_(
        and_(Instrument.exchange == "BJ", bar.adjustment == "NONE"),
        and_(
            or_(Instrument.exchange.is_(None), Instrument.exchange != "BJ"),
            bar.adjustment == "QFQ",
        ),
    )


def traded_bar_condition(bar: Any = Bar):
    """Exclude suspension days.

    BaoStock keeps a flat, zero-volume bar for each day a stock is suspended;
    other sources omit the day. Those bars are not trading, and a long
    suspension would otherwise insert a run of flat K-lines into the structure.
    """

    return or_(bar.volume.is_(None), bar.volume > 0)


def analysis_window_start(today: date | None = None) -> datetime:
    return datetime.combine((today or date.today()) - timedelta(days=ANALYSIS_WINDOW_DAYS), time.min)


def analysis_series_summary(session: Session, instrument_id: int) -> tuple[int, date | None]:
    """Return the size and last date of the exact series the Chan engine would read."""

    exchange = session.scalar(select(Instrument.exchange).where(Instrument.id == instrument_id))
    conditions = [
        Bar.instrument_id == instrument_id,
        Bar.timeframe == "DAY",
        Bar.adjustment == preferred_adjustment(exchange),
        Bar.quality_status == "ok",
    ]
    source_id = session.scalar(
        select(Bar.data_source_id).where(*conditions).order_by(Bar.bar_time.desc()).limit(1)
    )
    count, latest = session.execute(
        select(func.count(Bar.id), func.max(Bar.bar_time)).where(
            *conditions,
            Bar.data_source_id == source_id,
            Bar.bar_time >= analysis_window_start(),
            traded_bar_condition(),
        )
    ).one()
    return int(count or 0), latest.date() if latest else None


def analysis_series_coverage(session: Session, *, today: date | None = None) -> dict[str, Any]:
    """Measure how many mature stocks have a series long enough for Chan analysis.

    Mirrors ``load_daily_bars``: only the bars of each instrument's newest provider
    count, because that is all the engine reads. Having a bar on the latest trading
    day says nothing about this; most of a series can sit on an older provider.
    """

    current = today or date.today()
    eligibility = (
        Instrument.status == "active",
        func.length(Instrument.code) == 6,
        or_(
            Instrument.list_date.is_(None),
            Instrument.list_date <= current - timedelta(days=MATURE_LISTING_DAYS),
        ),
    )
    eligible_count = int(session.scalar(select(func.count(Instrument.id)).where(*eligibility)) or 0)

    latest = (
        select(Bar.instrument_id.label("instrument_id"), func.max(Bar.bar_time).label("bar_time"))
        .join(Instrument, Instrument.id == Bar.instrument_id)
        .where(*eligibility, Bar.timeframe == "DAY", Bar.quality_status == "ok", preferred_bar_condition())
        .group_by(Bar.instrument_id)
        .subquery()
    )
    newest_bar = aliased(Bar)
    newest = (
        select(latest.c.instrument_id.label("instrument_id"), newest_bar.data_source_id.label("source_id"))
        .join(
            newest_bar,
            and_(
                newest_bar.instrument_id == latest.c.instrument_id,
                newest_bar.bar_time == latest.c.bar_time,
                newest_bar.timeframe == "DAY",
                newest_bar.quality_status == "ok",
            ),
        )
        .join(Instrument, Instrument.id == latest.c.instrument_id)
        .where(preferred_bar_condition(newest_bar))
        .subquery()
    )
    usable = (
        select(Bar.instrument_id)
        .join(
            newest,
            and_(
                newest.c.instrument_id == Bar.instrument_id,
                Bar.data_source_id.is_not_distinct_from(newest.c.source_id),
            ),
        )
        .join(Instrument, Instrument.id == Bar.instrument_id)
        .where(
            Bar.timeframe == "DAY",
            Bar.quality_status == "ok",
            Bar.bar_time >= analysis_window_start(current),
            preferred_bar_condition(),
            traded_bar_condition(),
        )
        .group_by(Bar.instrument_id)
        .having(func.count(Bar.id) >= MIN_ANALYSIS_BARS)
        .subquery()
    )
    usable_count = int(session.scalar(select(func.count()).select_from(usable)) or 0)
    return {
        "eligible_count": eligible_count,
        "usable_count": usable_count,
        "insufficient_count": max(0, eligible_count - usable_count),
        "coverage_rate": usable_count / eligible_count if eligible_count else 0.0,
        "min_analysis_bars": MIN_ANALYSIS_BARS,
    }


def load_daily_bars(
    session: Session,
    instrument_id: int,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int | None = None,
    descending: bool = False,
    quality_only: bool = True,
    traded_only: bool = False,
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
    if traded_only:
        conditions.append(traded_bar_condition())
    statement = select(Bar).where(*conditions).order_by(
        Bar.bar_time.desc() if descending else Bar.bar_time
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement).all()), adjustment
