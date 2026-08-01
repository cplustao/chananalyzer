from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import Instrument, LimitUpEvent
from backend.app.domain.limit_up_scoring import (
    LimitUpSubject,
    build_quantitative_analysis,
)
from backend.app.domain.research_prompts import (
    LIMIT_UP_ANALYST_SYSTEM_PROMPT,
    LIMIT_UP_DECISION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    limit_up_analyst_prompt,
    limit_up_decision_prompt,
)
from backend.app.providers.ai import AIProvider, AIRequest
from backend.app.services.bar_access import load_daily_bars
from backend.app.services.structured_ai import GeneratedReport, failed_report, generate_validated_report


def _raw_value(raw: dict[str, Any], *keys: str) -> Any:
    sources = [raw]
    nested = raw.get("eastmoney_limit_up_pool")
    if isinstance(nested, dict):
        sources.append(nested)
    for source in sources:
        for key in keys:
            if source.get(key) not in (None, ""):
                return source[key]
    return None


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return None if number != number else number
    except (TypeError, ValueError):
        return None


class LimitUpAnalysisService:
    def __init__(
        self,
        session: Session,
        ai_provider: AIProvider | None = None,
    ):
        self.session = session
        self.ai_provider = ai_provider

    def build_quantitative(self, trade_date: date, code: str) -> dict[str, Any]:
        row = self.session.execute(
            select(LimitUpEvent, Instrument)
            .outerjoin(Instrument, Instrument.id == LimitUpEvent.instrument_id)
            .where(LimitUpEvent.trade_date == trade_date, LimitUpEvent.legacy_code == code)
        ).one_or_none()
        if row is None:
            raise ValueError(f"{trade_date.isoformat()} 涨停事件中不存在 {code}")
        event, instrument = row
        raw = dict(event.raw_payload or {})
        subject = LimitUpSubject(
            code=code,
            ts_code=instrument.ts_code if instrument else None,
            name=instrument.name if instrument else str(_raw_value(raw, "name", "名称") or code),
            industry=(
                instrument.industry.name
                if instrument and instrument.industry
                else str(_raw_value(raw, "industry", "所属行业") or "未分类")
            ),
            trade_date=trade_date.strftime("%Y%m%d"),
            reason=event.reason,
            consecutive_boards=event.consecutive_boards or 1,
            turnover_rate=event.turnover_rate,
            limit_order=_number(_raw_value(raw, "limit_order", "封板资金", "封单金额")),
            circ_mv=_number(_raw_value(raw, "circ_mv", "流通市值")),
            pe=_number(_raw_value(raw, "pe", "市盈率")),
            pb=_number(_raw_value(raw, "pb", "市净率")),
            industry_mv_percentile=_number(raw.get("industry_mv_percentile")),
            industry_pe_percentile=_number(raw.get("industry_pe_percentile")),
            industry_pb_percentile=_number(raw.get("industry_pb_percentile")),
            raw_payload=raw,
        )
        total_count = int(
            self.session.scalar(
                select(func.count()).select_from(LimitUpEvent).where(LimitUpEvent.trade_date == trade_date)
            )
            or 0
        )
        sector_count = len(
            [
                candidate
                for candidate in self.session.scalars(
                    select(LimitUpEvent).where(LimitUpEvent.trade_date == trade_date)
                )
                if str(
                    _raw_value(dict(candidate.raw_payload or {}), "industry", "所属行业")
                    or candidate.theme
                    or "未分类"
                )
                == subject.industry
            ]
        )
        return build_quantitative_analysis(
            subject,
            self._daily(event.instrument_id, trade_date),
            fundamentals=dict(raw.get("fundamentals") or {}),
            money=dict(raw.get("money_flow") or {}),
            information={
                "items": list(raw.get("news") or []),
                "event_reason": event.reason,
                "source": "v2_event_snapshot",
            },
            sector_count=sector_count,
            total_count=total_count,
        )

    async def generate_reports(self, result: dict[str, Any]) -> tuple[str, str, bool]:
        if self.ai_provider is None:
            return (
                "",
                "AI 报告未生成：请在设置页配置并测试 DeepSeek API Key。量化评分与风控计划已保存。",
                True,
            )
        snapshot_json = json.dumps(result["snapshot"], ensure_ascii=False, default=str)
        analyst = await self.ai_provider.generate(
            AIRequest(
                system_prompt=LIMIT_UP_ANALYST_SYSTEM_PROMPT,
                user_prompt=limit_up_analyst_prompt(snapshot_json),
                temperature=0.25,
                max_tokens=2200,
            )
        )
        summary = json.dumps(
            {
                "trade_date": result["trade_date"],
                "code": result["code"],
                "scores": result["snapshot"]["scores"],
                "classifications": result["snapshot"]["classifications"],
                "risk_plan": result["risk_plan"],
                "data_completeness": result["completeness"],
            },
            ensure_ascii=False,
        )
        decision = await self.ai_provider.generate(
            AIRequest(
                system_prompt=LIMIT_UP_DECISION_SYSTEM_PROMPT,
                user_prompt=limit_up_decision_prompt(summary, analyst),
                temperature=0.2,
                max_tokens=2200,
            )
        )
        return analyst, decision, False

    async def generate_structured_reports(
        self, result: dict[str, Any]
    ) -> tuple[GeneratedReport, GeneratedReport]:
        snapshot = dict(result.get("snapshot") or {})
        if self.ai_provider is None:
            message = "AI provider is not configured; no successful report was generated."
            return failed_report(message, snapshot), failed_report(message, snapshot)
        authoritative = {
            "trade_date": result.get("trade_date"),
            "code": result.get("code"),
            "scores": snapshot.get("scores"),
            "classifications": snapshot.get("classifications"),
            "risk_plan": result.get("risk_plan"),
            "data_time": result.get("trade_date"),
            "input_freshness": "fresh",
        }
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
        analyst = await generate_validated_report(
            self.ai_provider,
            system_prompt=LIMIT_UP_ANALYST_SYSTEM_PROMPT,
            user_prompt=limit_up_analyst_prompt(snapshot_json),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
        )
        if not analyst.successful:
            return analyst, failed_report(
                "Analyst report validation failed; review was not generated.", snapshot
            )
        review = await generate_validated_report(
            self.ai_provider,
            system_prompt=LIMIT_UP_DECISION_SYSTEM_PROMPT,
            user_prompt=limit_up_decision_prompt(
                json.dumps(authoritative, ensure_ascii=False, default=str), analyst.markdown
            ),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
            independent_review=False,
        )
        return analyst, review

    def _daily(self, instrument_id: int | None, trade_date: date) -> pd.DataFrame:
        if instrument_id is None:
            return pd.DataFrame()
        descending, _adjustment = load_daily_bars(
            self.session,
            instrument_id,
            end=datetime.combine(trade_date, datetime.max.time()),
            limit=250,
            descending=True,
        )
        rows = list(reversed(descending))
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "date": row.bar_time,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "amount": row.amount or 0,
                    "turnover_rate": row.turnover_rate,
                }
                for row in rows
            ]
        ).set_index("date")


def report_metadata() -> dict[str, str]:
    return {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "prompt_version": PROMPT_VERSION,
    }
