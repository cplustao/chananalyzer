from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user, settings_dep
from backend.app.api.schemas import JobAccepted
from backend.app.core.config import Settings
from backend.app.db.models import User
from backend.app.db.session import get_session
from backend.app.repositories.jobs import JobRepository
from backend.app.services.ipo_lists import build_ipo_items
from backend.app.services.research_lists import build_limit_up_items

router = APIRouter(tags=["research"])


class ScanRequest(BaseModel):
    mode: Literal["buy", "sell", "hot", "smart"]
    scan_side: Literal["buy", "sell"] = "buy"
    codes: list[str] = Field(default_factory=list)
    types: list[str] = Field(default_factory=list)
    industries: list[str] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)
    exclude_st: bool = True
    min_net_mf_amount: float | None = None
    min_main_net_amount: float | None = None
    rank_type: str = "top_gainers"
    top_n: int = Field(200, ge=1, le=500)
    force: bool = False


class AnalysisRequest(BaseModel):
    codes: list[str] = Field(min_length=1)
    trade_date: str | None = None
    force: bool = False


def _accepted(
    kind: str,
    payload: dict[str, Any],
    force: bool,
    session: Session,
    user: User,
    settings: Settings,
) -> JobAccepted:
    job, deduplicated = JobRepository(session).create(
        kind,
        payload,
        user.id,
        force=force,
        max_attempts=settings.worker_max_attempts,
    )
    return JobAccepted(job_id=job.id, status=job.status, deduplicated=deduplicated)


def _date(value: str | None, fallback: date) -> date:
    text = (value or fallback.isoformat()).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


@router.post("/scans", response_model=JobAccepted, status_code=202)
def create_scan(
    request: ScanRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> JobAccepted:
    kinds = {
        "buy": "scan.buy",
        "sell": "scan.sell",
        "hot": "screen.hot",
        "smart": "screen.smart",
    }
    payload = request.model_dump(exclude={"mode", "force"})
    return _accepted(kinds[request.mode], payload, request.force, session, user, settings)


@router.get("/limit-ups")
def limit_up_stocks(
    trade_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    today = date.today()
    if trade_date:
        start = end = _date(trade_date, today)
    else:
        start = _date(start_date, today)
        end = _date(end_date, start)
    if start > end:
        start, end = end, start

    items = build_limit_up_items(session, start, end)
    if items:
        return {
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
            "items": items,
            "count": len(items),
            "source": "v2_database",
        }
    return {
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "items": [],
        "count": 0,
        "source": "v2_database",
    }


@router.post("/limit-ups/analyses", response_model=JobAccepted, status_code=202)
def analyze_limit_ups(
    request: AnalysisRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> JobAccepted:
    return _accepted(
        "limit_up.analyze",
        {
            "codes": request.codes,
            "trade_date": request.trade_date or date.today().strftime("%Y%m%d"),
        },
        request.force,
        session,
        user,
        settings,
    )


@router.get("/ipos")
def ipo_stocks(
    start_date: str | None = None,
    end_date: str | None = None,
    force_refresh: bool = False,
    session: Session = Depends(get_session),
    _user: User = Depends(current_user),
) -> dict[str, Any]:
    today = date.today()
    start = _date(start_date, today)
    end = _date(end_date, start)
    if start > end:
        start, end = end, start
    items = build_ipo_items(session, start, end)
    return {
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
        "stocks": items,
        "items": items,
        "count": len(items),
        "source": "v2_database",
        "refresh_requested": bool(force_refresh),
    }


@router.post("/ipos/analyses", response_model=JobAccepted, status_code=202)
def analyze_ipos(
    request: AnalysisRequest,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    settings: Settings = Depends(settings_dep),
) -> JobAccepted:
    return _accepted(
        "ipo.analyze",
        {
            "codes": request.codes,
            "analysis_date": request.trade_date or date.today().strftime("%Y%m%d"),
        },
        request.force,
        session,
        user,
        settings,
    )

