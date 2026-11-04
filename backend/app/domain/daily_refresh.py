"""Plan daily-bar repairs using exchange sessions, never guessed weekdays."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

INITIAL_HISTORY_DAYS = 730
OVERLAP_SESSIONS = 3
# A refetched close differing by more than this means the adjusted base moved, not rounding.
REBASE_TOLERANCE_RATIO = 0.001
REBASE_TOLERANCE_ABSOLUTE = 0.011


@dataclass(frozen=True)
class DailyRefreshPlan:
    expected_dates: frozenset[date]
    ranges: tuple[tuple[date, date], ...]


def adjustment_base_changed(stored: Mapping[date, float], fetched: Mapping[date, float]) -> bool:
    """Detect a new ex-rights base by comparing refetched overlap sessions with saved closes.

    Adjusted prices are quoted against the latest factor at fetch time. After an ex-rights
    date, tail-only updates would join two bases with an artificial price gap, so a moved
    overlap means the whole saved series must be replaced.
    """
    for day, close in fetched.items():
        previous = stored.get(day)
        if previous is None:
            continue
        if abs(close - previous) > max(REBASE_TOLERANCE_ABSOLUTE, abs(previous) * REBASE_TOLERANCE_RATIO):
            return True
    return False


def plan_daily_refresh(
    *,
    open_dates: list[date],
    end: date,
    first_stored: date | None,
    saved_dates: set[date],
    list_date: date | None,
) -> DailyRefreshPlan:
    start = min(end - timedelta(days=INITIAL_HISTORY_DAYS), first_stored or end)
    if list_date is not None:
        start = max(start, list_date)
    elif first_stored is not None:
        # Without listing metadata, dates before the first observed bar cannot
        # be distinguished from pre-listing days. Still repair all interior gaps.
        start = first_stored
    expected = sorted({day for day in open_dates if start <= day <= end})
    if not expected:
        return DailyRefreshPlan(frozenset(), ())

    missing_indexes = {index for index, day in enumerate(expected) if day not in saved_dates}
    latest = max(saved_dates, default=end)
    overlap_end = bisect_right(expected, latest)
    selected = missing_indexes | set(range(max(0, overlap_end - OVERLAP_SESSIONS), len(expected)))
    # Include an adjacent observation around isolated holes. Providers that omit
    # suspension days can then return a valid window without inventing a bar.
    for index in missing_indexes:
        selected.update(neighbor for neighbor in (index - 1, index + 1) if 0 <= neighbor < len(expected))

    groups: list[tuple[int, int]] = []
    for index in sorted(selected):
        if groups and index == groups[-1][1] + 1:
            groups[-1] = (groups[-1][0], index)
        else:
            groups.append((index, index))
    # Bound requests for badly fragmented history, merging the smallest gaps.
    while len(groups) > 8:
        index = min(range(len(groups) - 1), key=lambda i: groups[i + 1][0] - groups[i][1])
        groups[index:index + 2] = [(groups[index][0], groups[index + 1][1])]
    return DailyRefreshPlan(
        frozenset(expected), tuple((expected[first], expected[last]) for first, last in groups)
    )
