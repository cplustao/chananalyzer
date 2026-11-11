from __future__ import annotations

from datetime import date

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
from backend.app.chan_core.engine import ALGORITHM_VERSION as CHAN_ALGORITHM_VERSION
from backend.app.db.models import AnalysisRun, User
from backend.app.db.session import get_session
from backend.app.repositories.instruments import InstrumentRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services.bar_access import MIN_ANALYSIS_BARS, analysis_series_summary
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
    analysis = _chart_structure(run)
    bar_count, latest_bar = analysis_series_summary(session, instrument_id)
    return {
        "instrument": instrument_view(instrument).model_dump(),
        "analysis": analysis,
        "algorithm_version": run.algorithm_version if run else None,
        "calculated_at": run.finished_at.isoformat() if run and run.finished_at else None,
        "is_stale": _structure_is_stale(analysis, bar_count, latest_bar),
        "available_bar_count": bar_count,
        "min_analysis_bars": MIN_ANALYSIS_BARS,
    }


def _chart_structure(run: AnalysisRun | None) -> dict | None:
    if run is None:
        return None
    if run.structure_snapshot:
        return run.structure_snapshot
    # Older chan-only runs kept the full structure here; compact AI inputs cannot draw a chart.
    snapshot = run.input_snapshot or {}
    return snapshot if "bi_list" in snapshot else None


def _structure_is_stale(analysis: dict | None, bar_count: int, latest_bar: date | None) -> bool:
    """True when the saved structure was computed from less data, or by an older engine, than is current."""
    if analysis is None or latest_bar is None:
        return False
    if analysis.get("algorithm_version") != CHAN_ALGORITHM_VERSION:
        return True
    computed_through = str(analysis.get("end_date") or "").replace("/", "-")[:10]
    if computed_through < latest_bar.isoformat():
        return True
    computed_bars = analysis.get("analysis_bar_count")
    return computed_bars is None or int(computed_bars) < bar_count


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
    lookback_days: int | None = Query(None, ge=1, le=3650),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobAccepted:
    instrument = InstrumentRepository(session).get(instrument_id)
    if instrument is None:
        raise HTTPException(status_code=404, detail="股票不存在")
    if len(instrument.code) != 6 or not instrument.code.isdigit():
        raise HTTPException(status_code=422, detail="当前记录不是规范 A 股个股，无法执行单股行情更新")
    adjustment = "NONE" if str(instrument.exchange or "").upper() == "BJ" else "QFQ"
    # A fixed recent window from a new provider would become the whole research series,
    # because analysis reads one provider only. Default to the gap-aware catch-up.
    window = {"auto_backfill": True} if lookback_days is None else {
        "auto_backfill": False,
        "lookback_days": lookback_days,
    }
    payload = {
        "codes": [instrument.code],
        **window,
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
