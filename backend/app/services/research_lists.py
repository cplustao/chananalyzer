from __future__ import annotations

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import (
    AnalysisReport,
    AnalysisRun,
    Instrument,
    LimitUpEvent,
    LimitUpMetric,
)


def _raw_value(raw: dict[str, Any], *keys: str) -> Any:
    sources = [raw]
    eastmoney = raw.get("eastmoney_limit_up_pool")
    if isinstance(eastmoney, dict):
        sources.append(eastmoney)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def _limit_up_analysis_map(
    session: Session,
    event_ids: list[int],
) -> dict[int, dict[str, Any]]:
    if not event_ids:
        return {}
    rows = session.execute(
        select(LimitUpMetric, AnalysisRun)
        .join(AnalysisRun, AnalysisRun.id == LimitUpMetric.analysis_run_id)
        .where(LimitUpMetric.limit_up_event_id.in_(event_ids))
        .order_by(AnalysisRun.created_at.desc())
    ).all()
    result: dict[int, dict[str, Any]] = {}
    for metric, run in rows:
        if metric.limit_up_event_id in result:
            continue
        result[metric.limit_up_event_id] = {
            "run_id": run.id,
            "status": run.status,
            "day_score": metric.day_score,
            "week_score": metric.week_score,
            "value_score": metric.value_score,
            "market_score": metric.market_score,
            "timing_score": metric.timing_score,
            "risk_score": metric.risk_score,
            "overall_score": metric.overall_score,
            "day_classification": metric.day_classification,
            "week_classification": metric.week_classification,
        }
    return result


def build_limit_up_items(
    session: Session,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    rows = session.execute(
        select(LimitUpEvent, Instrument)
        .outerjoin(Instrument, Instrument.id == LimitUpEvent.instrument_id)
        .where(LimitUpEvent.trade_date.between(start, end))
        .order_by(LimitUpEvent.trade_date.desc(), LimitUpEvent.pct_change.desc())
    ).all()
    if not rows:
        return []

    trading_dates = sorted(
        set(
            session.scalars(
                select(LimitUpEvent.trade_date)
                .where(LimitUpEvent.trade_date <= end)
                .distinct()
                .order_by(LimitUpEvent.trade_date.desc())
                .limit(180)
            ).all()
        )
    )
    date_index = {trade_date: index for index, trade_date in enumerate(trading_dates)}
    occurrences: dict[str, set[date]] = {}
    if trading_dates:
        for code, trade_date in session.execute(
            select(LimitUpEvent.legacy_code, LimitUpEvent.trade_date).where(
                LimitUpEvent.trade_date.between(trading_dates[0], end)
            )
        ):
            occurrences.setdefault(code, set()).add(trade_date)

    analyses = _limit_up_analysis_map(session, [event.id for event, _ in rows])
    items: list[dict[str, Any]] = []
    for event, instrument in rows:
        raw = dict(event.raw_payload or {})
        trade_index = date_index.get(event.trade_date, -1)
        code_dates = occurrences.get(event.legacy_code, set())
        counts = {}
        for window in (7, 30, 90, 180):
            window_dates = set(
                trading_dates[max(0, trade_index - window + 1): trade_index + 1]
            ) if trade_index >= 0 else set()
            counts[f"limit_up_count_{window}d"] = len(code_dates & window_dates)
        items.append(
            {
                **raw,
                "instrument_id": event.instrument_id,
                "code": event.legacy_code,
                "name": instrument.name if instrument else _raw_value(raw, "name", "名称"),
                "industry": (
                    instrument.industry.name
                    if instrument and instrument.industry
                    else _raw_value(raw, "industry", "所属行业")
                ),
                "trade_date": event.trade_date.strftime("%Y%m%d"),
                "limit_up_date": event.trade_date.strftime("%Y%m%d"),
                "market": event.market,
                "theme": event.theme,
                "reason": event.reason,
                "close": event.close,
                "pct_chg": event.pct_change,
                "turnover_rate": event.turnover_rate,
                "consecutive_boards": event.consecutive_boards,
                "limit_order": _raw_value(raw, "limit_order", "封板资金", "封单金额"),
                "amount": _raw_value(raw, "amount", "成交额"),
                "first_limit_time": _raw_value(raw, "first_limit_time", "首次封板时间"),
                "last_limit_time": _raw_value(raw, "last_limit_time", "最后封板时间"),
                "is_one_word": bool(_raw_value(raw, "is_one_word", "一字板") or False),
                "analysis": analyses.get(event.id, {"status": "pending"}),
                "quarantined": event.quarantined,
                **counts,
            }
        )
    return items


def _potential_score(content: str, multiplier: int) -> float | None:
    normalized = re.sub(r"\*\*|`", "", content)
    match = re.search(
        rf"{multiplier}\s*倍\s*(?:潜力)?\s*(?:评分|得分)?\s*[：:]?\s*"
        r"(\d+(?:\.\d+)?)\s*(?:[/／]\s*10|分)?",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    score = float(match.group(1))
    return score if 0 <= score <= 10 else None


def enrich_ipo_items(
    session: Session,
    raw_items: list[dict[str, Any]],
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    codes = {
        str(item.get("code") or item.get("ts_code") or "").split(".")[0]
        for item in raw_items
    }
    instruments = {
        item.code: item
        for item in session.scalars(
            select(Instrument).where(Instrument.code.in_(codes))
        ).all()
    } if codes else {}
    runs = list(
        session.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.kind == "ipo",
                AnalysisRun.subject_date.between(start, end),
            )
            .order_by(AnalysisRun.created_at.desc())
        ).all()
    )
    run_ids = [run.id for run in runs]
    reports: dict[str, list[str]] = {}
    if run_ids:
        for report in session.scalars(
            select(AnalysisReport).where(AnalysisReport.analysis_run_id.in_(run_ids))
        ):
            reports.setdefault(report.analysis_run_id, []).append(report.content)

    instrument_codes = {item.id: item.code for item in instruments.values()}
    analyses: dict[str, dict[str, Any]] = {}
    for run in runs:
        snapshot = dict(run.input_snapshot or {})
        code = instrument_codes.get(run.instrument_id) or str(
            snapshot.get("code") or snapshot.get("ts_code") or ""
        ).split(".")[0]
        if not code or code in analyses:
            continue
        content = "\n".join(reports.get(run.id, []))
        analyses[code] = {
            "run_id": run.id,
            "status": run.status,
            "ten_x_score": _potential_score(content, 10),
            "hundred_x_score": _potential_score(content, 100),
        }

    result: list[dict[str, Any]] = []
    for raw in raw_items:
        item = dict(raw)
        code = str(item.get("code") or item.get("ts_code") or "").split(".")[0]
        instrument = instruments.get(code)
        item.update(
            {
                "instrument_id": instrument.id if instrument else None,
                "code": code,
                "name": item.get("name") or (instrument.name if instrument else None),
                "industry": item.get("industry") or (
                    instrument.industry.name
                    if instrument and instrument.industry
                    else None
                ),
                "analysis": analyses.get(code, {"status": "pending"}),
            }
        )
        result.append(item)
    return result
