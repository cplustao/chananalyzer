from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.core.security import ensure_identity, resolve_session
from backend.app.db.models import User
from backend.app.db.session import get_session


def settings_dep() -> Settings:
    return get_settings()


def current_user(
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> User:
    if settings.auth_mode == "local":
        client_host = request.client.host if request.client else ""
        if settings.environment != "test" and client_host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Local mode only accepts local clients")
        return ensure_identity(session, settings)
    user = resolve_session(
        session,
        request.cookies.get(settings.session_cookie_name),
        settings.session_touch_interval_seconds,
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user