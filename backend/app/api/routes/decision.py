from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user, settings_dep
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_session
from backend.app.services.decision import DecisionService

router = APIRouter(prefix="/decision", tags=["decision"])


@router.get("/today")
def today(
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
    user: User = Depends(current_user),
) -> dict:
    return DecisionService(session, settings, user.id).today()
