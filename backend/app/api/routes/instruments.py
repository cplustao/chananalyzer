from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user
from backend.app.api.schemas import (
    BarSeries,
    BarView,
    InstrumentFacets,
    InstrumentPage,
    InstrumentView,
    JobAccepted,
)
from backend.app.db.models import AnalysisRun, User
from backend.app.db.session import get_session
from backend.app.repositories.instruments import InstrumentRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services.views import instrument_view

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("", response_model=InstrumentPage)
def list_instruments(
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> InstrumentPage:
    items, total = InstrumentRepository(session).list(q, page, page_size)
    return InstrumentPage(
        items=[instrument_view(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )

@router.get("/facets", response_model=InstrumentFacets)
def get_instrument_facets(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> InstrumentFacets:
    return InstrumentFacets.model_validate(InstrumentRepository(session).facets())



@router.get("/{instrument_id}", response_model=InstrumentView)
def get_instrument(
    instrument_id: int,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> InstrumentView:
    item = InstrumentRepository(session).get(instrument_id)
    if item is None:
        raise HTTPException(status_code=404, detail="股票不存在")
    return instrument_view(item)


@router.get("/{instrument_id}/bars", response_model=BarSeries)
def get_bars(
    instrument_id: int,
    timeframe: str = "DAY",
    adjustment: str = "QFQ",
    limit: int = Query(500, ge=1, le=3000),
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> BarSeries:
    repository = InstrumentRepository(session)
    instrument = repository.get(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="股票不存在")
    effective_adjustment = adjustment.upper()
    bars = repository.bars(instrument_id, timeframe.upper(), effective_adjustment, limit)
    if (
        not bars
        and effective_adjustment == "QFQ"
        and str(instrument.exchange or "").upper() == "BJ"
    ):
        effective_adjustment = "NONE"
        bars = repository.bars(instrument_id, timeframe.upper(), effective_adjustment, limit)
    return BarSeries(
        instrument=instrument_view(instrument),
        timeframe=timeframe.upper(),
        adjustment=effective_adjustment,
        items=[BarView.model_validate(item) for item in bars],
    )


@router.get("/{instrument_id}/chan-structure")
def get_chan_structure(
    instrument_id: int,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict:
    instrument = InstrumentRepository(session).get(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="股票不存在")
    run = session.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.instrument_id == instrument_id,
            AnalysisRun.kind == "stock",
            AnalysisRun.status.in_(("completed", "partial")),
        )
        .order_by(AnalysisRun.created_at.desc())
    )
    return {
        "instrument": instrument_view(instrument).model_dump(),
        "analysis": run.input_snapshot if run else None,
        "algorithm_version": run.algorithm_version if run else None,
        "calculated_at": run.finished_at.isoformat() if run and run.finished_at else None,
    }


@router.post("/{instrument_id}/analyses", response_model=JobAccepted, status_code=202)
def analyze_instrument(
    instrument_id: int,
    force: bool = False,
    include_ai: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobAccepted:
    instrument = InstrumentRepository(session).get(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="股票不存在")
    job, deduplicated = JobRepository(session).create(
        "stock.analyze",
        {"code": instrument.code, "include_ai": include_ai},
        user.id,
        force=force,
        max_attempts=3,
    )
    return JobAccepted(job_id=job.id, status=job.status, deduplicated=deduplicated)

@router.post("/{instrument_id}/refresh", response_model=JobAccepted, status_code=202)
def refresh_instrument(
    instrument_id: int,
    force: bool = False,
    lookback_days: int = Query(30, ge=1, le=3650),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobAccepted:
    instrument = InstrumentRepository(session).get(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="股票不存在")
    if len(instrument.code) != 6 or not instrument.code.isdigit():
        raise HTTPException(status_code=422, detail="当前记录不是规范 A 股个股，无法执行单股行情更新")
    adjustment = "NONE" if str(instrument.exchange or "").upper() == "BJ" else "QFQ"
    payload = {
        "codes": [instrument.code],
        "lookback_days": lookback_days,
        "timeframe": "DAY",
        "adjustment": adjustment,
        "refresh_master_data": False,
    }
    job, deduplicated = JobRepository(session).create(
        "data.refresh",
        payload,
        user.id,
        force=force,
        max_attempts=3,
    )
    return JobAccepted(job_id=job.id, status=job.status, deduplicated=deduplicated)
