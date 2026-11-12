from __future__ import annotations

import hashlib
import json
import math
import statistics
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings, get_settings
from backend.app.db.models import Bar, Industry, Instrument, RadarSnapshot, TradingCalendar
from backend.app.services.bar_access import preferred_adjustment
from backend.app.services.data_health import expected_trade_date

# 2.2: everything comes from daily bars (limit ecology included, so every day is
# computed the same way and history can be rebuilt), suspension days are not
# counted, and the trend adds the share above MA60 and the 20-day median return.
ALGORITHM_VERSION = "2.2"
# Calendar days of bars loaded before the first computed day: enough for MA60.
WINDOW_CALENDAR_DAYS = 130
# A close within this of the price limit, at the day's high, counts as a limit-up.
LIMIT_TOLERANCE = 0.0015
# Newly listed stocks trade without a price limit at first.
NEW_LISTING_DAYS = 15


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


@dataclass(frozen=True)
class DayBar:
    day: date
    open: float
    high: float
    low: float
    close: float
    amount: float
    turnover_rate: float | None


@dataclass
class StockSeries:
    instrument_id: int
    code: str
    name: str
    exchange: str | None
    industry: str
    list_date: date | None
    adjustment: str
    bars: list[DayBar] = field(default_factory=list)  # traded days, ascending
    days: list[date] = field(default_factory=list)
    present: set[date] = field(default_factory=set)  # traded or suspended

    def price_limit(self) -> float:
        if "ST" in self.name.upper():
            return 0.05
        if str(self.exchange or "").upper() == "BJ":
            return 0.30
        if self.code.startswith(("300", "301", "302", "688", "689")):
            return 0.20
        return 0.10

    def index_on(self, day: date) -> int | None:
        index = bisect_right(self.days, day) - 1
        return index if index >= 0 and self.days[index] == day else None

    def limited(self, index: int) -> tuple[bool, bool]:
        """(limit-up, limit-down) on the traded bar at index."""
        if index < 1:
            return False, False
        bar = self.bars[index]
        if self.list_date and (bar.day - self.list_date).days < NEW_LISTING_DAYS:
            return False, False
        previous = self.bars[index - 1].close
        if not previous:
            return False, False
        change = bar.close / previous - 1
        limit = self.price_limit() - LIMIT_TOLERANCE
        up = change >= limit and bar.close >= bar.high * 0.9995
        down = change <= -limit and bar.close <= bar.low * 1.0005
        return up, down

    def boards(self, index: int) -> int:
        count = 0
        while index >= 1 and self.limited(index)[0]:
            count += 1
            index -= 1
        return count


class MarketRadarService:
    """Calculate and persist the market radar exclusively from v2 daily bars."""

    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def refresh(self, as_of_trade_date: date | None = None) -> dict[str, Any]:
        end = as_of_trade_date or expected_trade_date(self.session) or self._latest_bar_day()
        stocks = self._load(end - timedelta(days=WINDOW_CALENDAR_DAYS), end)
        days = sorted({day for stock in stocks for day in stock.present if day <= end})
        if not days:
            raise ValueError("v2 日线数据为空，请先执行行情刷新")
        trade_date = days[-1]
        previous = days[-2] if len(days) > 1 else None
        snapshot = self._compute(trade_date, previous, stocks)
        self._persist(trade_date, snapshot)
        return snapshot

    def backfill(
        self, start: date, end: date, progress: Callable[[date, dict[str, Any]], None] | None = None
    ) -> list[date]:
        """Compute every open session in [start, end] from the bars, month by month."""
        sessions = list(
            self.session.scalars(
                select(TradingCalendar.trade_date)
                .where(
                    TradingCalendar.is_open.is_(True),
                    TradingCalendar.trade_date >= start,
                    TradingCalendar.trade_date <= end,
                )
                .order_by(TradingCalendar.trade_date)
            )
        )
        computed: list[date] = []
        chunk: list[date] = []
        for day in [*sessions, None]:
            if day is not None and (not chunk or (day.year, day.month) == (chunk[0].year, chunk[0].month)):
                chunk.append(day)
                continue
            if chunk:
                stocks = self._load(chunk[0] - timedelta(days=WINDOW_CALENDAR_DAYS), chunk[-1])
                data_days = sorted({item for stock in stocks for item in stock.present})
                for trade_date in chunk:
                    if trade_date not in data_days:
                        continue
                    position = data_days.index(trade_date)
                    previous = data_days[position - 1] if position else None
                    snapshot = self._compute(trade_date, previous, stocks)
                    self._persist(trade_date, snapshot)
                    computed.append(trade_date)
                    if progress:
                        progress(trade_date, snapshot)
            chunk = [day] if day is not None else []
        return computed

    def _latest_bar_day(self) -> date:
        """Only used without a trading calendar (tests, a fresh install)."""
        latest = self.session.scalar(
            select(Bar.bar_time).where(Bar.timeframe == "DAY").order_by(Bar.bar_time.desc()).limit(1)
        )
        return latest.date() if latest else date.today()

    def _load(self, start: date, end: date) -> list[StockSeries]:
        instruments = self.session.execute(
            select(
                Instrument.id, Instrument.code, Instrument.name, Instrument.exchange,
                Instrument.list_date, Industry.name,
            )
            .outerjoin(Industry, Industry.id == Instrument.industry_id)
            .where(
                Instrument.status == "active",
                Instrument.asset_type == "stock",
                Instrument.code.is_not(None),
            )
        ).all()
        stocks: dict[int, StockSeries] = {}
        for instrument_id, code, name, exchange, list_date, industry in instruments:
            if not code or len(code) != 6:
                continue
            stocks[instrument_id] = StockSeries(
                instrument_id=instrument_id, code=code, name=name or code, exchange=exchange,
                industry=(industry or "").strip() or "未分类", list_date=list_date,
                adjustment=preferred_adjustment(exchange),
            )
        by_adjustment: dict[str, list[int]] = {}
        for stock in stocks.values():
            by_adjustment.setdefault(stock.adjustment, []).append(stock.instrument_id)
        rows: dict[int, list[Any]] = {}
        for adjustment, ids in by_adjustment.items():
            for offset in range(0, len(ids), 900):
                batch = ids[offset : offset + 900]
                for row in self.session.execute(
                    select(
                        Bar.instrument_id, Bar.bar_time, Bar.open, Bar.high, Bar.low, Bar.close,
                        Bar.volume, Bar.amount, Bar.turnover_rate, Bar.data_source_id,
                    )
                    .where(
                        Bar.instrument_id.in_(batch),
                        Bar.timeframe == "DAY",
                        Bar.adjustment == adjustment,
                        Bar.quality_status == "ok",
                        Bar.bar_time >= datetime.combine(start, time.min),
                        Bar.bar_time <= datetime.combine(end, time.max),
                    )
                    .order_by(Bar.instrument_id, Bar.bar_time)
                ):
                    rows.setdefault(row.instrument_id, []).append(row)
        series: list[StockSeries] = []
        for instrument_id, bars in rows.items():
            stock = stocks[instrument_id]
            # Research reads one provider per series: the provider of the newest bar.
            source_id = bars[-1].data_source_id
            for row in bars:
                if row.data_source_id != source_id:
                    continue
                day = row.bar_time.date()
                stock.present.add(day)
                if row.volume is not None and row.volume <= 0:
                    continue  # suspension day
                stock.bars.append(DayBar(
                    day, float(row.open), float(row.high), float(row.low), float(row.close),
                    float(row.amount or 0), row.turnover_rate,
                ))
                stock.days.append(day)
            series.append(stock)
        return series

    def _compute(self, trade_date: date, previous_date: date | None, stocks: list[StockSeries]) -> dict[str, Any]:
        cached_count = 0
        present_count = 0
        rows: list[dict[str, Any]] = []
        limit_ups: list[tuple[StockSeries, int]] = []
        previous_limit_ups: list[StockSeries] = []
        limit_down_count = 0
        adjustment_counts: dict[str, int] = {"QFQ": 0, "NONE": 0}
        for stock in stocks:
            if not any(day <= trade_date for day in stock.present):
                continue
            cached_count += 1
            adjustment_counts[stock.adjustment] = adjustment_counts.get(stock.adjustment, 0) + 1
            if trade_date in stock.present:
                present_count += 1
            if previous_date is not None:
                previous_index = stock.index_on(previous_date)
                if previous_index is not None and stock.limited(previous_index)[0]:
                    previous_limit_ups.append(stock)
            index = stock.index_on(trade_date)
            if index is None or index < 1:
                continue
            bars = stock.bars
            current = bars[index].close
            closes = [bar.close for bar in bars[max(0, index - 59) : index + 1]]
            ma20 = _mean(closes[-20:]) if len(closes) >= 20 else None
            ma60 = _mean(closes) if len(closes) >= 60 else None
            base_5d = bars[index - 5].close if index >= 5 else bars[0].close
            base_20d = bars[index - 20].close if index >= 20 else None
            up, down = stock.limited(index)
            if up:
                limit_ups.append((stock, index))
            if down:
                limit_down_count += 1
            rows.append({
                "change": (current / bars[index - 1].close - 1) * 100 if bars[index - 1].close else None,
                "return_5d": (current / base_5d - 1) * 100 if base_5d else None,
                "return_20d": (current / base_20d - 1) * 100 if base_20d else None,
                "above_ma20": ma20 is not None and current >= ma20,
                "has_ma20": ma20 is not None,
                "above_ma60": ma60 is not None and current >= ma60,
                "has_ma60": ma60 is not None,
                "amount": bars[index].amount,
                "previous_amount": bars[index - 1].amount,
                "turnover_rate": bars[index].turnover_rate,
            })
        if not rows:
            raise ValueError(f"{trade_date} 没有可用的日线，请先执行行情刷新")

        changes = [row["change"] for row in rows if row["change"] is not None]
        returns_5d = [row["return_5d"] for row in rows if row["return_5d"] is not None]
        returns_20d = [row["return_20d"] for row in rows if row["return_20d"] is not None]
        advancers = sum(value > 0.05 for value in changes)
        decliners = sum(value < -0.05 for value in changes)
        flat = max(0, len(changes) - advancers - decliners)
        ma20_rows = [row for row in rows if row["has_ma20"]]
        ma60_rows = [row for row in rows if row["has_ma60"]]
        above_ma20 = sum(row["above_ma20"] for row in ma20_rows)
        above_ma60 = sum(row["above_ma60"] for row in ma60_rows)
        advance_ratio = _ratio(advancers, len(changes)) or 0.0
        decline_ratio = _ratio(decliners, len(changes)) or 0.0
        ma20_ratio = _ratio(above_ma20, len(ma20_rows))
        ma60_ratio = _ratio(above_ma60, len(ma60_rows))
        current_coverage = _ratio(present_count, cached_count) or 0.0
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
        median_20d = _median(returns_20d)
        amount = sum(row["amount"] for row in rows)
        previous_amount = sum(row["previous_amount"] for row in rows)
        amount_ratio = _ratio(amount, previous_amount)
        average_turnover = _mean(row["turnover_rate"] for row in rows if row["turnover_rate"] is not None)
        ecology = self._limit_ecology(trade_date, previous_date, limit_ups, previous_limit_ups)

        trend_score = _clamp(
            50
            + (median_change or 0) * 8
            + ((ma20_ratio if ma20_ratio is not None else 0.5) - 0.5) * 30
            + ((ma60_ratio if ma60_ratio is not None else 0.5) - 0.5) * 50
            + (median_5d or 0) * 1.5
            + (median_20d or 0) * 1.0
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
            trend_score * 0.30
            + breadth_score * 0.20
            + liquidity_score * 0.20
            + limit_score * 0.20
            + industry_score * 0.10,
            1,
        )
        status, status_label, position_range = _status(score)
        evidence, counter = self._evidence(
            advance_ratio, ma20_ratio, ma60_ratio, amount_ratio, ecology, limit_down_count
        )
        components = [
            {
                "key": "equal_weight_trend",
                "label": "全市场等权趋势",
                "score": round(trend_score, 1),
                "summary": (
                    f"中位涨跌幅 {_round(median_change)}%，20日中位涨跌幅 {_round(median_20d)}%，"
                    f"{_round((ma60_ratio or 0) * 100, 1)}% 位于 60 日均线上方"
                ),
                "metrics": {
                    "median_change": _round(median_change),
                    "average_change": _round(average_change),
                    "median_return_5d": _round(median_5d),
                    "median_return_20d": _round(median_20d),
                    "above_ma20_rate": _round(ma20_ratio, 4),
                    "above_ma60_rate": _round(ma60_ratio, 4),
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
                "summary": f"成交额 {_round(amount / 100000000)} 亿元，环比 {_round(((amount_ratio or 1) - 1) * 100)}%",
                "metrics": {
                    "amount_yi": _round(amount / 100000000),
                    "previous_amount_yi": _round(previous_amount / 100000000),
                    "amount_ratio": _round(amount_ratio, 4),
                    "average_turnover_rate": _round(average_turnover),
                },
            },
            {
                "key": "limit_ecology",
                "label": "涨跌停生态",
                "score": round(limit_score, 1),
                "summary": f"涨停 {ecology['limit_up_count']} / 跌停 {limit_down_count} / 最高 {ecology['highest_board']} 板",
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
            "trade_date": trade_date.isoformat(),
            "algorithm_version": ALGORITHM_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(),
            "source": "v2 日线（涨跌停按收盘封板推算）",
            "regime": {
                "status": status,
                "status_label": status_label,
                "score": score,
                "position_range": position_range,
            },
            "freshness": freshness,
            "coverage": {
                "current_count": present_count,
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
                "above_ma60_count": above_ma60,
                "above_ma60_rate": _round(ma60_ratio, 4),
                "limit_down_count": limit_down_count,
            },
            "liquidity": {
                "amount_yi": _round(amount / 100000000),
                "previous_amount_yi": _round(previous_amount / 100000000),
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
                    "涨跌停按收盘价达到涨跌幅限制且收在最高（最低）价推算；行业取股票主数据。",
                    "停牌日不计入涨跌统计。",
                ],
            },
            "components": components,
            "evidence": evidence,
            "counter_evidence": counter,
            "limit_ecology": ecology,
            "missing_fields": [
                "核心指数行情未缓存，趋势维度使用全市场等权指标替代",
            ],
        }
        if freshness != "fresh":
            snapshot["regime"].pop("position_range", None)
            snapshot["regime"]["position_unavailable_reason"] = "当前交易日或 MA20 样本覆盖未达到可信度门槛"
            snapshot["missing_fields"].append("覆盖不足，未生成可执行仓位")
        return snapshot

    @staticmethod
    def _limit_ecology(
        trade_date: date,
        previous_date: date | None,
        limit_ups: list[tuple[StockSeries, int]],
        previous_limit_ups: list[StockSeries],
    ) -> dict[str, Any]:
        current_groups: dict[str, dict[str, Any]] = {}
        boards: list[int] = []
        one_word = 0
        for stock, index in limit_ups:
            height = stock.boards(index)
            boards.append(height)
            bar = stock.bars[index]
            if bar.open == bar.high == bar.low == bar.close:
                one_word += 1
            item = current_groups.setdefault(
                stock.industry, {"name": stock.industry, "limit_up_count": 0, "highest_board": 0, "amount": 0.0}
            )
            item["limit_up_count"] += 1
            item["highest_board"] = max(item["highest_board"], height)
            item["amount"] += bar.amount
        previous_groups: dict[str, int] = {}
        for stock in previous_limit_ups:
            previous_groups[stock.industry] = previous_groups.get(stock.industry, 0) + 1
        industries = []
        for name in set(current_groups) | set(previous_groups):
            item = current_groups.get(name, {"name": name, "limit_up_count": 0, "highest_board": 0, "amount": 0.0})
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
            industries.append({
                **item,
                "previous_count": previous_count,
                "change": delta,
                "status": status,
                "amount_yi": _round(item["amount"] / 100000000, 2),
            })
        industries.sort(
            key=lambda item: (item["limit_up_count"], item["highest_board"], item["previous_count"]), reverse=True
        )
        return {
            "trade_date": trade_date.isoformat(),
            "previous_trade_date": previous_date.isoformat() if previous_date else "",
            "limit_up_count": len(limit_ups),
            "previous_limit_up_count": len(previous_limit_ups),
            "highest_board": max(boards, default=0),
            "multi_board_count": sum(height >= 2 for height in boards),
            "one_word_count": one_word,
            "industry_count": sum(name != "未分类" for name in current_groups),
            "focus_industry_count": sum(item["status"] == "focus" for item in industries),
            "active_industry_count": sum(item["status"] in {"focus", "active"} for item in industries),
            "industries": industries[:20],
            "recent": [],
            "source": "v2 日线推算（收盘封板）",
        }

    @staticmethod
    def _evidence(
        advance_ratio: float,
        ma20_ratio: float | None,
        ma60_ratio: float | None,
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
            evidence.append(f"{ma20_ratio:.1%} 的样本位于 MA20 上方，短期结构占优")
        elif ma20_ratio is not None and ma20_ratio < 0.45:
            counter.append(f"仅 {ma20_ratio:.1%} 的样本位于 MA20 上方")
        if ma60_ratio is not None and ma60_ratio >= 0.6:
            evidence.append(f"{ma60_ratio:.1%} 的样本位于 MA60 上方，中期趋势向上")
        elif ma60_ratio is not None and ma60_ratio < 0.45:
            counter.append(f"仅 {ma60_ratio:.1%} 的样本位于 MA60 上方，中期趋势{'向下' if ma60_ratio < 0.30 else '偏弱'}")
        if amount_ratio is not None and amount_ratio >= 1.08:
            evidence.append(f"全市场成交额较前一交易日放大 {(amount_ratio - 1):.1%}")
        elif amount_ratio is not None and amount_ratio <= 0.92:
            counter.append(f"全市场成交额较前一交易日收缩 {(1 - amount_ratio):.1%}")
        if ecology["limit_up_count"] > limit_down_count:
            evidence.append(f"涨停 {ecology['limit_up_count']} 家、高度 {ecology['highest_board']} 板，强于跌停生态")
        else:
            counter.append(f"跌停 {limit_down_count} 家，不少于涨停 {ecology['limit_up_count']} 家")
        if ecology["focus_industry_count"]:
            evidence.append(f"{ecology['focus_industry_count']} 个行业形成涨停聚集")
        elif ecology["industry_count"]:
            counter.append("涨停分布较散，暂未形成三家以上聚集行业")
        return evidence, counter

    def _persist(self, trade_date: date, snapshot: dict[str, Any]) -> None:
        regime = snapshot["regime"]
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
            "score": regime["score"],
            "status": regime["status"],
            "status_label": regime["status_label"],
            "snapshot": snapshot,
            "calculated_at": datetime.now(),
        }
        if row is None:
            self.session.add(RadarSnapshot(trade_date=trade_date, algorithm_version=ALGORITHM_VERSION, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
        self.session.commit()
