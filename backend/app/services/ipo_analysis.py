from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Instrument, IpoEvent
from backend.app.domain.research_prompts import (
    IPO_ANALYST_SYSTEM_PROMPT,
    IPO_DECISION_SYSTEM_PROMPT,
    PROMPT_VERSION,
    ipo_analyst_prompt,
    ipo_decision_prompt,
)
from backend.app.providers.ai import AIProvider, AIRequest
from backend.app.services.bar_access import load_daily_bars
from backend.app.services.structured_ai import GeneratedReport, failed_report, generate_validated_report

ANALYSIS_VERSION = "ipo-v2.0"


class IpoAnalysisService:
    def __init__(
        self,
        session: Session,
        ai_provider: AIProvider | None = None,
    ):
        self.session = session
        self.ai_provider = ai_provider

    def build_snapshot(self, code: str, analysis_date: date) -> dict[str, Any]:
        event, instrument = self.session.execute(
            select(IpoEvent, Instrument)
            .outerjoin(Instrument, Instrument.id == IpoEvent.instrument_id)
            .where(IpoEvent.legacy_code == code)
            .order_by(IpoEvent.listing_date.desc())
            .limit(1)
        ).one_or_none() or (None, None)
        if event is None:
            raise ValueError(f"v2 新股事件中不存在 {code}")

        snapshot = dict(event.raw_payload or {})
        snapshot.update(
            {
                "code": code,
                "ts_code": instrument.ts_code if instrument else snapshot.get("ts_code"),
                "name": snapshot.get("name") or (instrument.name if instrument else code),
                "industry": snapshot.get("industry")
                or (instrument.industry.name if instrument and instrument.industry else None),
                "market": instrument.exchange if instrument else snapshot.get("market"),
                "area": instrument.area if instrument else snapshot.get("area"),
                "list_date": event.listing_date.isoformat(),
                "ipo_price": snapshot.get("ipo_price") or event.issue_price,
                "ipo_pe": snapshot.get("ipo_pe") or event.issue_pe,
                "analysis_date": analysis_date.isoformat(),
                "input_source": "v2_ipo_events",
                "analysis_version": ANALYSIS_VERSION,
            }
        )
        snapshot["market_confirmation"] = self._market_confirmation(
            event.instrument_id, event.listing_date, analysis_date
        )
        snapshot["missing_fields"] = [
            key
            for key in (
                "roe",
                "net_profit_margin",
                "gross_margin",
                "revenue_growth",
                "profit_growth",
                "debt_ratio",
            )
            if snapshot.get(key) in (None, "")
        ]
        return snapshot

    async def generate_reports(self, snapshot: dict[str, Any]) -> tuple[str, str, bool]:
        if self.ai_provider is None:
            return (
                "",
                "AI 报告未生成：请在设置页配置并测试 DeepSeek API Key。输入快照已保存，可稍后重试。",
                True,
            )
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
        analyst = await self.ai_provider.generate(
            AIRequest(
                system_prompt=IPO_ANALYST_SYSTEM_PROMPT,
                user_prompt=ipo_analyst_prompt(payload),
                temperature=0.4,
            )
        )
        decision = await self.ai_provider.generate(
            AIRequest(
                system_prompt=IPO_DECISION_SYSTEM_PROMPT,
                user_prompt=ipo_decision_prompt(analyst),
                temperature=0.3,
            )
        )
        return analyst, decision, False

    async def generate_structured_reports(
        self, snapshot: dict[str, Any]
    ) -> tuple[GeneratedReport, GeneratedReport]:
        if self.ai_provider is None:
            message = "AI provider is not configured; no successful report was generated."
            return failed_report(message, snapshot), failed_report(message, snapshot)
        confirmation = dict(snapshot.get("market_confirmation") or {})
        authoritative = {
            "code": snapshot.get("code"),
            "list_date": snapshot.get("list_date"),
            "analysis_date": snapshot.get("analysis_date"),
            "market_confirmation": confirmation,
            "missing_fields": snapshot.get("missing_fields") or [],
            "data_time": confirmation.get("data_as_of") or snapshot.get("analysis_date"),
            "input_freshness": "missing" if confirmation.get("status") == "no_bars" else "fresh",
        }
        payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
        analyst = await generate_validated_report(
            self.ai_provider,
            system_prompt=IPO_ANALYST_SYSTEM_PROMPT,
            user_prompt=ipo_analyst_prompt(payload),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
        )
        if not analyst.successful:
            return analyst, failed_report(
                "Analyst report validation failed; review was not generated.", snapshot
            )
        review = await generate_validated_report(
            self.ai_provider,
            system_prompt=IPO_DECISION_SYSTEM_PROMPT,
            user_prompt=ipo_decision_prompt(analyst.markdown),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
            independent_review=False,
        )
        return analyst, review

    def _market_confirmation(
        self,
        instrument_id: int | None,
        listing_date: date,
        analysis_date: date,
    ) -> dict[str, Any]:
        if instrument_id is None:
            return {"status": "no_instrument"}
        rows, adjustment = load_daily_bars(
            self.session,
            instrument_id,
            start=datetime.combine(listing_date, datetime.min.time()),
            end=datetime.combine(analysis_date, datetime.max.time()),
            limit=30,
        )
        if not rows:
            return {"status": "no_bars"}
        first, latest = rows[0], rows[-1]
        return {
            "status": "available",
            "adjustment": adjustment,
            "data_as_of": latest.bar_time.date().isoformat(),
            "trading_days": len(rows),
            "first_close": first.close,
            "latest_close": latest.close,
            "return_since_first_close_pct": round(
                (latest.close / first.close - 1) * 100 if first.close else 0, 2
            ),
            "average_turnover_rate": (
                round(
                    sum(row.turnover_rate for row in rows if row.turnover_rate is not None)
                    / sum(row.turnover_rate is not None for row in rows),
                    2,
                )
                if any(row.turnover_rate is not None for row in rows)
                else None
            ),
            "average_amount": round(sum(row.amount or 0 for row in rows) / len(rows), 2),
        }


def report_metadata() -> dict[str, str]:
    return {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "prompt_version": PROMPT_VERSION,
    }
