from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import AutomationSchedule
from backend.app.db.models.common import utcnow
from backend.app.repositories.jobs import JobRepository

TIMEZONE = ZoneInfo("Asia/Shanghai")
ALLOWED_SCHEDULE_JOB_KINDS = {"market_radar.refresh", "limit_up.refresh", "ipo.refresh", "data.refresh", "screen.hot", "screen.smart"}
DEFAULT_SCHEDULES = (
    {
        "key": "weekday-data-refresh",
        "job_kind": "data.refresh",
        "hour": 16,
        "minute": 30,
        "weekdays": [1, 2, 3, 4, 5],
    },
    {
        "key": "weekday-hot-screen",
        "job_kind": "screen.hot",
        "hour": 16,
        "minute": 10,
        "weekdays": [1, 2, 3, 4, 5],
        "payload": {
            "rank_type": "top_gainers",
            "top_n": 200,
            "types": ["2", "3a", "3b"],
            "scan_side": "buy",
            "codes": [],
            "industries": [],
            "areas": [],
            "exclude_st": True,
        },
    },
    {
        "key": "weekday-smart-screen",
        "job_kind": "screen.smart",
        "hour": 16,
        "minute": 20,
        "weekdays": [1, 2, 3, 4, 5],
        "payload": {
            "scan_side": "buy",
            "types": ["2", "3a", "3b"],
            "codes": [],
            "industries": [],
            "areas": [],
            "exclude_st": True,
            "rank_type": "top_gainers",
            "top_n": 200,
        },
    },
    {
        "key": "weekday-limit-up-refresh",
        "job_kind": "limit_up.refresh",
        "hour": 16,
        "minute": 50,
        "weekdays": [1, 2, 3, 4, 5],
    },
    {
        "key": "weekday-ipo-refresh",
        "job_kind": "ipo.refresh",
        "hour": 17,
        "minute": 10,
        "weekdays": [1, 2, 3, 4, 5],
        "payload": {"lookback_days": 365, "lookahead_days": 90},
    },
    {
        "key": "weekday-radar-refresh",
        "job_kind": "market_radar.refresh",
        "hour": 18,
        "minute": 0,
        "weekdays": [1, 2, 3, 4, 5],
    },
)


def build_cron(hour: int, minute: int, weekdays: list[int]) -> str:
    normalized = sorted(set(weekdays))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("执行时间无效")
    if not normalized or any(day < 1 or day > 7 for day in normalized):
        raise ValueError("执行星期必须位于 1—7")
    return f"{minute} {hour} * * {','.join(str(day) for day in normalized)}"


def parse_cron(expression: str) -> tuple[int, int, list[int]]:
    parts = expression.split()
    if len(parts) != 5 or parts[2:4] != ["*", "*"]:
        raise ValueError("仅支持按星期的日内定时任务")
    minute, hour = int(parts[0]), int(parts[1])
    weekdays = [int(value) for value in parts[4].split(",")]
    build_cron(hour, minute, weekdays)
    return hour, minute, weekdays


def next_run(hour: int, minute: int, weekdays: list[int], after: datetime | None = None) -> datetime:
    current_utc = (after or utcnow()).replace(tzinfo=UTC)
    current_local = current_utc.astimezone(TIMEZONE)
    for offset in range(8):
        candidate_date = current_local.date() + timedelta(days=offset)
        if candidate_date.isoweekday() not in weekdays:
            continue
        candidate = datetime.combine(candidate_date, time(hour, minute), tzinfo=TIMEZONE)
        if candidate > current_local:
            return candidate.astimezone(UTC).replace(tzinfo=None)
    raise ValueError("无法计算下次执行时间")


def schedule_view(schedule: AutomationSchedule) -> dict[str, object]:
    hour, minute, weekdays = parse_cron(schedule.cron_expression)
    next_local = (
        schedule.next_run_at.replace(tzinfo=UTC).astimezone(TIMEZONE).isoformat()
        if schedule.next_run_at
        else None
    )
    last_local = (
        schedule.last_run_at.replace(tzinfo=UTC).astimezone(TIMEZONE).isoformat()
        if schedule.last_run_at
        else None
    )
    return {
        "id": schedule.id,
        "key": schedule.key,
        "job_kind": schedule.job_kind,
        "hour": hour,
        "minute": minute,
        "weekdays": weekdays,
        "timezone": schedule.timezone,
        "enabled": schedule.enabled,
        "payload": schedule.payload or {},
        "last_run_at": last_local,
        "next_run_at": next_local,
        "updated_at": schedule.updated_at,
    }


def ensure_default_schedules(session: Session) -> list[AutomationSchedule]:
    existing = {item.key: item for item in session.scalars(select(AutomationSchedule)).all()}
    for config in DEFAULT_SCHEDULES:
        current = existing.get(config["key"])
        if current is not None:
            if (
                current.key == "weekday-radar-refresh"
                and not current.enabled
                and current.cron_expression == "10 17 * * 1,2,3,4,5"
            ):
                current.cron_expression = build_cron(18, 0, [1, 2, 3, 4, 5])
                session.commit()
            continue
        schedule = AutomationSchedule(
            key=str(config["key"]),
            job_kind=str(config["job_kind"]),
            cron_expression=build_cron(
                int(config["hour"]),
                int(config["minute"]),
                list(config["weekdays"]),
            ),
            timezone="Asia/Shanghai",
            enabled=False,
            payload=dict(config.get("payload") or {}),
        )
        session.add(schedule)
        existing[schedule.key] = schedule
    session.commit()
    return sorted(existing.values(), key=lambda item: item.key)


def upsert_schedule(
    session: Session,
    key: str,
    job_kind: str,
    hour: int,
    minute: int,
    weekdays: list[int],
    enabled: bool,
    payload: dict[str, object] | None,
) -> AutomationSchedule:
    if job_kind not in ALLOWED_SCHEDULE_JOB_KINDS:
        raise ValueError("不支持该自动任务类型")
    expression = build_cron(hour, minute, weekdays)
    schedule = session.scalar(select(AutomationSchedule).where(AutomationSchedule.key == key))
    if schedule is None:
        schedule = AutomationSchedule(key=key)
        session.add(schedule)
    schedule.job_kind = job_kind
    schedule.cron_expression = expression
    schedule.timezone = "Asia/Shanghai"
    schedule.enabled = enabled
    schedule.payload = payload or {}
    schedule.next_run_at = next_run(hour, minute, weekdays) if enabled else None
    session.commit()
    return schedule


def enqueue_due_schedules(session: Session, max_attempts: int) -> int:
    now = utcnow()
    due = session.scalars(
        select(AutomationSchedule).where(
            AutomationSchedule.enabled.is_(True),
            AutomationSchedule.next_run_at.is_not(None),
            AutomationSchedule.next_run_at <= now,
        )
    ).all()
    enqueued = 0
    for schedule in due:
        hour, minute, weekdays = parse_cron(schedule.cron_expression)
        payload = {**(schedule.payload or {}), "automation_schedule": schedule.key}
        _job, deduplicated = JobRepository(session).create(
            schedule.job_kind,
            payload,
            user_id=None,
            force=False,
            max_attempts=max_attempts,
        )
        if not deduplicated:
            enqueued += 1
        schedule.last_run_at = now
        schedule.next_run_at = next_run(hour, minute, weekdays, after=now)
        session.commit()
    return enqueued
