from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app import __version__
from backend.app.api.deps import current_user, settings_dep
from backend.app.api.schemas import Catalog, DataHealthView, SystemStatus
from backend.app.core.config import Settings
from backend.app.db.models import AnalysisRun, Bar, Instrument, Job, User, WorkerHeartbeat
from backend.app.db.models.common import utcnow
from backend.app.db.session import engine, get_session
from backend.app.services.data_health import DataHealthService

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatus)
def system_status(
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> SystemStatus:
    worker = session.scalar(select(WorkerHeartbeat).order_by(WorkerHeartbeat.heartbeat_at.desc()).limit(1))
    if worker is None:
        worker_status: Literal["healthy", "stale", "missing"] = "missing"
    elif worker.heartbeat_at < utcnow() - timedelta(seconds=180):
        worker_status = "stale"
    else:
        worker_status = "healthy"
    return SystemStatus(
        status="healthy",
        version=__version__,
        auth_mode=settings.auth_mode,
        database=engine.url.get_backend_name(),
        worker_required=True,
        worker_status=worker_status,
        last_heartbeat=worker.heartbeat_at if worker else None,
        active_job=worker.active_job_id if worker else None,
        data_counts={
            "instruments": int(
                session.scalar(
                    select(func.count()).select_from(Instrument).where(
                        Instrument.status == "active",
                        func.length(Instrument.code) == 6,
                    )
                )
                or 0
            ),
            "bars": int(session.scalar(select(func.count()).select_from(Bar)) or 0),
            "jobs": int(session.scalar(select(func.count()).select_from(Job)) or 0),
            "analysis_runs": int(session.scalar(select(func.count()).select_from(AnalysisRun)) or 0),
        },
    )


@router.get("/catalog", response_model=Catalog)
def catalog(_user: User = Depends(current_user)) -> Catalog:
    return Catalog(
        timeframes=["DAY", "WEEK", "MON", "1M", "5M", "15M", "30M"],
        adjustments=["QFQ", "HFQ", "NONE"],
        buy_signal_types=["1", "1p", "2", "2s", "3a", "3b"],
        sell_signal_types=["1", "1p", "2", "2s", "3a", "3b"],
        analysis_kinds=["stock", "limit_up", "ipo", "market_radar"],
        algorithm_versions={
            "chan_core": "chan-core-v2-memory-1",
            "market_radar": "2.0",
            "limit_up": "limit-up-v2.0",
            "ipo": "ipo-v2.0",
        },
    )


@router.get("/data-health", response_model=DataHealthView)
def data_health(
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
    _user: User = Depends(current_user),
) -> DataHealthView:
    return DataHealthView.model_validate(DataHealthService(session, settings).snapshot())
