"""What happened after the buy signals the scans actually saved (signal outcomes v0).

Only signals stored by past scans are measured. Recomputing old signals with today's engine
would use bars that did not exist yet, because Chan buy points change as later bars arrive.

Each signal is entered at the close of the day the scan's data ended (the first day it could be
known), not on the signal date, and held for 5/10/20 open sessions. The benchmark is the median
return of every stock over the same sessions. Both prices come from one stored series, so a
forward-adjusted base change since then cancels out.

Groups with fewer than MIN_SAMPLE signals, or entered on fewer than MIN_ENTRY_DAYS days, show
only their counts. Nothing here accounts for
trading costs, limit-up days that could not be bought or survivorship, so this is a research
check of the tool, not evidence that a signal makes money.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from datetime import time as dt_time
from statistics import mean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Bar, Instrument, Job, RadarSnapshot, ScanResult, TradingCalendar
from backend.app.services.bar_access import preferred_bar_condition
from backend.app.services.data_health import expected_trade_date
from backend.app.services.today_candidates import (
    BUY_SCAN_KINDS,
    FINISHED_STATUSES,
    LOCAL_ZONE,
    SIGNAL_WINDOW_SESSIONS,
    _data_as_of,
    _is_buy_scan,
    _local_date,
)

HORIZONS = (5, 10, 20)
MIN_SAMPLE = 30
MIN_ENTRY_DAYS = 5
ALL = "全部"


def _parse_day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


class SignalOutcomeService:
    def __init__(self, session: Session):
        self.session = session
        self._sessions: list[date] = []
        self._index: dict[date, int] = {}
        self._closes: dict[date, dict[int, tuple[int | None, float]]] = {}
        self._benchmarks: dict[tuple[date, date], float | None] = {}

    def build(self, horizons: Iterable[int] = HORIZONS) -> dict[str, Any]:
        horizons = tuple(horizons)
        self._sessions = list(self.session.scalars(
            select(TradingCalendar.trade_date)
            .where(TradingCalendar.is_open.is_(True))
            .distinct()
            .order_by(TradingCalendar.trade_date)
        ).all())
        self._index = {day: position for position, day in enumerate(self._sessions)}
        last_bar = self.session.scalar(select(Bar.bar_time).where(Bar.timeframe == "DAY").order_by(Bar.bar_time.desc()).limit(1))
        last_day = last_bar.date() if last_bar else None

        events = self._signal_events()
        radar = self._radar_rows()
        needed: set[date] = set()
        for start in {event["known_date"] for event in events} | {row["trade_date"] for row in radar}:
            needed.add(start)
            for horizon in horizons:
                exit_day = self._exit_day(start, horizon, last_day)
                if exit_day:
                    needed.add(exit_day)
        self._load_closes(needed)

        outcomes = [self._outcome(event, horizons, last_day) for event in events]
        return {
            "data_through": last_day.isoformat() if last_day else None,
            "horizons": list(horizons),
            "min_sample": MIN_SAMPLE,
            "min_entry_days": MIN_ENTRY_DAYS,
            "method": {
                "entry": "扫描数据截止日收盘",
                "exit": "其后第 N 个交易日收盘",
                "benchmark": "同一区间全市场个股收益中位数（等权）",
                "fresh_sessions": SIGNAL_WINDOW_SESSIONS,
            },
            "signals": len(events),
            "groups": {
                "overall": self._groups(outcomes, horizons, lambda _item: ALL),
                "signal_type": self._groups(outcomes, horizons, lambda item: str(item["signal_type"])),
                "freshness": self._groups(
                    outcomes, horizons,
                    lambda item: "近 3 个交易日内" if item["age_sessions"] < SIGNAL_WINDOW_SESSIONS else "更早",
                ),
                "known_date": self._groups(outcomes, horizons, lambda item: item["known_date"]),
            },
            "radar": self._radar(radar, horizons, last_day),
        }

    # Signals ---------------------------------------------------------------------------------

    def _known_date(self, job: Job) -> date | None:
        as_of = _data_as_of(job)
        if as_of:
            return as_of
        # Older scans did not record their data date; use the last session settled when they ran.
        moment = job.finished_at or job.created_at
        if moment is None:
            return None
        aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
        return expected_trade_date(self.session, aware.astimezone(LOCAL_ZONE))

    def _signal_events(self) -> list[dict[str, Any]]:
        jobs = [
            job for job in self.session.scalars(
                select(Job).where(Job.kind.in_(BUY_SCAN_KINDS), Job.status.in_(FINISHED_STATUSES))
            ).all()
            if _is_buy_scan(job)
        ]
        known = {job.id: self._known_date(job) for job in jobs}
        kinds = {job.id: job.kind for job in jobs}
        events: dict[tuple[Any, ...], dict[str, Any]] = {}
        rows = self.session.execute(
            select(ScanResult, Instrument.code)
            .outerjoin(Instrument, Instrument.id == ScanResult.instrument_id)
            .where(ScanResult.job_id.in_(list(known)))
        ).all() if known else []
        for result, code in rows:
            known_date = known.get(result.job_id)
            if known_date is None or result.instrument_id is None:
                continue
            for signal in (result.payload or {}).get("signals") or []:
                signal_date = _parse_day(signal.get("date"))
                if not signal.get("is_buy") or signal_date is None or signal_date > known_date:
                    continue
                key = (result.instrument_id, str(signal.get("type")), signal_date)
                existing = events.get(key)
                if existing is not None and existing["known_date"] <= known_date:
                    if kinds[result.job_id] not in existing["scan_kinds"]:
                        existing["scan_kinds"].append(kinds[result.job_id])
                    continue
                events[key] = {
                    "instrument_id": result.instrument_id,
                    "code": code or (result.payload or {}).get("code"),
                    "signal_type": key[1],
                    "signal_date": signal_date,
                    "known_date": known_date,
                    "age_sessions": self._sessions_between(signal_date, known_date),
                    "scan_kinds": [kinds[result.job_id]],
                }
        return list(events.values())

    def _sessions_between(self, earlier: date, later: date) -> int:
        if earlier in self._index and later in self._index:
            return self._index[later] - self._index[earlier]
        return sum(1 for day in self._sessions if earlier < day <= later)

    # Prices ----------------------------------------------------------------------------------

    def _exit_day(self, start: date, horizon: int, last_day: date | None) -> date | None:
        position = self._index.get(start)
        if position is None or position + horizon >= len(self._sessions):
            return None
        exit_day = self._sessions[position + horizon]
        return exit_day if last_day and exit_day <= last_day else None

    def _load_closes(self, days: set[date]) -> None:
        self._closes = defaultdict(dict)
        if not days:
            return
        rows = self.session.execute(
            select(Bar.instrument_id, Bar.bar_time, Bar.close, Bar.data_source_id)
            .join(Instrument, Instrument.id == Bar.instrument_id)
            .where(
                Bar.timeframe == "DAY",
                Bar.quality_status == "ok",
                preferred_bar_condition(),
                Bar.bar_time.in_([datetime.combine(day, dt_time.min) for day in days]),
            )
        ).all()
        for instrument_id, bar_time, close, source_id in rows:
            if close and close > 0:
                self._closes[bar_time.date()][instrument_id] = (source_id, float(close))

    def _return(self, instrument_id: int, start: date, exit_day: date) -> float | None:
        entry = self._closes.get(start, {}).get(instrument_id)
        leave = self._closes.get(exit_day, {}).get(instrument_id)
        # Two sources carry different adjustment bases, so their prices are not comparable.
        if entry is None or leave is None or entry[0] != leave[0]:
            return None
        return leave[1] / entry[1] - 1

    def _benchmark(self, start: date, exit_day: date) -> float | None:
        key = (start, exit_day)
        if key not in self._benchmarks:
            returns = [
                value for value in (
                    self._return(instrument_id, start, exit_day) for instrument_id in self._closes.get(start, {})
                ) if value is not None
            ]
            self._benchmarks[key] = median(returns) if returns else None
        return self._benchmarks[key]

    def _outcome(self, event: dict[str, Any], horizons: tuple[int, ...], last_day: date | None) -> dict[str, Any]:
        results: dict[int, dict[str, Any]] = {}
        for horizon in horizons:
            exit_day = self._exit_day(event["known_date"], horizon, last_day)
            if exit_day is None:
                results[horizon] = {"state": "pending"}
                continue
            value = self._return(event["instrument_id"], event["known_date"], exit_day)
            benchmark = self._benchmark(event["known_date"], exit_day)
            if value is None or benchmark is None:
                results[horizon] = {"state": "unusable"}
                continue
            results[horizon] = {"state": "measured", "return": value, "excess": value - benchmark}
        return {**event, "known_date": event["known_date"].isoformat(), "results": results}

    # Aggregation -----------------------------------------------------------------------------

    @staticmethod
    def _summary(items: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
        states = [item["results"][horizon]["state"] for item in items]
        measured = [item["results"][horizon] for item in items if item["results"][horizon]["state"] == "measured"]
        entry_days = {item["known_date"] for item in items if item["results"][horizon]["state"] == "measured"}
        summary: dict[str, Any] = {
            "measured": len(measured),
            "entry_days": len(entry_days),
            "pending": states.count("pending"),
            "unusable": states.count("unusable"),
            # Signals entered on one day share that day's market, so many of them are still one sample.
            "sufficient": len(measured) >= MIN_SAMPLE and len(entry_days) >= MIN_ENTRY_DAYS,
        }
        if summary["sufficient"]:
            returns = [value["return"] for value in measured]
            excess = [value["excess"] for value in measured]
            summary.update({
                "mean_return": mean(returns),
                "median_return": median(returns),
                "win_rate": sum(1 for value in returns if value > 0) / len(returns),
                "median_excess": median(excess),
                "beat_market_rate": sum(1 for value in excess if value > 0) / len(excess),
            })
        return summary

    def _groups(self, outcomes: list[dict[str, Any]], horizons: tuple[int, ...], key) -> list[dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in outcomes:
            grouped[key(item)].append(item)
        return [
            {"group": name, "signals": len(items), "horizons": {str(h): self._summary(items, h) for h in horizons}}
            for name, items in sorted(grouped.items())
        ]

    # Radar -----------------------------------------------------------------------------------

    def _radar_rows(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.session.scalars(select(RadarSnapshot)).all():
            calculated = _local_date(row.calculated_at)
            rows.append({
                "trade_date": row.trade_date,
                "algorithm_version": row.algorithm_version,
                "status_label": row.status_label or row.status,
                # Computed two or more sessions later: it could only use bars up to its trade date,
                # but it was not what the page showed that evening.
                "backfilled": bool(calculated and self._sessions_between(row.trade_date, calculated) > 1),
            })
        return rows

    def _radar(self, rows: list[dict[str, Any]], horizons: tuple[int, ...], last_day: date | None) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[(row["algorithm_version"], row["status_label"])].append(row)
        report = []
        for (version, label), items in sorted(grouped.items()):
            entry: dict[str, Any] = {
                "algorithm_version": version,
                "status_label": label,
                "days": len(items),
                "backfilled_days": sum(1 for item in items if item["backfilled"]),
                "horizons": {},
            }
            for horizon in horizons:
                medians = []
                for item in items:
                    exit_day = self._exit_day(item["trade_date"], horizon, last_day)
                    value = self._benchmark(item["trade_date"], exit_day) if exit_day else None
                    if value is not None:
                        medians.append(value)
                summary: dict[str, Any] = {"measured_days": len(medians), "sufficient": len(medians) >= MIN_SAMPLE}
                if summary["sufficient"]:
                    summary.update({
                        "mean_market_median": mean(medians),
                        "up_rate": sum(1 for value in medians if value > 0) / len(medians),
                    })
                entry["horizons"][str(horizon)] = summary
            report.append(entry)
        return report
