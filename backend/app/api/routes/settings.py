from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user, settings_dep
from backend.app.api.schemas import JobAccepted, ScheduleUpdate, SecretUpdate
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_session
from backend.app.repositories.jobs import JobRepository
from backend.app.services.schedules import (
    ensure_default_schedules,
    schedule_view,
    upsert_schedule,
)
from backend.app.services.secrets import SecretService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/secrets")
def secret_status(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> list[dict[str, object]]:
    return SecretService(session, settings).status()


@router.put("/secrets/{key}")
def update_secret(
    key: str,
    payload: SecretUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> dict:
    service = SecretService(session, settings)
    key = key.upper()
    try:
        service.set(key, payload.value, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    service.apply_to_environment()
    return {"key": key, "configured": True, "source": "database"}
_CONNECTION_TESTS = {
    "TUSHARE_TOKEN": ("POST", "https://api.tushare.pro"),
    "DEEPSEEK_API_KEY": ("GET", "https://api.deepseek.com/models"),
    "SILICONFLOW_API_KEY": ("GET", "https://api.siliconflow.cn/v1/models"),
}


@router.post("/secrets/{key}/test")
async def test_secret_connection(
    key: str,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    key = key.upper()
    target = _CONNECTION_TESTS.get(key)
    if target is None:
        raise HTTPException(status_code=400, detail="不支持测试该配置")
    value = SecretService(session, settings).get(key)
    if not value:
        raise HTTPException(status_code=400, detail="请先保存密钥或令牌")
    method, url = target
    headers = {"Authorization": f"Bearer {value}"}
    payload = None
    if key == "TUSHARE_TOKEN":
        headers = {}
        payload = {
            "api_name": "daily",
            "token": value,
            "params": {
                "ts_code": "000001.SZ",
                "start_date": "20240102",
                "end_date": "20240102",
            },
            "fields": "ts_code,trade_date,close",
        }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            if payload is None:
                response = await client.request(method, url, headers=headers)
            else:
                response = await client.request(method, url, headers=headers, json=payload)
        latency_ms = round((time.perf_counter() - started) * 1000)
        ok = response.is_success
        message = "连接成功"
        if key == "TUSHARE_TOKEN" and ok:
            result = response.json()
            ok = result.get("code") == 0
            message = "基础日线连接成功" if ok else str(result.get("msg") or "令牌校验失败")
        elif not ok:
            message = f"服务返回 HTTP {response.status_code}"
        return {"key": key, "ok": ok, "message": message, "latency_ms": latency_ms}
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Secret connection test failed for %s: %s", key, type(exc).__name__, exc_info=True)
        return {
            "key": key,
            "ok": False,
            "message": "连接失败，请检查配置和网络",
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
@router.get("/schedules")
def list_schedules(
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> list[dict[str, object]]:
    return [schedule_view(item) for item in ensure_default_schedules(session)]


@router.put("/schedules/{key}")
def update_schedule(
    key: str,
    payload: ScheduleUpdate,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, object]:
    if not key or len(key) > 64 or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in key):
        raise HTTPException(status_code=400, detail="自动任务标识无效")
    try:
        schedule = upsert_schedule(
            session,
            key,
            payload.job_kind,
            payload.hour,
            payload.minute,
            payload.weekdays,
            payload.enabled,
            payload.payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schedule_view(schedule)


@router.post("/schedules/{key}/run", response_model=JobAccepted, status_code=202)
def run_schedule_now(
    key: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> JobAccepted:
    schedules = {item.key: item for item in ensure_default_schedules(session)}
    schedule = schedules.get(key)
    if schedule is None:
        raise HTTPException(status_code=404, detail="自动任务不存在")
    job, deduplicated = JobRepository(session).create(
        schedule.job_kind,
        {**(schedule.payload or {}), "automation_schedule": schedule.key},
        user.id,
        force=False,
        max_attempts=settings.worker_max_attempts,
    )
    return JobAccepted(job_id=job.id, status=job.status, deduplicated=deduplicated)
