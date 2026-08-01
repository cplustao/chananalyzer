from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user
from backend.app.db.models import User
from backend.app.db.session import get_session
from backend.app.services.scan_changes import ScanChangeService

router = APIRouter(prefix="/scans", tags=["scans"])


@router.get("/{job_id}/changes")
def scan_changes(
    job_id: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict:
    return ScanChangeService(session).compare(job_id)
