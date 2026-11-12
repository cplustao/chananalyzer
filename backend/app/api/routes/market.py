from __future__ import annotations

from copy import deepcopy

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user, settings_dep
from backend.app.core.config import Settings
from backend.app.db.models import RadarSnapshot, User
from backend.app.db.session import get_session
from backend.app.services.data_health import DataHealthService
from backend.app.services.radar_positions import version_history

router = APIRouter(prefix="/market", tags=["market"])


@router.get("/radar")
def radar(
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> dict:
    row = session.scalar(
        select(RadarSnapshot).order_by(RadarSnapshot.trade_date.desc(), RadarSnapshot.calculated_at.desc())
    )
    if row is not None:
        history = version_history(session, row.algorithm_version)
        snapshot = deepcopy(row.snapshot)
        regime = dict(snapshot.get("regime") or {})
        health = DataHealthService(session, settings).cached_snapshot()
        if history and health.get("decision_usable"):
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
        else:
            regime.pop("position_range", None)
            regime.pop("instant_position_range", None)
            regime["executable_position"] = None
            regime["position_unavailable_reason"] = "数据质量门禁未通过，不生成可执行仓位"
        snapshot["regime"] = regime
        return {
            **snapshot,
            "source": "v2_cache",
            "algorithm_version": row.algorithm_version,
            "decision_usable": bool(health.get("decision_usable")),
            "blocking_reasons": health.get("blocking_reasons", []),
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
    # Positions depend on the whole path, so they are computed over all of it before paging.
    items = version_history(session, target_version)[-safe_limit:]
    return {
        "items": items,
        "count": len(items),
        "source": "v2_cache",
        "algorithm_version": target_version,
    }
