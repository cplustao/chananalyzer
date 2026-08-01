from __future__ import annotations

import math
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user, settings_dep
from backend.app.api.schemas import LoginRequest, UserView
from backend.app.core.config import Settings
from backend.app.core.security import (
    issue_session,
    resolve_client_address,
    revoke_session,
    verify_password,
)
from backend.app.db.models import User
from backend.app.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])
_login_attempts: dict[tuple[str, str], deque[float]] = {}
_login_attempts_lock = threading.Lock()


def _register_login_attempt(key: tuple[str, str], limit: int, window_seconds: int) -> int | None:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _login_attempts_lock:
        attempts = _login_attempts.setdefault(key, deque())
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if len(attempts) >= limit:
            return max(1, math.ceil(window_seconds - (now - attempts[0])))
        attempts.append(now)
    return None


def _clear_login_attempts(key: tuple[str, str]) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(key, None)


@router.get("/session", response_model=UserView)
def session_info(user: User = Depends(current_user), settings: Settings = Depends(settings_dep)) -> UserView:
    return UserView(id=user.id, username=user.username, role=user.role, auth_mode=settings.auth_mode)


@router.post("/login", response_model=UserView)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> UserView:
    if settings.auth_mode == "local":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Local mode does not require login")
    client_host = request.client.host if request.client else "unknown"
    client_address = resolve_client_address(
        client_host,
        request.headers.get("X-Forwarded-For"),
        settings.trusted_proxies,
    )
    rate_key = (payload.username.casefold(), client_address)
    retry_after = _register_login_attempt(
        rate_key,
        settings.login_max_attempts,
        settings.login_window_seconds,
    )
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过多，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )
    user = session.scalar(select(User).where(User.username == payload.username, User.enabled.is_(True)))
    if user is None or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    _clear_login_attempts(rate_key)
    token = issue_session(session, user, settings.session_ttl_hours)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return UserView(id=user.id, username=user.username, role=user.role, auth_mode=settings.auth_mode)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> Response:
    revoke_session(session, request.cookies.get(settings.session_cookie_name))
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response