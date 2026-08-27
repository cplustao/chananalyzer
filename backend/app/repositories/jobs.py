from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from backend.app.core.errors import sanitize_text
from backend.app.db.models import Job, JobEvent, JobItem, WorkerHeartbeat
from backend.app.db.models.common import utcnow
from backend.app.db.models.jobs import ACTIVE_JOB_STATUSES


class JobRepository:
    def __init__(self, session: Session, worker_instance_id: str | None = None):
        self.session = session
        self.worker_instance_id = worker_instance_id

    @staticmethod
    def dedupe_key(kind: str, payload: dict[str, Any]) -> str:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(f"{kind}:{canonical}".encode()).hexdigest()

    def create(
        self,
        kind: str,
        payload: dict[str, Any],
        user_id: str | None,
        force: bool,
        max_attempts: int,
        retry_of_id: str | None = None,
    ) -> tuple[Job, bool]:
        key = self.dedupe_key(kind, payload)
        if not force:
            existing = self.session.scalar(
                select(Job)
                .where(Job.dedupe_key == key, Job.status.in_(ACTIVE_JOB_STATUSES))
                .order_by(Job.created_at.desc())
            )
            if existing:
                return existing, True
        job = Job(
            kind=kind,
            payload=payload,
            requested_by=user_id,
            dedupe_key=key,
            max_attempts=max_attempts,
            retry_of_id=retry_of_id,
        )
        self.session.add(job)
        self.session.flush()
        self.append_event(job, "queued", {"message": "任务已进入队列"})
        self.session.commit()
        return job, False

    def get(self, job_id: str) -> Job | None:
        return self.session.scalar(select(Job).where(Job.id == job_id))

    def get_with_events(self, job_id: str) -> Job | None:
        return self.session.scalar(select(Job).options(selectinload(Job.events)).where(Job.id == job_id))

    def list_recent(self, limit: int = 20) -> list[Job]:
        return list(
            self.session.scalars(
                select(Job)
                .where(Job.archived_at.is_(None))
                .order_by(Job.created_at.desc())
                .limit(limit)
            ).all()
        )

    def archive(self, job: Job) -> Job:
        if job.status in ACTIVE_JOB_STATUSES:
            raise ValueError("执行中的任务不能移除，请先取消任务")
        job.archived_at = utcnow()
        self.session.commit()
        return job

    def prepare_items(
        self, job: Job, subjects: list[str], instrument_ids: dict[str, int]
    ) -> dict[str, JobItem]:
        existing = {
            item.subject_key: item
            for item in self.session.scalars(select(JobItem).where(JobItem.job_id == job.id)).all()
            if item.subject_key
        }
        for subject in subjects:
            if subject not in existing:
                item = JobItem(job_id=job.id, subject_key=subject, instrument_id=instrument_ids.get(subject))
                self.session.add(item)
                existing[subject] = item
        job.total = len(subjects)
        self.session.commit()
        return existing

    def start_item(self, job: Job, item: JobItem) -> None:
        item.status = "running"
        item.started_at = item.started_at or utcnow()
        item.attempts += 1
        self.session.commit()

    def finish_item(
        self,
        job: Job,
        item: JobItem,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        status: str | None = None,
    ) -> None:
        item.status = status or ("failed" if error else "completed")
        item.result = result
        item.error = sanitize_text(error) if error else None
        item.finished_at = utcnow()
        self.session.flush()
        job.completed = int(
            self.session.scalar(
                select(func.count())
                .select_from(JobItem)
                .where(JobItem.job_id == job.id, JobItem.status == "completed")
            )
            or 0
        )
        job.failed = int(
            self.session.scalar(
                select(func.count())
                .select_from(JobItem)
                .where(JobItem.job_id == job.id, JobItem.status.in_(("failed", "partial")))
            )
            or 0
        )
        if job.total:
            job.progress = min(95.0, (job.completed + job.failed) / job.total * 95.0)
        self.session.commit()

    def complete_all_items(
        self, job: Job, result_by_subject: dict[str, dict[str, Any]] | None = None
    ) -> None:
        items = self.session.scalars(select(JobItem).where(JobItem.job_id == job.id)).all()
        now = utcnow()
        for item in items:
            item.status = "completed"
            item.started_at = item.started_at or now
            item.finished_at = now
            item.result = (result_by_subject or {}).get(item.subject_key or "")
        job.completed = len(items)
        job.failed = 0
        if job.total:
            job.progress = 95.0
        self.session.commit()

    def cancel(self, job: Job) -> Job:
        if job.status == "queued":
            job.cancel_requested = True
            job.status = "cancelled"
            job.finished_at = utcnow()
            job.message = "任务已取消"
            self.append_event(job, "cancelled", {"before_start": True})
            self.session.commit()
        elif job.status in ACTIVE_JOB_STATUSES:
            job.cancel_requested = True
            job.message = "已请求取消"
            self.append_event(job, "cancel_requested", None)
            self.session.commit()
        return job

    def claim_next(self) -> Job | None:
        now = utcnow()
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            job = self.session.scalar(
                select(Job)
                .where(Job.status == "queued")
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "running"
            job.started_at = job.started_at or now
            job.heartbeat_at = now
            job.attempts += 1
        else:
            candidate_id = self.session.scalar(
                select(Job.id).where(Job.status == "queued").order_by(Job.created_at).limit(1)
            )
            if candidate_id is None:
                return None
            claimed_id = self.session.scalar(
                update(Job)
                .where(Job.id == candidate_id, Job.status == "queued")
                .values(
                    status="running",
                    started_at=func.coalesce(Job.started_at, now),
                    heartbeat_at=now,
                    attempts=Job.attempts + 1,
                )
                .returning(Job.id)
                .execution_options(synchronize_session=False)
            )
            if claimed_id is None:
                self.session.rollback()
                return None
            job = self.session.get(Job, claimed_id)
            if job is None:
                self.session.rollback()
                return None
        job.message = "任务执行中"
        self.append_event(job, "running", {"attempt": job.attempts})
        self.touch_worker(job.id, commit=False)
        self.session.commit()
        return job

    def heartbeat(self, job: Job, message: str | None = None, progress: float | None = None) -> None:
        job.heartbeat_at = utcnow()
        if message is not None:
            job.message = message
        if progress is not None:
            job.progress = max(0.0, min(100.0, progress))
        self.touch_worker(job.id, commit=False)
        self.append_event(
            job,
            "stage_progress",
            {
                "stage_key": job.kind,
                "processed": job.completed,
                "coverage_rate": job.progress / 100.0,
                "message": message,
            },
        )
        self.session.commit()

    def complete(self, job: Job, result: dict[str, Any] | None = None, partial: bool = False) -> None:
        job.status = "partial" if partial else "completed"
        job.result = result
        if job.total == 0:
            job.total = 1
        if not partial:
            job.completed = max(job.completed, job.total - job.failed)
        job.progress = 100.0
        job.finished_at = utcnow()
        job.heartbeat_at = utcnow()
        job.message = "部分完成" if partial else "任务完成"
        job.current_stage = None
        self.append_event(job, job.status, result)
        self.touch_worker(None, commit=False)
        self.session.commit()

    def fail(self, job: Job, error: str, *, retryable: bool = True) -> None:
        safe_error = sanitize_text(error)
        job.error = safe_error
        job.failure_summary = safe_error
        job.heartbeat_at = utcnow()
        if retryable and job.attempts < job.max_attempts and not job.cancel_requested:
            job.status = "queued"
            job.message = f"执行失败，将重试（{job.attempts}/{job.max_attempts}）"
            self.append_event(job, "retrying", {"error": safe_error, "attempt": job.attempts})
        else:
            job.status = "failed"
            job.finished_at = utcnow()
            job.message = "任务失败"
            self.append_event(job, "failed", {"error": safe_error})
        self.touch_worker(None, commit=False)
        self.session.commit()

    def mark_cancelled(self, job: Job) -> None:
        job.status = "cancelled"
        job.finished_at = utcnow()
        job.message = "任务已取消"
        self.append_event(job, "cancelled", None)
        self.touch_worker(None, commit=False)
        self.session.commit()

    def append_event(self, job: Job, event_type: str, payload: dict[str, Any] | None) -> JobEvent:
        sequence = self.session.scalar(
            update(Job)
            .where(Job.id == job.id)
            .values(event_sequence=Job.event_sequence + 1)
            .returning(Job.event_sequence)
            .execution_options(synchronize_session=False)
        )
        if sequence is None:
            raise RuntimeError(f"job disappeared while appending event: {job.id}")
        job.event_sequence = int(sequence)
        event = JobEvent(job_id=job.id, sequence=int(sequence), event_type=event_type, payload=payload)
        self.session.add(event)
        return event

    def append_stage_event(
        self,
        job: Job,
        event_type: str,
        stage_key: str,
        **payload: Any,
    ) -> JobEvent:
        if event_type not in {"stage_started", "stage_progress", "stage_completed", "stage_failed"}:
            raise ValueError(f"unsupported stage event: {event_type}")
        safe_payload = {**payload, "stage_key": stage_key}
        job.current_stage = stage_key if event_type in {"stage_started", "stage_progress"} else None
        checkpoint = dict(job.checkpoint or {})
        checkpoint[stage_key] = {
            "status": event_type.removeprefix("stage_"),
            "updated_at": utcnow().isoformat(),
            **{key: value for key, value in payload.items() if key in {"processed", "coverage_rate", "message"}},
        }
        job.checkpoint = checkpoint
        if "error" in safe_payload:
            safe_payload["error"] = sanitize_text(safe_payload["error"])
        event = self.append_event(job, event_type, safe_payload)
        self.session.commit()
        return event

    def stages(self, job: Job) -> list[dict[str, Any]]:
        events = self.session.scalars(
            select(JobEvent)
            .where(JobEvent.job_id == job.id, JobEvent.event_type.like("stage_%"))
            .order_by(JobEvent.sequence)
        ).all()
        stages: dict[str, dict[str, Any]] = {}
        for event in events:
            payload = dict(event.payload or {})
            key = str(payload.pop("stage_key", "unknown"))
            stages[key] = {
                **stages.get(key, {}),
                **payload,
                "stage_key": key,
                "status": event.event_type.removeprefix("stage_"),
                "updated_at": event.created_at.isoformat(),
            }
        return list(stages.values())

    def touch_worker(self, active_job_id: str | None = None, *, commit: bool = True) -> None:
        if not self.worker_instance_id:
            return
        heartbeat = self.session.get(WorkerHeartbeat, self.worker_instance_id)
        if heartbeat is None:
            heartbeat = WorkerHeartbeat(instance_id=self.worker_instance_id)
            self.session.add(heartbeat)
        heartbeat.heartbeat_at = utcnow()
        heartbeat.active_job_id = active_job_id
        if commit:
            self.session.commit()
    def recover_stale(self, stale_minutes: int = 5) -> int:
        cutoff = utcnow() - timedelta(minutes=stale_minutes)
        jobs = self.session.scalars(
            select(Job).where(
                Job.status == "running", (Job.heartbeat_at.is_(None)) | (Job.heartbeat_at < cutoff)
            )
        ).all()
        for job in jobs:
            job.status = "queued" if job.attempts < job.max_attempts else "failed"
            job.message = "Worker 重启后恢复任务" if job.status == "queued" else "任务超过最大重试次数"
            if job.status == "failed":
                job.finished_at = utcnow()
            self.append_event(job, "recovered", {"new_status": job.status})
        self.session.commit()
        return len(jobs)
