from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user
from backend.app.db.models import RadarSnapshot, User
from backend.app.db.session import get_session
from backend.app.domain.market_positions import build_executable_positions, risk_inputs_from_snapshot

router = APIRouter(prefix="/market", tags=["market"])


def _dedupe_rows(rows: list[RadarSnapshot]) -> list[RadarSnapshot]:
    """Defensively keep the latest calculation for each trade date."""
    by_date: dict[object, RadarSnapshot] = {}
    for row in sorted(rows, key=lambda item: (item.trade_date, item.calculated_at)):
        by_date[row.trade_date] = row
    return list(by_date.values())


def _history_payload(rows: list[RadarSnapshot]) -> list[dict]:
    summaries = [
        {
            "trade_date": row.trade_date.isoformat(),
            "algorithm_version": row.algorithm_version,
            "score": row.score,
            "status": row.status,
            "status_label": row.status_label,
            "freshness": row.freshness,
            "component_scores": {
                str(component.get("key")): component.get("score")
                for component in (row.snapshot.get("components") or [])
                if isinstance(component, dict) and component.get("key")
            },
            "risk_inputs": risk_inputs_from_snapshot(row.snapshot),
            "calculated_at": row.calculated_at.isoformat(),
        }
        for row in _dedupe_rows(rows)
    ]
    fresh_indexes = [index for index, item in enumerate(summaries) if item["freshness"] == "fresh"]
    positions = build_executable_positions([summaries[index] for index in fresh_indexes])
    positions_by_index = dict(zip(fresh_indexes, positions, strict=False))
    for index, item in enumerate(summaries):
        item["executable_position"] = positions_by_index.get(index)
    return summaries


@router.get("/radar")
def radar(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict:
    row = session.scalar(
        select(RadarSnapshot).order_by(RadarSnapshot.trade_date.desc(), RadarSnapshot.calculated_at.desc())
    )
    if row is not None:
        history_rows = list(
            session.scalars(
                select(RadarSnapshot)
                .where(RadarSnapshot.algorithm_version == row.algorithm_version)
                .order_by(RadarSnapshot.trade_date, RadarSnapshot.calculated_at)
            ).all()
        )
        history = _history_payload(history_rows)
        snapshot = deepcopy(row.snapshot)
        regime = dict(snapshot.get("regime") or {})
        if history:
            position = history[-1]["executable_position"]
            if position is not None:
                regime["instant_position_range"] = regime.get("position_range")
                regime["position_range"] = {"min": position["min"], "max": position["max"]}
                regime["executable_position"] = position
            else:
                regime.pop("position_range", None)
                regime.pop("instant_position_range", None)
                regime["executable_position"] = None
                regime["position_unavailable_reason"] = "当前交易日或 MA20 样本覆盖未达到可信度门槛"
        snapshot["regime"] = regime
        return {
            **snapshot,
            "source": "v2_cache",
            "algorithm_version": row.algorithm_version,
        }
    return {"source": "v2_cache", "status": "empty", "message": "暂无市场雷达快照，请提交刷新任务"}


@router.get("/radar/history")
def radar_history(
    limit: int = 120,
    algorithm_version: str | None = Query(default=None, max_length=32),
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict:
    safe_limit = min(500, max(1, limit))
    latest = session.scalar(
        select(RadarSnapshot).order_by(RadarSnapshot.trade_date.desc(), RadarSnapshot.calculated_at.desc())
    )
    target_version = algorithm_version or (latest.algorithm_version if latest else None)
    if target_version is None:
        return {"items": [], "count": 0, "source": "v2_cache", "algorithm_version": None}
    rows = list(
        reversed(
            session.scalars(
                select(RadarSnapshot)
                .where(RadarSnapshot.algorithm_version == target_version)
                .order_by(RadarSnapshot.trade_date.desc(), RadarSnapshot.calculated_at.desc())
                .limit(safe_limit)
            ).all()
        )
    )
    items = _history_payload(rows)
    return {
        "items": items,
        "count": len(items),
        "source": "v2_cache",
        "algorithm_version": target_version,
    }
