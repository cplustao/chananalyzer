from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError
from backend.app.db.models import Instrument, Job, ScanResult

SCAN_KINDS = ("scan.buy", "scan.sell", "screen.hot", "screen.smart")
SCAN_ALGORITHM_VERSION = "chan-core-v2-memory-1"


def _configuration(job: Job) -> str:
    payload = dict(job.payload or {})
    for key in ("codes", "types", "industries", "areas"):
        if isinstance(payload.get(key), list):
            payload[key] = sorted(str(item) for item in payload[key])
    return json.dumps(
        {
            "kind": job.kind,
            "payload": payload,
            "algorithm_version": (job.result or {}).get("algorithm_version", SCAN_ALGORITHM_VERSION),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class ScanChangeService:
    def __init__(self, session: Session):
        self.session = session

    def compare(self, job_id: str) -> dict[str, Any]:
        current = self.session.get(Job, job_id)
        if current is None or current.kind not in SCAN_KINDS:
            raise AppError("scan_job_not_found", "Scan job not found", status_code=404)
        current_rows = self._rows(current.id)
        if current.status != "completed":
            return self._not_comparable(current, current_rows, "current_job_incomplete")
        signature = _configuration(current)
        candidates = self.session.scalars(
            select(Job)
            .where(Job.kind == current.kind, Job.status == "completed", Job.created_at < current.created_at)
            .order_by(Job.created_at.desc())
            .limit(100)
        ).all()
        previous = next((job for job in candidates if _configuration(job) == signature), None)
        if previous is None:
            return self._not_comparable(current, current_rows, "no_previous_comparable_job")
        previous_rows = self._rows(previous.id)
        current_map = {self._key(item): item for item in current_rows}
        previous_map = {self._key(item): item for item in previous_rows}
        items = []
        for key in sorted(set(current_map) | set(previous_map)):
            present = current_map.get(key)
            prior = previous_map.get(key)
            state = "continued_hit" if present and prior else "new_hit" if present else "no_longer_hit"
            basis = present or prior
            if basis is None:
                continue
            items.append(
                {**basis, "state": state, "current_job_id": current.id, "previous_job_id": previous.id}
            )
        return {
            "job_id": current.id,
            "previous_job_id": previous.id,
            "comparable": True,
            "reason": None,
            "algorithm_version": (current.result or {}).get("algorithm_version", SCAN_ALGORITHM_VERSION),
            "items": items,
        }

    def _rows(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.session.execute(
            select(ScanResult, Instrument.code, Instrument.name)
            .outerjoin(Instrument, Instrument.id == ScanResult.instrument_id)
            .where(ScanResult.job_id == job_id)
        ).all()
        return [
            {
                "instrument_id": result.instrument_id,
                "code": code,
                "name": name,
                "signal_type": result.signal_type,
                "signal_date": result.signal_date.isoformat() if result.signal_date else None,
                "score": result.score,
            }
            for result, code, name in rows
        ]

    @staticmethod
    def _key(item: dict[str, Any]) -> tuple[str, str]:
        return (str(item.get("instrument_id") or item.get("code") or ""), str(item.get("signal_type") or ""))

    @staticmethod
    def _not_comparable(job: Job, rows: list[dict[str, Any]], reason: str) -> dict[str, Any]:
        return {
            "job_id": job.id,
            "previous_job_id": None,
            "comparable": False,
            "reason": reason,
            "algorithm_version": (job.result or {}).get("algorithm_version", SCAN_ALGORITHM_VERSION),
            "items": [
                {**item, "state": "not_comparable", "current_job_id": job.id, "previous_job_id": None}
                for item in rows
            ],
        }
