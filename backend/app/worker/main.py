from __future__ import annotations

import logging
import os
import signal
import socket
import time
import uuid

from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging
from backend.app.db.session import SessionLocal
from backend.app.repositories.jobs import JobRepository
from backend.app.services.job_handlers import JobHandlers
from backend.app.services.schedules import enqueue_due_schedules
from backend.app.services.secrets import SecretService

logger = logging.getLogger(__name__)
_stop = False


def _request_stop(_signum, _frame) -> None:
    global _stop
    _stop = True


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    worker_instance_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)
    with SessionLocal() as session:
        SecretService(session, settings).apply_to_environment()
        repository = JobRepository(session, worker_instance_id)
        repository.touch_worker()
        recovered = repository.recover_stale()
        if recovered:
            logger.info("Recovered %s stale jobs", recovered)
    logger.info("ChanAnalyzer worker started", extra={"worker_instance_id": worker_instance_id})
    last_schedule_check = 0.0
    while not _stop:
        with SessionLocal() as session:
            if time.monotonic() - last_schedule_check >= 30:
                try:
                    enqueued = enqueue_due_schedules(session, settings.worker_max_attempts)
                    if enqueued:
                        logger.info("Enqueued %s scheduled jobs", enqueued)
                except Exception:
                    session.rollback()
                    logger.exception("Unable to enqueue scheduled jobs")
                last_schedule_check = time.monotonic()
            repository = JobRepository(session, worker_instance_id)
            repository.touch_worker()
            job = repository.claim_next()
            if job is None:
                time.sleep(settings.worker_poll_seconds)
                continue
            job_id = job.id
            try:
                result = JobHandlers(session, worker_instance_id=worker_instance_id).execute(job)
                if job.status == "running":
                    repository.complete(job, result=result)
            except Exception as exc:
                session.rollback()
                logger.exception("Job %s failed", job_id, extra={"job_id": job_id})
                failed_job = repository.get(job_id)
                if failed_job is not None:
                    try:
                        repository.fail(failed_job, str(exc))
                    except Exception:
                        session.rollback()
                        logger.exception(
                            "Unable to persist failure for job %s",
                            job_id,
                            extra={"job_id": job_id},
                        )
    logger.info("ChanAnalyzer worker stopped")


if __name__ == "__main__":
    run()
