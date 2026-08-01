from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user, settings_dep
from backend.app.api.schemas import (
    JobAccepted,
    JobCreate,
    JobItemView,
    JobStagesView,
    JobView,
    ScanResultView,
)
from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.db.models import Instrument, JobEvent, JobItem, ScanResult, User
from backend.app.db.session import SessionLocal, get_session
from backend.app.repositories.jobs import JobRepository

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("", response_model=JobAccepted, status_code=202)
def create_job(
    payload: JobCreate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> JobAccepted:
    job, deduplicated = JobRepository(session).create(
        payload.kind,
        payload.payload.model_dump(mode="json", exclude_none=True),
        user.id,
        force=payload.force,
        max_attempts=settings.worker_max_attempts,
    )
    return JobAccepted(job_id=job.id, status=job.status, deduplicated=deduplicated)


@router.get("", response_model=list[JobView])
def recent_jobs(
    limit: int = Query(20, ge=1, le=100),
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[JobView]:
    return [JobView.model_validate(item) for item in JobRepository(session).list_recent(limit)]


@router.get("/{job_id}", response_model=JobView)
def get_job(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> JobView:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JobView.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobView)
def cancel_job(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> JobView:
    job = JobRepository(session).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JobView.model_validate(JobRepository(session).cancel(job))


@router.delete("/{job_id}", status_code=204)
def archive_job(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> Response:
    repository = JobRepository(session)
    job = repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    try:
        repository.archive(job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/{job_id}/items", response_model=list[JobItemView])
def job_items(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[JobItemView]:
    if JobRepository(session).get(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = session.scalars(
        select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.subject_key)
    ).all()
    return [JobItemView.model_validate(item) for item in items]


@router.get("/{job_id}/results", response_model=list[ScanResultView])
def job_results(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[ScanResultView]:
    rows = session.execute(
        select(ScanResult, Instrument.code, Instrument.name)
        .outerjoin(Instrument, Instrument.id == ScanResult.instrument_id)
        .where(ScanResult.job_id == job_id)
        .order_by(ScanResult.rank)
    ).all()
    return [
        ScanResultView(
            instrument_id=result.instrument_id,
            code=code,
            name=name,
            scan_kind=result.scan_kind,
            signal_type=result.signal_type,
            signal_date=result.signal_date,
            rank=result.rank,
            score=result.score,
            payload=result.payload,
        )
        for result, code, name in rows
    ]


@router.get("/{job_id}/stages", response_model=JobStagesView)
def job_stages(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> JobStagesView:
    repository = JobRepository(session)
    job = repository.get(job_id)
    if job is None:
        raise AppError("job_not_found", "Job not found", status_code=404)
    return JobStagesView(job_id=job.id, status=job.status, items=repository.stages(job))


@router.post("/{job_id}/rerun", response_model=JobAccepted, status_code=202)
def rerun_job(
    job_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> JobAccepted:
    repository = JobRepository(session)
    original = repository.get(job_id)
    if original is None:
        raise AppError("job_not_found", "Job not found", status_code=404)
    if original.status not in {"partial", "failed"}:
        raise AppError(
            "job_not_rerunnable",
            "Only partial or failed jobs can be rerun",
            status_code=409,
            details={"status": original.status},
        )
    failed_subjects = list(
        session.scalars(
            select(JobItem.subject_key).where(
                JobItem.job_id == original.id,
                JobItem.status.in_(("failed", "partial")),
                JobItem.subject_key.is_not(None),
            )
        ).all()
    )
    if not failed_subjects and original.kind == "data.refresh":
        failed_codes = (original.result or {}).get("failed_codes", [])
        if isinstance(failed_codes, list) and failed_codes:
            failed_subjects = [str(code).strip() for code in failed_codes if str(code).strip()]
        else:
            errors = (original.result or {}).get("errors", [])
            if isinstance(errors, list):
                failed_subjects = [
                    str(item["code"]).strip()
                    for item in errors
                    if isinstance(item, dict) and item.get("code") and str(item["code"]).strip()
                ]
    if not failed_subjects and original.kind == "limit_up.refresh":
        errors = (original.result or {}).get("errors", [])
        if isinstance(errors, list):
            failed_dates = [
                str(item["trade_date"]).strip()
                for item in errors
                if isinstance(item, dict) and item.get("trade_date") and str(item["trade_date"]).strip()
            ]
        else:
            failed_dates = []
    else:
        failed_dates = []
    payload = dict(original.payload or {})
    if failed_dates:
        payload["dates"] = list(dict.fromkeys(failed_dates))
        payload.pop("start_date", None)
        payload.pop("end_date", None)
    if failed_subjects:
        payload["codes"] = list(dict.fromkeys(failed_subjects))
        if original.kind == "data.refresh":
            payload["refresh_master_data"] = False
    job, _ = repository.create(
        original.kind,
        payload,
        user.id,
        force=True,
        max_attempts=settings.worker_max_attempts,
        retry_of_id=original.id,
    )
    return JobAccepted(job_id=job.id, status=job.status, deduplicated=False)


@router.get("/{job_id}/events")
def job_events(
    job_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _user: User = Depends(current_user),
) -> StreamingResponse:
    try:
        initial_sequence = max(0, int(last_event_id or 0))
    except ValueError:
        initial_sequence = 0

    async def stream():
        sequence = initial_sequence
        last_keep_alive = time.monotonic()
        while True:
            if await request.is_disconnected():
                return
            with SessionLocal() as session:
                events = session.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.sequence > sequence)
                    .order_by(JobEvent.sequence)
                ).all()
                job = JobRepository(session).get(job_id)
                if job is None:
                    yield 'event: error\ndata: {"message":"job not found"}\n\n'
                    return
                for event in events:
                    sequence = event.sequence
                    data = json.dumps(
                        {"sequence": event.sequence, "event": event.event_type, "payload": event.payload},
                        ensure_ascii=False,
                        default=str,
                    )
                    yield f"id: {sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
                if job.status in {"completed", "partial", "failed", "cancelled"}:
                    return
            now = time.monotonic()
            if now - last_keep_alive >= 15:
                yield ": keep-alive\n\n"
                last_keep_alive = now
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
