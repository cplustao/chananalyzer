from __future__ import annotations

from sqlalchemy import text

from backend.app.core.security import hash_password, verify_password
from backend.app.db.session import engine


def test_sqlite_runtime_pragmas():
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert connection.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar() == 30000

def test_argon2_password_hashing():
    encoded = hash_password("research-password")
    assert encoded.startswith("$argon2")
    assert verify_password(encoded, "research-password")
    assert not verify_password(encoded, "wrong")

def test_scan_handler_persists_job_items_and_progress(session):
    from sqlalchemy import select

    from backend.app.core.config import get_settings
    from backend.app.core.security import ensure_identity
    from backend.app.db.models import Instrument, JobItem
    from backend.app.repositories.jobs import JobRepository
    from backend.app.services.job_handlers import JobHandlers

    class FakeChanEngine:
        def analyze(self, code):
            return {"code": code}

        def scan(self, **_kwargs):
            return []

    instrument = session.scalar(select(Instrument).where(Instrument.code == "600519"))
    if instrument is None:
        instrument = Instrument(code="600519", ts_code="600519.SH", exchange="SSE", name="贵州茅台")
        session.add(instrument)
        session.commit()
    user = ensure_identity(session, get_settings())
    repository = JobRepository(session)
    job, _ = repository.create(
        "scan.buy", {"codes": ["600519"], "types": ["2"]}, user.id, force=True, max_attempts=3
    )
    repository.claim_next()
    result = JobHandlers(session, FakeChanEngine()).execute(job)
    repository.complete(job, result)
    session.refresh(job)
    item = session.scalar(select(JobItem).where(JobItem.job_id == job.id))
    assert (job.total, job.completed, job.failed, job.progress) == (1, 1, 0, 100.0)
    assert item.subject_key == "600519" and item.status == "completed"

def test_chan_values_are_json_safe():
    import json
    from enum import Enum

    from backend.app.chan_core.engine import json_safe

    class ExampleLevel(Enum):
        DAY = "day"

    payload = {
        "kl_type_enum": ExampleLevel.DAY,
        "nested": [float("inf"), {"level": ExampleLevel.DAY}],
    }
    converted = json_safe(payload)
    assert converted["kl_type_enum"] == "DAY"
    assert converted["nested"][0] == "inf"
    json.dumps(converted)

