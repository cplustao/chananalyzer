from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from backend.app.api.deps import current_user
from backend.app.db.models import (
    AnalysisReport,
    AnalysisRun,
    Instrument,
    Job,
    LimitUpMetric,
    ScanResult,
    User,
)
from backend.app.db.session import get_session
from backend.app.services.views import instrument_view

router = APIRouter(tags=["research-history"])


def _run_summary(
    run: AnalysisRun,
    instrument: Instrument | None,
    report_count: int,
    metric: LimitUpMetric | None = None,
) -> dict[str, Any]:
    return {
        "id": run.id,
        "kind": run.kind,
        "instrument": instrument_view(instrument).model_dump() if instrument else None,
        "subject_code": instrument.code if instrument else (run.input_snapshot or {}).get("code"),
        "subject_name": instrument.name if instrument else (run.input_snapshot or {}).get("name"),
        "subject_date": run.subject_date,
        "status": run.status,
        "algorithm_version": run.algorithm_version,
        "report_count": report_count,
        "overall_score": metric.overall_score if metric else None,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


@router.get("/analyses")
def list_analyses(
    kind: Literal["stock", "limit_up", "ipo"] | None = None,
    instrument_id: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    filters = []
    if kind:
        filters.append(AnalysisRun.kind == kind)
    if instrument_id:
        filters.append(AnalysisRun.instrument_id == instrument_id)
    if start_date:
        filters.append(AnalysisRun.subject_date >= start_date)
    if end_date:
        filters.append(AnalysisRun.subject_date <= end_date)

    report_count = (
        select(func.count())
        .where(AnalysisRun.id == AnalysisReport.analysis_run_id)
        .correlate(AnalysisRun)
        .scalar_subquery()
    )
    total = int(session.scalar(select(func.count()).select_from(AnalysisRun).where(*filters)) or 0)
    rows = session.execute(
        select(AnalysisRun, Instrument, report_count)
        .outerjoin(Instrument, Instrument.id == AnalysisRun.instrument_id)
        .where(*filters)
        .order_by(AnalysisRun.subject_date.desc(), AnalysisRun.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    run_ids = [run.id for run, _instrument, _count in rows]
    metrics = (
        {
            metric.analysis_run_id: metric
            for metric in session.scalars(
                select(LimitUpMetric).where(LimitUpMetric.analysis_run_id.in_(run_ids))
            ).all()
        }
        if run_ids
        else {}
    )
    return {
        "items": [
            _run_summary(run, instrument, int(count or 0), metrics.get(run.id))
            for run, instrument, count in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/analyses/{run_id}")
def get_analysis(
    run_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    run = session.scalar(
        select(AnalysisRun).options(selectinload(AnalysisRun.reports)).where(AnalysisRun.id == run_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="分析记录不存在")
    instrument = session.get(Instrument, run.instrument_id) if run.instrument_id else None
    metric = session.get(LimitUpMetric, run.id)
    result = _run_summary(run, instrument, len(run.reports), metric)
    result.update(
        {
            "config_snapshot": run.config_snapshot,
            "input_snapshot": run.input_snapshot,
            "error": run.error,
            "reports": [
                {
                    "id": report.id,
                    "role": report.role,
                    "provider": report.provider,
                    "model": report.model,
                    "prompt_version": report.prompt_version,
                    "content": report.content,
                    "status": report.status,
                    "structured_payload": report.structured_payload,
                    "input_digest": report.input_digest,
                    "validation_status": report.validation_status,
                    "validation_error": report.validation_error,
                    "created_at": report.created_at,
                }
                for report in sorted(run.reports, key=lambda item: item.created_at)
            ],
            "metrics": {
                "day_score": metric.day_score,
                "week_score": metric.week_score,
                "value_score": metric.value_score,
                "market_score": metric.market_score,
                "timing_score": metric.timing_score,
                "risk_score": metric.risk_score,
                "overall_score": metric.overall_score,
                "day_classification": metric.day_classification,
                "week_classification": metric.week_classification,
                "completeness": metric.completeness,
                "evidence": metric.evidence,
                "risk_plan": metric.risk_plan,
            }
            if metric
            else None,
        }
    )
    return result


SCAN_KINDS = ("scan.buy", "scan.sell", "screen.hot", "screen.smart")


@router.get("/scans/history")
def scan_history(
    limit: int = Query(30, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(Job, func.count(ScanResult.id))
        .outerjoin(ScanResult, ScanResult.job_id == Job.id)
        .where(Job.kind.in_(SCAN_KINDS))
        .group_by(Job.id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": job.id,
            "kind": job.kind,
            "status": job.status,
            "progress": job.progress,
            "result_count": int(count or 0),
            "payload": job.payload,
            "message": job.message,
            "error": job.error,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
        }
        for job, count in rows
    ]
