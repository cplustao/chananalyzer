"""Today's short list: buy signals from scans of the expected trade date, newest first.

A comparison with the previous scan is not a condition for showing a stock. A scan that is partial,
first of its kind or based on older data is still shown, with its coverage and date stated, so the
page never claims "today" for something older.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Instrument, Job, ScanResult, TradingCalendar

TODAY_CANDIDATE_LIMIT = 20
SIGNAL_WINDOW_SESSIONS = 3
BUY_SCAN_KINDS = ("screen.smart", "screen.hot", "scan.buy")
FINISHED_STATUSES = ("completed", "partial")
ACTIVE_STATUSES = ("queued", "running")
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


def _is_buy_scan(job: Job) -> bool:
    if job.kind == "screen.smart":
        return str((job.payload or {}).get("scan_side") or "buy") == "buy"
    return job.kind in BUY_SCAN_KINDS


def _data_as_of(job: Job) -> date | None:
    raw = (job.result or {}).get("data_as_of")
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def _local_date(moment: datetime | None) -> date | None:
    if moment is None:
        return None
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return aware.astimezone(LOCAL_ZONE).date()


class TodayCandidateService:
    def __init__(self, session: Session):
        self.session = session

    def build(self, health: dict[str, Any] | None) -> dict[str, Any]:
        expected_raw = (health or {}).get("expected_trade_date")
        expected = date.fromisoformat(expected_raw) if expected_raw else None
        gate_passed = bool((health or {}).get("decision_usable"))
        recent = [
            job for job in self.session.scalars(
                select(Job)
                .where(Job.kind.in_(BUY_SCAN_KINDS), Job.status.in_(FINISHED_STATUSES + ACTIVE_STATUSES))
                .order_by(Job.created_at.desc())
                .limit(50)
            ).all()
            if _is_buy_scan(job)
        ]
        pending = [job.id for job in recent if job.status in ACTIVE_STATUSES]
        finished = [job for job in recent if job.status in FINISHED_STATUSES]

        current = [job for job in finished if expected and _data_as_of(job) == expected] if gate_passed else []
        if current:
            jobs = self._latest_per_kind(current)
            basis = expected
            reason = None
        elif finished:
            latest = finished[0]
            latest_as_of = _data_as_of(latest)
            # Scans of the same data date belong together, e.g. the hot and full-market scans of one day.
            jobs = (
                self._latest_per_kind([job for job in finished if _data_as_of(job) == latest_as_of])
                if latest_as_of
                else [latest]
            )
            basis = latest_as_of or _local_date(latest.finished_at or latest.created_at)
            reason = "gate_blocked" if not gate_passed else "stale_scan"
        else:
            jobs, basis = [], None
            reason = "gate_blocked" if not gate_passed else "no_scan"

        window = self._signal_window(basis) if basis else []
        items = self._candidates(jobs, set(window)) if window else []
        last_bar = basis.isoformat() if basis else None
        for item in items:
            # A signal on the last bar can still change once the next bars arrive.
            item["unconfirmed"] = item["signal_date"] == last_bar
        return {
            "is_current": bool(current),
            "reason": reason,
            "basis_trade_date": basis.isoformat() if basis else None,
            "expected_trade_date": expected.isoformat() if expected else None,
            "signal_window": {
                "start": window[0].isoformat() if window else None,
                "end": window[-1].isoformat() if window else None,
                "sessions": SIGNAL_WINDOW_SESSIONS,
            },
            "ordering": "信号日期从新到旧，同一天按现价距信号价由近到远",
            "limit": TODAY_CANDIDATE_LIMIT,
            "total": len(items),
            "items": items[:TODAY_CANDIDATE_LIMIT],
            "scans": [self._scan_view(job) for job in jobs],
            "pending_scan_job_ids": pending,
        }

    @staticmethod
    def _latest_per_kind(jobs: list[Job]) -> list[Job]:
        latest: dict[str, Job] = {}
        for job in jobs:  # already newest first
            latest.setdefault(job.kind, job)
        return list(latest.values())

    def _signal_window(self, basis: date) -> list[date]:
        sessions = self.session.scalars(
            select(TradingCalendar.trade_date)
            .where(TradingCalendar.is_open.is_(True), TradingCalendar.trade_date <= basis)
            .order_by(TradingCalendar.trade_date.desc())
            .limit(SIGNAL_WINDOW_SESSIONS)
        ).all()
        return sorted(sessions)

    def _candidates(self, jobs: list[Job], window: set[date]) -> list[dict[str, Any]]:
        if not jobs:
            return []
        kinds = {job.id: job.kind for job in jobs}
        rows = self.session.execute(
            select(ScanResult, Instrument.code, Instrument.name)
            .outerjoin(Instrument, Instrument.id == ScanResult.instrument_id)
            .where(ScanResult.job_id.in_(list(kinds)))
        ).all()
        by_stock: dict[str, dict[str, Any]] = {}
        for result, code, name in rows:
            payload = result.payload or {}
            signal = self._latest_buy_signal(payload.get("signals") or [], window)
            if signal is None:
                continue
            key = str(result.instrument_id or code or payload.get("code"))
            kind = kinds[result.job_id]
            existing = by_stock.get(key)
            if existing is not None:
                if kind not in existing["scan_kinds"]:
                    existing["scan_kinds"].append(kind)
                if signal["date"] <= existing["signal_date"]:
                    continue
            latest_price = payload.get("latest_price")
            signal_price = signal.get("price")
            distance = (
                (float(latest_price) / float(signal_price) - 1) * 100
                if latest_price and signal_price
                else None
            )
            by_stock[key] = {
                "instrument_id": result.instrument_id,
                "code": code or payload.get("code"),
                "name": name or payload.get("name"),
                "industry": payload.get("industry") or None,
                "signal_type": signal.get("type"),
                "signal_date": signal["date"],
                "signal_price": signal_price,
                "latest_price": latest_price,
                "distance_pct": distance,
                "scan_kinds": existing["scan_kinds"] if existing else [kind],
                "job_id": result.job_id,
            }
        return sorted(
            by_stock.values(),
            key=lambda item: (
                -date.fromisoformat(item["signal_date"]).toordinal(),
                abs(item["distance_pct"]) if item["distance_pct"] is not None else float("inf"),
                str(item["code"]),
            ),
        )

    @staticmethod
    def _latest_buy_signal(signals: list[dict[str, Any]], window: set[date]) -> dict[str, Any] | None:
        chosen: dict[str, Any] | None = None
        for signal in signals:
            if not signal.get("is_buy"):
                continue
            try:
                signal_date = date.fromisoformat(str(signal.get("date"))[:10])
            except ValueError:
                continue
            if signal_date in window and (chosen is None or signal_date.isoformat() > chosen["date"]):
                chosen = {**signal, "date": signal_date.isoformat()}
        return chosen

    @staticmethod
    def _scan_view(job: Job) -> dict[str, Any]:
        result = job.result or {}
        data_as_of = _data_as_of(job)
        return {
            "job_id": job.id,
            "kind": job.kind,
            "status": job.status,
            "coverage_rate": result.get("coverage_rate"),
            "matched": result.get("matched"),
            "insufficient_history": result.get("insufficient_history"),
            "data_as_of": data_as_of.isoformat() if data_as_of else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }
