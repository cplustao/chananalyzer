from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import Bar, Instrument, LimitUpEvent, RadarSnapshot
from backend.app.services.bar_access import preferred_adjustment

ALGORITHM_VERSION = "2.0"


def _safe(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return sum(items) / len(items) if items else None


def _median(values: Iterable[float]) -> float | None:
    items = list(values)
    return statistics.median(items) if items else None


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _status(score: float) -> tuple[str, str, dict[str, float]]:
    if score >= 68:
        return "strong", "强势", {"min": 0.6, "max": 0.85}
    if score >= 58:
        return "warm", "偏强", {"min": 0.45, "max": 0.7}
    if score >= 45:
        return "neutral", "震荡", {"min": 0.3, "max": 0.55}
    if score >= 35:
        return "weak", "偏弱", {"min": 0.15, "max": 0.35}
    return "risk", "风险", {"min": 0.0, "max": 0.2}


class MarketRadarService:
    """Calculate and persist the market radar exclusively from v2 tables."""

    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def refresh(self) -> dict[str, Any]:
        cached_count, histories, latest_date, adjustment_counts = self._histories()
        latest_limit_date = self.session.scalar(select(func.max(LimitUpEvent.trade_date)))
        if not latest_limit_date or latest_limit_date < latest_date.date():
            limit_label = latest_limit_date.isoformat() if latest_limit_date else "无数据"
            raise ValueError(
                f"涨停原始事件仅到 {limit_label}，落后于行情 {latest_date.date()}；请先刷新涨停事件"
            )
        rows = self._daily_rows(histories, latest_date)
        if not rows:
            raise ValueError("v2 日线数据为空，请先执行行情刷新")

        changes = [row["change"] for row in rows if row["change"] is not None]
        returns_5d = [row["return_5d"] for row in rows if row["return_5d"] is not None]
        advancers = sum(value > 0.05 for value in changes)
        decliners = sum(value < -0.05 for value in changes)
        flat = max(0, len(changes) - advancers - decliners)
        ma20_rows = [row for row in rows if row["has_ma20"]]
        above_ma20 = sum(row["above_ma20"] for row in ma20_rows)
        advance_ratio = _ratio(advancers, len(changes)) or 0.0
        decline_ratio = _ratio(decliners, len(changes)) or 0.0
        ma20_ratio = _ratio(above_ma20, len(ma20_rows))
        current_coverage = _ratio(len(rows), cached_count) or 0.0
        ma20_coverage = _ratio(len(ma20_rows), len(rows)) or 0.0
        freshness = (
            "fresh"
            if current_coverage >= self.settings.radar_min_current_coverage
            and ma20_coverage >= self.settings.radar_min_ma20_coverage
            else "partial"
        )
        median_change = _median(changes)
        average_change = _mean(changes)
        return_dispersion = statistics.pstdev(changes) if len(changes) >= 2 else None
        median_5d = _median(returns_5d)
        amount = sum(row["amount"] for row in rows)
        previous_amount = sum(row["previous_amount"] for row in rows)
        amount_ratio = _ratio(amount, previous_amount)
        average_turnover = _mean(row["turnover_rate"] for row in rows if row["turnover_rate"] is not None)
        estimated_limit_up = sum(value >= 9.5 for value in changes)
        limit_down_count = sum(value <= -9.5 for value in changes)
        ecology = self._limit_ecology(latest_date, estimated_limit_up)

        trend_score = _clamp(
            50 + (median_change or 0) * 12 + ((ma20_ratio or 0.5) - 0.5) * 45 + (median_5d or 0) * 3
        )
        breadth_score = _clamp(50 + (advance_ratio - decline_ratio) * 65)
        liquidity_score = _clamp(50 + ((amount_ratio or 1) - 1) * 80)
        limit_score = _clamp(
            48
            + (ecology["limit_up_count"] - limit_down_count) * 0.45
            + max(0, ecology["highest_board"] - 2) * 4
            + ecology["multi_board_count"] * 0.6
        )
        industry_score = _clamp(
            38
            + ecology["focus_industry_count"] * 9
            + ecology["active_industry_count"] * 4
            + min(ecology["industry_count"], 20) * 0.6
        )
        score = round(
            trend_score * 0.25
            + breadth_score * 0.25
            + liquidity_score * 0.2
            + limit_score * 0.2
            + industry_score * 0.1,
            1,
        )
        status, status_label, position_range = _status(score)
        evidence, counter = self._evidence(advance_ratio, ma20_ratio, amount_ratio, ecology, limit_down_count)
        components = [
            {
                "key": "equal_weight_trend",
                "label": "全市场等权趋势",
                "score": round(trend_score, 1),
                "summary": f"中位涨跌幅 {_round(median_change)}%，5日中位涨跌幅 {_round(median_5d)}%",
                "metrics": {
                    "median_change": _round(median_change),
                    "average_change": _round(average_change),
                    "median_return_5d": _round(median_5d),
                    "above_ma20_rate": _round(ma20_ratio, 4),
                },
            },
            {
                "key": "market_breadth",
                "label": "市场宽度",
                "score": round(breadth_score, 1),
                "summary": f"上涨 {advancers} / 下跌 {decliners} / 平盘 {flat}",
                "metrics": {
                    "advancers": advancers,
                    "decliners": decliners,
                    "flat": flat,
                    "advance_rate": _round(advance_ratio, 4),
                },
            },
            {
                "key": "turnover_liquidity",
                "label": "成交活跃度",
                "score": round(liquidity_score, 1),
                "summary": f"成交额 {_round(amount / 100000)} 亿元，环比 {_round(((amount_ratio or 1) - 1) * 100)}%",
                "metrics": {
                    "amount_yi": _round(amount / 100000),
                    "previous_amount_yi": _round(previous_amount / 100000),
                    "amount_ratio": _round(amount_ratio, 4),
                    "average_turnover_rate": _round(average_turnover),
                },
            },
            {
                "key": "limit_ecology",
                "label": "涨跌停生态",
                "score": round(limit_score, 1),
                "summary": f"涨停 {ecology['limit_up_count']} / 跌停估算 {limit_down_count} / 最高 {ecology['highest_board']} 板",
                "metrics": {
                    "limit_up_count": ecology["limit_up_count"],
                    "limit_down_count": limit_down_count,
                    "highest_board": ecology["highest_board"],
                    "multi_board_count": ecology["multi_board_count"],
                },
            },
            {
                "key": "industry_diffusion",
                "label": "行业扩散",
                "score": round(industry_score, 1),
                "summary": f"涨停覆盖 {ecology['industry_count']} 个行业，{ecology['focus_industry_count']} 个聚焦行业",
                "metrics": {
                    "industry_count": ecology["industry_count"],
                    "focus_industry_count": ecology["focus_industry_count"],
                    "active_industry_count": ecology["active_industry_count"],
                },
            },
        ]
        snapshot: dict[str, Any] = {
            "trade_date": latest_date.date().isoformat(),
            "generated_at": datetime.now().astimezone().isoformat(),
            "source": "v2 日线 + v2 涨停事件",
            "regime": {
                "status": status,
                "status_label": status_label,
                "score": score,
                "position_range": position_range,
            },
            "freshness": freshness,
            "coverage": {
                "current_count": len(rows),
                "cached_count": cached_count,
                "rate": round(current_coverage, 4),
                "ma20_count": len(ma20_rows),
                "ma20_rate": round(ma20_coverage, 4),
                "current_threshold": self.settings.radar_min_current_coverage,
                "ma20_threshold": self.settings.radar_min_ma20_coverage,
            },
            "breadth": {
                "advancers": advancers,
                "decliners": decliners,
                "flat": flat,
                "advance_rate": round(advance_ratio, 4),
                "decline_rate": round(decline_ratio, 4),
                "return_dispersion": _round(return_dispersion),
                "above_ma20_count": above_ma20,
                "above_ma20_rate": _round(ma20_ratio, 4),
                "limit_down_count": limit_down_count,
            },
            "liquidity": {
                "amount_yi": _round(amount / 100000),
                "previous_amount_yi": _round(previous_amount / 100000),
                "amount_ratio": _round(amount_ratio, 4),
                "average_turnover_rate": _round(average_turnover),
            },
            "methodology": {
                "trend_benchmark": "equal_weight_proxy",
                "trend_benchmark_label": "全市场等权趋势（非大盘指数）",
                "bar_adjustments": adjustment_counts,
                "notes": [
                    "沪深样本使用前复权日线；北交所因当前数据源限制使用不复权日线。",
                    "核心指数行情尚未接入，趋势维度使用全市场等权指标替代。",
                ],
            },
            "components": components,
            "evidence": evidence,
            "counter_evidence": counter,
            "limit_ecology": ecology,
            "missing_fields": [
                "核心指数行情未缓存，趋势维度使用全市场等权指标替代",
                "行业字段覆盖不足时，行业强弱按涨停事件快照计算",
            ],
        }
        if freshness != "fresh":
            snapshot["regime"].pop("position_range", None)
            snapshot["regime"]["position_unavailable_reason"] = "当前交易日或 MA20 样本覆盖未达到可信度门槛"
            snapshot["missing_fields"].append("覆盖不足，未生成可执行仓位")
        self._persist(latest_date.date(), score, status, status_label, snapshot)
        return snapshot

    def _histories(
        self,
    ) -> tuple[int, list[tuple[int, list[Bar]]], datetime, dict[str, int]]:
        instruments = list(
            self.session.execute(
                select(Instrument.id, Instrument.exchange).where(
                    Instrument.status == "active",
                    func.length(Instrument.code) == 6,
                )
            ).all()
        )
        histories: list[tuple[int, list[Bar]]] = []
        latest = datetime.min
        adjustment_counts: dict[str, int] = {"QFQ": 0, "NONE": 0}
        for instrument_id, exchange in instruments:
            adjustment = preferred_adjustment(exchange)
            descending = list(
                self.session.scalars(
                    select(Bar)
                    .where(
                        Bar.instrument_id == instrument_id,
                        Bar.timeframe == "DAY",
                        Bar.adjustment == adjustment,
                        Bar.quality_status == "ok",
                    )
                    .order_by(Bar.bar_time.desc())
                    .limit(21)
                ).all()
            )
            if descending:
                histories.append((instrument_id, descending))
                adjustment_counts[adjustment] = adjustment_counts.get(adjustment, 0) + 1
                latest = max(latest, descending[0].bar_time)
        return len(histories), histories, latest, adjustment_counts

    @staticmethod
    def _daily_rows(histories: list[tuple[int, list[Bar]]], latest: datetime) -> list[dict[str, Any]]:
        rows = []
        for _instrument_id, bars in histories:
            if bars[0].bar_time.date() != latest.date():
                continue
            current = bars[0].close
            previous = bars[1].close if len(bars) > 1 else None
            closes = [bar.close for bar in bars]
            ma20 = _mean(closes[:20]) if len(closes) >= 20 else None
            base_5d = bars[min(5, len(bars) - 1)].close if len(bars) > 1 else None
            rows.append(
                {
                    "change": (current / previous - 1) * 100 if previous else None,
                    "return_5d": (current / base_5d - 1) * 100 if base_5d else None,
                    "above_ma20": ma20 is not None and current >= ma20,
                    "has_ma20": ma20 is not None,
                    "amount": bars[0].amount or 0,
                    "previous_amount": (bars[1].amount or 0) if len(bars) > 1 else 0,
                    "turnover_rate": bars[0].turnover_rate,
                }
            )
        return rows

    def _limit_ecology(self, trade_time: datetime, fallback_up: int) -> dict[str, Any]:
        trade_date = trade_time.date()
        previous_date = self.session.scalar(
            select(LimitUpEvent.trade_date)
            .where(LimitUpEvent.trade_date < trade_date)
            .distinct()
            .order_by(LimitUpEvent.trade_date.desc())
            .limit(1)
        )
        current = list(
            self.session.scalars(select(LimitUpEvent).where(LimitUpEvent.trade_date == trade_date)).all()
        )
        previous = (
            list(
                self.session.scalars(
                    select(LimitUpEvent).where(LimitUpEvent.trade_date == previous_date)
                ).all()
            )
            if previous_date
            else []
        )

        def industry(event: LimitUpEvent) -> str:
            raw = event.raw_payload or {}
            eastmoney = raw.get("eastmoney_limit_up_pool") or {}
            return (
                str(
                    event.theme
                    or raw.get("industry")
                    or raw.get("所属行业")
                    or eastmoney.get("所属行业")
                    or "未分类"
                ).strip()
                or "未分类"
            )

        current_groups: dict[str, dict[str, Any]] = {}
        previous_groups: dict[str, int] = {}
        for event in current:
            name = industry(event)
            item = current_groups.setdefault(
                name, {"name": name, "limit_up_count": 0, "highest_board": 0, "amount": 0.0}
            )
            full_raw = event.raw_payload or {}
            raw = full_raw.get("eastmoney_limit_up_pool") or {}
            item["limit_up_count"] += 1
            item["highest_board"] = max(item["highest_board"], event.consecutive_boards or 1)
            item["amount"] += (
                _safe(full_raw.get("amount") or full_raw.get("成交额") or raw.get("成交额")) or 0
            )
        for event in previous:
            name = industry(event)
            previous_groups[name] = previous_groups.get(name, 0) + 1
        industries = []
        for name in set(current_groups) | set(previous_groups):
            item = current_groups.get(
                name, {"name": name, "limit_up_count": 0, "highest_board": 0, "amount": 0.0}
            )
            previous_count = previous_groups.get(name, 0)
            delta = item["limit_up_count"] - previous_count
            status = (
                "focus"
                if item["limit_up_count"] >= 3 and delta >= 0
                else "active"
                if item["limit_up_count"] >= 2
                else "retreat"
                if previous_count >= 2 and delta < 0
                else "normal"
            )
            industries.append(
                {
                    **item,
                    "previous_count": previous_count,
                    "change": delta,
                    "status": status,
                    "amount_yi": _round(item["amount"] / 100000000, 2),
                }
            )
        industries.sort(
            key=lambda item: (item["limit_up_count"], item["highest_board"], item["previous_count"]),
            reverse=True,
        )
        return {
            "trade_date": trade_date.isoformat(),
            "previous_trade_date": previous_date.isoformat() if previous_date else "",
            "limit_up_count": len(current) or fallback_up,
            "previous_limit_up_count": len(previous),
            "highest_board": max(
                (item.consecutive_boards or 1 for item in current), default=1 if fallback_up else 0
            ),
            "multi_board_count": sum((item.consecutive_boards or 1) >= 2 for item in current),
            "one_word_count": 0,
            "industry_count": sum(name != "未分类" for name in current_groups),
            "focus_industry_count": sum(item["status"] == "focus" for item in industries),
            "active_industry_count": sum(item["status"] in {"focus", "active"} for item in industries),
            "industries": industries[:20],
            "recent": [],
            "source": "v2 涨停事件",
        }

    @staticmethod
    def _evidence(
        advance_ratio: float,
        ma20_ratio: float | None,
        amount_ratio: float | None,
        ecology: dict[str, Any],
        limit_down_count: int,
    ) -> tuple[list[str], list[str]]:
        evidence: list[str] = []
        counter: list[str] = []
        if advance_ratio >= 0.55:
            evidence.append(f"上涨家数占比 {advance_ratio:.1%}，市场宽度偏强")
        elif advance_ratio < 0.45:
            counter.append(f"上涨家数占比仅 {advance_ratio:.1%}，个股扩散不足")
        if ma20_ratio is not None and ma20_ratio >= 0.6:
            evidence.append(f"{ma20_ratio:.1%} 的样本位于 MA20 上方，中期结构占优")
        elif ma20_ratio is not None and ma20_ratio < 0.45:
            counter.append(f"仅 {ma20_ratio:.1%} 的样本位于 MA20 上方")
        if amount_ratio is not None and amount_ratio >= 1.08:
            evidence.append(f"全市场成交额较前一交易日放大 {(amount_ratio - 1):.1%}")
        elif amount_ratio is not None and amount_ratio <= 0.92:
            counter.append(f"全市场成交额较前一交易日收缩 {(1 - amount_ratio):.1%}")
        if ecology["limit_up_count"] > limit_down_count:
            evidence.append(
                f"涨停 {ecology['limit_up_count']} 家、高度 {ecology['highest_board']} 板，强于跌停生态"
            )
        else:
            counter.append(f"跌停估算 {limit_down_count} 家，不少于涨停 {ecology['limit_up_count']} 家")
        if ecology["focus_industry_count"]:
            evidence.append(f"{ecology['focus_industry_count']} 个行业形成涨停聚集")
        elif ecology["industry_count"]:
            counter.append("涨停分布较散，暂未形成三家以上聚集行业")
        return evidence, counter

    def _persist(
        self,
        trade_date,
        score: float,
        status: str,
        status_label: str,
        snapshot: dict[str, Any],
    ) -> None:
        row = self.session.scalar(
            select(RadarSnapshot).where(
                RadarSnapshot.trade_date == trade_date,
                RadarSnapshot.algorithm_version == ALGORITHM_VERSION,
            )
        )
        values = {
            "source_summary": snapshot.get("source"),
            "data_time": datetime.combine(trade_date, datetime.min.time()),
            "freshness": str(snapshot.get("freshness") or "partial"),
            "coverage_rate": (snapshot.get("coverage") or {}).get("rate"),
            "missing_fields": snapshot.get("missing_fields") or [],
            "input_digest": hashlib.sha256(
                json.dumps(
                    {
                        "trade_date": str(trade_date),
                        "coverage": snapshot.get("coverage"),
                        "components": snapshot.get("components"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest(),
            "score": score,
            "status": status,
            "status_label": status_label,
            "snapshot": snapshot,
            "calculated_at": datetime.now(),
        }
        if row is None:
            self.session.add(
                RadarSnapshot(
                    trade_date=trade_date,
                    algorithm_version=ALGORITHM_VERSION,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self.session.commit()
