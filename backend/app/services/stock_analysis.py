from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.app.db.models import Instrument
from backend.app.domain.research_prompts import (
    STOCK_ANALYST_SYSTEM_PROMPT,
    STOCK_DECISION_SYSTEM_PROMPT,
    STOCK_PROMPT_VERSION,
    STOCK_REVIEW_SYSTEM_PROMPT,
    stock_analyst_prompt,
    stock_decision_prompt,
    stock_review_prompt,
)
from backend.app.providers.ai import AIProvider
from backend.app.services.data_health import expected_trade_date
from backend.app.services.stock_market_context import StockMarketContextService
from backend.app.services.structured_ai import (
    GeneratedReport,
    failed_report,
    generate_validated_report,
)


def _analysis_date(analysis: dict[str, Any]) -> date | None:
    raw = str(analysis.get("end_date") or "").strip().replace("/", "-")
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _recent(items: Any, *, date_key: str, limit: int) -> list[dict[str, Any]]:
    values = [dict(item) for item in items or [] if isinstance(item, dict)]
    values.sort(key=lambda item: str(item.get(date_key) or ""))
    return values[-limit:]


def compact_stock_snapshot(
    analysis: dict[str, Any],
    instrument: Instrument,
    *,
    algorithm_version: str,
) -> dict[str, Any]:
    return {
        "code": instrument.code,
        "name": instrument.name or instrument.code,
        "exchange": instrument.exchange,
        "industry": instrument.industry.name if instrument.industry else None,
        "algorithm_version": algorithm_version,
        "timeframe": analysis.get("kl_type"),
        "adjustment": analysis.get("adjustment"),
        "start_date": analysis.get("start_date"),
        "end_date": analysis.get("end_date"),
        "kline_count": analysis.get("kline_count"),
        "current_price": analysis.get("current_price"),
        "macd": analysis.get("macd"),
        "kline_range": analysis.get("kline_range"),
        "volume_analysis": analysis.get("volume_analysis"),
        "center_position": analysis.get("zs_position"),
        "latest_structure": analysis.get("latest"),
        "recent_buy_signals": _recent(
            analysis.get("buy_signals"), date_key="date", limit=10
        ),
        "recent_sell_signals": _recent(
            analysis.get("sell_signals"), date_key="date", limit=10
        ),
        "recent_strokes": _recent(
            analysis.get("bi_list"), date_key="end_date", limit=16
        ),
        "recent_segments": _recent(
            analysis.get("seg_list"), date_key="end_date", limit=10
        ),
        "recent_centers": _recent(
            analysis.get("zs_list"), date_key="end_date", limit=8
        ),
    }


class StockAnalysisService:
    def __init__(
        self,
        session: Session,
        ai_provider: AIProvider | None = None,
        market_context: StockMarketContextService | None = None,
    ):
        self.session = session
        self.ai_provider = ai_provider
        self.market_context = market_context or StockMarketContextService()

    def input_freshness(self, analysis: dict[str, Any]) -> tuple[str, date | None]:
        data_date = _analysis_date(analysis)
        expected = expected_trade_date(self.session)
        if data_date is None or expected is None:
            return "missing", expected
        return ("fresh" if data_date >= expected else "stale"), expected

    async def generate_structured_reports(
        self,
        analysis: dict[str, Any],
        instrument: Instrument,
        *,
        algorithm_version: str,
    ) -> tuple[dict[str, Any], tuple[GeneratedReport, GeneratedReport, GeneratedReport]]:
        snapshot = compact_stock_snapshot(
            analysis,
            instrument,
            algorithm_version=algorithm_version,
        )
        freshness, expected = self.input_freshness(analysis)
        snapshot["input_freshness"] = freshness
        snapshot["expected_trade_date"] = expected.isoformat() if expected else None
        snapshot.update(self.market_context.snapshot(instrument))
        if self.ai_provider is None:
            message = "AI Provider 未配置；请先在设置页配置并测试 DeepSeek 或硅基流动。"
            return snapshot, (
                failed_report(message, snapshot),
                failed_report(message, snapshot),
                failed_report(message, snapshot),
            )

        authoritative = {
            "code": instrument.code,
            "name": instrument.name or instrument.code,
            "data_time": snapshot.get("end_date"),
            "current_price": snapshot.get("current_price"),
            "algorithm_version": algorithm_version,
            "latest_structure": snapshot.get("latest_structure"),
            "recent_buy_signals": snapshot.get("recent_buy_signals"),
            "recent_sell_signals": snapshot.get("recent_sell_signals"),
            "input_freshness": freshness,
            "realtime_quote": snapshot.get("realtime_quote"),
            "money_flow": snapshot.get("money_flow"),
        }
        snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
        analyst = await generate_validated_report(
            self.ai_provider,
            system_prompt=STOCK_ANALYST_SYSTEM_PROMPT,
            user_prompt=stock_analyst_prompt(snapshot_json),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
        )
        if not analyst.successful:
            return snapshot, (
                analyst,
                failed_report("分析师报告未通过校验，未生成独立复核。", snapshot),
                failed_report("分析师报告未通过校验，未生成综合决策。", snapshot),
            )
        review = await generate_validated_report(
            self.ai_provider,
            system_prompt=STOCK_REVIEW_SYSTEM_PROMPT,
            user_prompt=stock_review_prompt(snapshot_json),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
            independent_review=True,
        )
        if not review.successful:
            return snapshot, (
                analyst,
                review,
                failed_report("独立风控复核未通过校验，未生成综合决策。", snapshot),
            )
        decision = await generate_validated_report(
            self.ai_provider,
            system_prompt=STOCK_DECISION_SYSTEM_PROMPT,
            user_prompt=stock_decision_prompt(snapshot_json, analyst.markdown, review.markdown),
            authoritative_rule_fields=authoritative,
            snapshot=snapshot,
        )
        return snapshot, (analyst, review, decision)



def report_metadata() -> dict[str, str]:
    return {
        "provider": "deepseek",
        "model": "configured-provider-chain",
        "prompt_version": STOCK_PROMPT_VERSION,
    }