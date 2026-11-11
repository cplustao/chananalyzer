"""Find daily bars that move further than the exchange allows, and check them against another source.

A move beyond the daily price limit can only be real on a few days: the first
sessions after listing and the first day of a delisting period (excluded
here). Everywhere else it points at a stored series problem, typically a
forward-adjusted history left on an old ex-rights base, or a single bad bar.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

FIRST_SESSIONS_WITHOUT_LIMIT = 5
# Allowed gap between the stored move and the other source's move on the same day.
VERIFY_TOLERANCE_POINTS = 2.0


@dataclass(frozen=True)
class Jump:
    instrument_id: int
    code: str
    name: str
    day: date
    change_pct: float
    other_change_pct: float | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["day"] = self.day.isoformat()
        return value


def price_limit(code: str, exchange: str | None) -> float:
    if str(exchange or "").upper() == "BJ":
        return 0.30
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    return 0.10


def find_beyond_limit_jumps(session: Session, *, since: date) -> list[Jump]:
    rows = session.execute(
        text(
            """
            with series as (
              select b.instrument_id, i.code, i.name, i.exchange, b.bar_time, b.close,
                     lag(b.close) over (partition by b.instrument_id order by b.bar_time) as prev_close,
                     row_number() over (partition by b.instrument_id order by b.bar_time) as n
              from bars b join instruments i on i.id = b.instrument_id
              where b.timeframe = 'DAY' and b.quality_status = 'ok' and b.bar_time >= :since
                and i.status = 'active' and length(i.code) = 6
                and ((i.exchange = 'BJ' and b.adjustment = 'NONE')
                     or (coalesce(i.exchange, '') != 'BJ' and b.adjustment = 'QFQ'))
            )
            select instrument_id, code, name, exchange, bar_time, prev_close, close, n
            from series where prev_close > 0 and abs(close / prev_close - 1) > 0.1
            """
        ),
        {"since": datetime.combine(since, datetime.min.time())},
    ).fetchall()
    jumps: list[Jump] = []
    for row in rows:
        name = str(row.name or "")
        if "退" in name or row.n <= FIRST_SESSIONS_WITHOUT_LIMIT:
            continue
        change = row.close / row.prev_close - 1
        # Prices are quoted in cents, so a one-tick rounding can exceed the limit on cheap stocks.
        allowance = max(0.006, 0.011 / float(row.prev_close))
        if abs(change) <= price_limit(row.code, row.exchange) + allowance:
            continue
        bar_time = row.bar_time if isinstance(row.bar_time, datetime) else datetime.fromisoformat(str(row.bar_time))
        jumps.append(Jump(int(row.instrument_id), row.code, name, bar_time.date(), round(change * 100, 2)))
    return jumps


CloseFetcher = Callable[[str, date, date], dict[date, float]]


def confirm_with_other_source(jumps: Iterable[Jump], fetch_closes: CloseFetcher) -> list[Jump]:
    """Keep the jumps the other source does not show; fetch errors keep the jump unconfirmed."""
    broken: list[Jump] = []
    by_code: dict[str, list[Jump]] = {}
    for jump in jumps:
        by_code.setdefault(jump.code, []).append(jump)
    for code, items in by_code.items():
        start = min(item.day for item in items) - timedelta(days=20)
        end = max(item.day for item in items)
        closes = fetch_closes(code, start, end)
        days = sorted(closes)
        for jump in items:
            previous = [day for day in days if day < jump.day]
            if jump.day not in closes or not previous or not closes[previous[-1]]:
                continue
            other = (closes[jump.day] / closes[previous[-1]] - 1) * 100
            if abs(other - jump.change_pct) > VERIFY_TOLERANCE_POINTS:
                broken.append(Jump(jump.instrument_id, jump.code, jump.name, jump.day, jump.change_pct, round(other, 2)))
    return broken
