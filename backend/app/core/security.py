from __future__ import annotations

import base64
import hashlib
import ipaddress
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.core.config import PROJECT_ROOT, Settings
from backend.app.db.models import AuthSession, User
from backend.app.db.models.common import utcnow

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(session: Session, user: User, ttl_hours: int) -> str:
    raw_token = secrets.token_urlsafe(48)
    session.add(AuthSession(
        user_id=user.id,
        token_hash=token_hash(raw_token),
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    ))
    session.commit()
    return raw_token


def resolve_session(
    session: Session, raw_token: str | None, touch_interval_seconds: int = 300
) -> User | None:
    if not raw_token:
        return None
    now = utcnow()
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == token_hash(raw_token), AuthSession.expires_at > now)
    )
    if auth_session is None:
        return None
    user = session.get(User, auth_session.user_id)
    if user is None or not user.enabled:
        return None
    if auth_session.last_seen_at <= now - timedelta(seconds=touch_interval_seconds):
        auth_session.last_seen_at = now
        session.commit()
    return user


def revoke_session(session: Session, raw_token: str | None) -> None:
    if raw_token:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(raw_token)))
        session.commit()


def ensure_identity(session: Session, settings: Settings) -> User:
    username = "local" if settings.auth_mode == "local" else (settings.admin_username or "admin")
    user = session.scalar(select(User).where(User.username == username))
    if user is None:
        if settings.auth_mode == "admin" and not settings.admin_password:
            raise RuntimeError("ADMIN_PASSWORD is required when AUTH_MODE=admin")
        user = User(
            username=username,
            role="local" if settings.auth_mode == "local" else "admin",
            password_hash=hash_password(settings.admin_password) if settings.admin_password else None,
        )
        session.add(user)
        session.commit()
    elif settings.auth_mode == "admin" and settings.admin_password:
        if not verify_password(user.password_hash, settings.admin_password):
            user.password_hash = hash_password(settings.admin_password)
            session.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
            session.commit()
    return user


def cleanup_expired_sessions(session: Session) -> int:
    result = session.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def resolve_client_address(client_host: str, forwarded_for: str | None, trusted_proxies: list[str]) -> str:
    try:
        direct = ipaddress.ip_address(client_host)
    except ValueError:
        return client_host
    trusted = False
    for rule in trusted_proxies:
        try:
            if direct in ipaddress.ip_network(rule, strict=False):
                trusted = True
                break
        except ValueError:
            continue
    if not trusted or not forwarded_for:
        return client_host
    candidate = forwarded_for.split(",", 1)[0].strip()
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return client_host

def _secret_material(settings: Settings) -> bytes:
    if settings.app_secret_key:
        return settings.app_secret_key.encode("utf-8")
    key_path = PROJECT_ROOT / "data" / ".secret-key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        return key_path.read_bytes()
    value = secrets.token_bytes(48)
    key_path.write_bytes(value)
    return value


def secret_cipher(settings: Settings) -> Fernet:
    derived = hashlib.sha256(_secret_material(settings)).digest()
    return Fernet(base64.urlsafe_b64encode(derived))