from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import pandas as pd

from backend.app.chan_core.engine import ChanBar, PureChanEngine

ANALYSIS_VERSION = "limit-up-v2.0"


@dataclass(frozen=True, slots=True)
class LimitUpSubject:
    code: str
    ts_code: str | None
    name: str
    industry: str
    trade_date: str
    reason: str | None
    consecutive_boards: int
    turnover_rate: float | None
    limit_order: float | None
    circ_mv: float | None
    pe: float | None
    pb: float | None
    industry_mv_percentile: float | None
    industry_pe_percentile: float | None
    industry_pb_percentile: float | None
    raw_payload: dict[str, Any]


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def add_rule(
    rules: list[dict[str, Any]],
    label: str,
    hit: bool,
    points: float,
    value: Any = None,
) -> float:
    awarded = points if bool(hit) else 0.0
    rules.append(
        {
            "rule": label,
            "hit": bool(hit),
            "points": round(awarded, 2),
            "max": points,
            "value": _number(value),
        }
    )
    return awarded


def classify_score(score: float | None) -> str:
    if score is None:
        return "样本不足"
    return (
        "强主升"
        if score >= 80
        else "主升候选"
        if score >= 65
        else "转强观察"
        if score >= 50
        else "非主升"
    )


def indicator_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for period in (5, 10, 20, 40, 60, 120):
        data[f"ma{period}"] = data["close"].rolling(period).mean()
    ema12 = data["close"].ewm(span=12, adjust=False).mean()
    ema26 = data["close"].ewm(span=26, adjust=False).mean()
    data["dif"] = ema12 - ema26
    data["dea"] = data["dif"].ewm(span=9, adjust=False).mean()
    data["macd_hist"] = (data["dif"] - data["dea"]) * 2
    delta = data["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    data["rsi14"] = 100 - (100 / (1 + gain / loss.replace(0, float("nan"))))
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr14"] = true_range.rolling(14).mean()
    return data


def aggregate_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    source = daily.copy()
    source["actual_date"] = source.index
    weekly = source.resample("W-FRI").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "amount": "sum",
            "turnover_rate": "sum",
            "actual_date": ["last", "count"],
        }
    )
    weekly.columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "turnover_rate",
        "actual_date",
        "trading_days",
    ]
    weekly = weekly.dropna(subset=["close"])
    weekly["avg_daily_volume"] = weekly["volume"] / weekly["trading_days"].clip(lower=1)
    weekly.index = pd.to_datetime(weekly["actual_date"])
    return weekly


def _chan_structure(
    frame: pd.DataFrame,
    max_score: float,
) -> tuple[float, list[dict[str, Any]], float | None]:
    try:
        bars = [
            ChanBar(
                bar_time=pd.Timestamp(index).to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume or 0),
                amount=_number(row.get("amount")),
                turnover_rate=_number(row.get("turnover_rate")),
            )
            for index, row in frame.iterrows()
        ]
        analysis = PureChanEngine(
            config={
                "print_warning": False,
                "bs_type": "1,1p,2,2s,3a,3b",
                "macd": {"fast": 12, "slow": 26, "signal": 9},
            },
            inherit_research_defaults=False,
        ).analyze_bars("limit-up-memory", bars)
        segment = analysis["seg_list"][-1] if analysis["seg_list"] else None
        center = analysis["zs_list"][-1] if analysis["zs_list"] else None
        points = sorted(
            analysis["buy_signals"] + analysis["sell_signals"],
            key=lambda item: item["klu_idx"],
        )
        rules = []
        checks = (
            ("最新线段向上", bool(segment and segment["dir"] == "向上"), max_score * 0.4),
            (
                "价格突破最近中枢上沿",
                bool(center and float(frame.iloc[-1]["close"]) > float(center["high"])),
                max_score * 0.35,
            ),
            ("最近确认信号为买点", bool(points and points[-1]["is_buy"]), max_score * 0.25),
        )
        for label, hit, weight in checks:
            rules.append(
                {
                    "rule": label,
                    "hit": hit,
                    "points": round(weight if hit else 0, 2),
                    "max": round(weight, 2),
                    "source": "chan_core_v2",
                }
            )
        return round(sum(item["points"] for item in rules), 2), rules, center["low"] if center else None
    except Exception as exc:
        close = frame["close"]
        high_break = len(frame) >= 20 and close.iloc[-1] > close.iloc[-20:-1].max()
        rising_low = len(frame) >= 20 and frame.low.iloc[-10:].min() > frame.low.iloc[-20:-10].min()
        support = _number(frame.low.iloc[-20:].min()) if len(frame) >= 20 else None
        rules = [
            {
                "rule": "结构创新高",
                "hit": bool(high_break),
                "points": round(max_score * 0.55 if high_break else 0, 2),
                "max": round(max_score * 0.55, 2),
                "source": "price_structure_fallback",
            },
            {
                "rule": "结构低点抬高",
                "hit": bool(rising_low),
                "points": round(max_score * 0.45 if rising_low else 0, 2),
                "max": round(max_score * 0.45, 2),
                "source": "price_structure_fallback",
                "note": str(exc),
            },
        ]
        return round(sum(item["points"] for item in rules), 2), rules, support


def score_daily_main_wave(daily: pd.DataFrame) -> dict[str, Any]:
    if len(daily) < 120:
        return {"score": None, "classification": "样本不足", "sample_count": len(daily), "rules": []}
    data, rules, score = indicator_frame(daily), [], 0.0
    row = data.iloc[-1]
    checks = [
        ("收盘价高于MA20", row.close > row.ma20, 6, row.close - row.ma20),
        ("MA20高于MA60", row.ma20 > row.ma60, 6, row.ma20 - row.ma60),
        ("MA60高于MA120", row.ma60 > row.ma120, 6, row.ma60 - row.ma120),
        ("MA20十日斜率向上", row.ma20 > data.iloc[-11].ma20, 6, row.ma20 - data.iloc[-11].ma20),
        ("MA60二十日斜率向上", row.ma60 > data.iloc[-21].ma60, 6, row.ma60 - data.iloc[-21].ma60),
        ("短均线多头排列", row.close > row.ma5 > row.ma10 > row.ma20, 8, None),
        ("收盘价高于MA120", row.close > row.ma120, 4, None),
        ("距MA20不超过12%", 0 <= row.close / row.ma20 - 1 <= 0.12, 4, row.close / row.ma20 - 1),
        ("MA5五日斜率向上", row.ma5 > data.iloc[-6].ma5, 4, row.ma5 - data.iloc[-6].ma5),
        ("接近或突破前60日高点", row.close >= daily.high.iloc[-61:-1].max() * 0.98, 8, row.close / daily.high.iloc[-61:-1].max()),
        ("20日涨幅处于5%-45%", 0.05 <= row.close / data.close.iloc[-21] - 1 <= 0.45, 4, row.close / data.close.iloc[-21] - 1),
        ("MACD金叉结构", row.dif > row.dea, 4, row.dif - row.dea),
        ("MACD红柱且增强", row.macd_hist > 0 and row.macd_hist > data.iloc[-2].macd_hist, 4, row.macd_hist),
    ]
    for check in checks:
        score += add_rule(rules, *check)
    volume_average = daily.volume.iloc[-21:-1].mean()
    volume_ratio = row.volume / volume_average if volume_average and volume_average > 0 else 0
    close_position = (row.close - row.low) / (row.high - row.low) if row.high > row.low else 1
    recent = daily.iloc[-10:]
    up_volume = recent.loc[recent.close >= recent.open, "volume"].mean()
    down_volume = recent.loc[recent.close < recent.open, "volume"].mean()
    ratio = up_volume / down_volume if pd.notna(down_volume) and down_volume > 0 else None
    turnover = _number(row.get("turnover_rate"))
    for check in (
        ("量比处于1.2-3.5", 1.2 <= volume_ratio <= 3.5, 6, volume_ratio),
        ("收盘位于日内高位", close_position >= 0.8, 3, close_position),
        ("上涨日量能占优", ratio is not None and ratio >= 1.2, 3, ratio),
        ("换手率处于2%-25%", turnover is not None and 2 <= turnover <= 25, 3, turnover),
    ):
        score += add_rule(rules, *check)
    chan_score, chan_rules, support = _chan_structure(daily, 15)
    score += chan_score
    rules.extend(chan_rules)
    final = round(max(0, min(100, score)), 1)
    return {
        "score": final,
        "classification": classify_score(final),
        "sample_count": len(daily),
        "rules": rules,
        "chan_support": support,
    }


def score_weekly_main_wave(weekly: pd.DataFrame) -> dict[str, Any]:
    if len(weekly) < 40:
        return {"score": None, "classification": "样本不足", "sample_count": len(weekly), "rules": []}
    data, rules, score = indicator_frame(weekly), [], 0.0
    row = data.iloc[-1]
    checks = [
        ("周收盘高于MA10", row.close > row.ma10, 7, row.close - row.ma10),
        ("MA10高于MA20", row.ma10 > row.ma20, 7, row.ma10 - row.ma20),
        ("MA20高于MA40", row.ma20 > row.ma40, 7, row.ma20 - row.ma40),
        ("MA10四周斜率向上", row.ma10 > data.iloc[-5].ma10, 7, row.ma10 - data.iloc[-5].ma10),
        ("MA20八周斜率向上", row.ma20 > data.iloc[-9].ma20, 7, row.ma20 - data.iloc[-9].ma20),
        ("接近或突破前26周高点", row.close >= weekly.high.iloc[-27:-1].max() * 0.98, 10, row.close / weekly.high.iloc[-27:-1].max()),
        ("13周涨幅处于10%-80%", 0.10 <= row.close / data.close.iloc[-14] - 1 <= 0.80, 5, row.close / data.close.iloc[-14] - 1),
        ("周MACD多头", row.dif > row.dea and row.macd_hist > 0, 5, row.macd_hist),
        ("周RSI处于55-80", 55 <= row.rsi14 <= 80, 5, row.rsi14),
    ]
    for check in checks:
        score += add_rule(rules, *check)
    base = weekly.avg_daily_volume.iloc[-11:-1].mean()
    volume_ratio = row.avg_daily_volume / base if base and base > 0 else 0
    close_position = (row.close - row.low) / (row.high - row.low) if row.high > row.low else 1
    recent = weekly.iloc[-10:]
    up_volume = recent.loc[recent.close >= recent.open, "avg_daily_volume"].mean()
    down_volume = recent.loc[recent.close < recent.open, "avg_daily_volume"].mean()
    ratio = up_volume / down_volume if pd.notna(down_volume) and down_volume > 0 else None
    for check in (
        ("周均日量比处于1.2-3.5", 1.2 <= volume_ratio <= 3.5, 7, volume_ratio),
        ("周收盘位于区间高位", close_position >= 0.75, 4, close_position),
        ("上涨周量能占优", ratio is not None and ratio >= 1.15, 4, ratio),
    ):
        score += add_rule(rules, *check)
    chan_score, chan_rules, support = _chan_structure(weekly, 25)
    score += chan_score
    rules.extend(chan_rules)
    final = round(max(0, min(100, score)), 1)
    return {
        "score": final,
        "classification": classify_score(final),
        "sample_count": len(weekly),
        "rules": rules,
        "chan_support": support,
        "partial_week": pd.Timestamp(weekly.iloc[-1].actual_date).weekday() != 4,
    }


def weighted_score(
    components: Iterable[tuple[str, float, float | None, Any]],
) -> dict[str, Any]:
    available = earned = 0.0
    details = []
    for name, weight, fraction, evidence in components:
        valid = fraction is not None and math.isfinite(float(fraction))
        points = weight * max(0, min(1, float(fraction))) if valid else None
        if valid:
            available += weight
            earned += points or 0
        details.append(
            {
                "component": name,
                "weight": weight,
                "fraction": fraction,
                "points": round(points, 2) if points is not None else None,
                "evidence": evidence,
                "available": valid,
            }
        )
    return {
        "score": round(earned / available * 100, 1) if available >= 60 else None,
        "coverage": round(available / 100, 2),
        "components": details,
    }


def clamp_fraction(value: float | None, low: float, high: float) -> float | None:
    return None if value is None else max(0.0, min(1.0, (value - low) / (high - low)))


def score_value(subject: LimitUpSubject, fundamentals: dict[str, Any]) -> dict[str, Any]:
    roe, gross, margin = (
        _number(fundamentals.get(key))
        for key in ("roe", "grossprofit_margin", "netprofit_margin")
    )
    profitability_parts = [
        part
        for part in (
            clamp_fraction(roe, 0, 20),
            clamp_fraction(gross, 10, 50),
            clamp_fraction(margin, 0, 20),
        )
        if part is not None
    ]
    profitability = (
        sum(profitability_parts) / len(profitability_parts) if profitability_parts else None
    )
    revenue, profit = (
        _number(fundamentals.get(key)) for key in ("or_yoy", "netprofit_yoy")
    )
    growth_parts = [
        part
        for part in (clamp_fraction(revenue, -10, 30), clamp_fraction(profit, -10, 40))
        if part is not None
    ]
    growth = sum(growth_parts) / len(growth_parts) if growth_parts else None
    valuation_parts = []
    if subject.industry_pe_percentile is not None and (subject.pe or 0) > 0:
        valuation_parts.append(1 - subject.industry_pe_percentile)
    if subject.industry_pb_percentile is not None and (subject.pb or 0) > 0:
        valuation_parts.append(1 - subject.industry_pb_percentile)
    valuation = sum(valuation_parts) / len(valuation_parts) if valuation_parts else None
    debt = _number(fundamentals.get("debt_to_assets"))
    balance = 1 - clamp_fraction(debt, 20, 80) if debt is not None else None
    return weighted_score(
        [
            ("盈利质量与ROE", 25, profitability, {"roe": roe, "gross_margin": gross, "net_margin": margin}),
            ("成长", 25, growth, {"revenue_growth": revenue, "profit_growth": profit}),
            ("行业竞争力代理", 20, subject.industry_mv_percentile, {"industry_mv_percentile": subject.industry_mv_percentile}),
            ("行业相对估值", 20, valuation, {"pe": subject.pe, "pb": subject.pb}),
            ("资产负债质量", 10, balance, {"debt_to_assets": debt}),
        ]
    )


def score_market(
    subject: LimitUpSubject,
    money: dict[str, Any],
    information: dict[str, Any],
    sector_count: int,
    total_count: int,
) -> dict[str, Any]:
    board_fraction = min(1.0, max(0.25, subject.consecutive_boards / 4))
    seal_ratio = (
        clamp_fraction(subject.limit_order / (subject.circ_mv * 10000), 0, 0.03)
        if subject.limit_order and subject.circ_mv
        else None
    )
    strength_parts = [board_fraction] + ([seal_ratio] if seal_ratio is not None else [])
    strength = sum(strength_parts) / len(strength_parts)
    net = _number(money.get("main_net_amount_5d"))
    funds = clamp_fraction(net, -10000, 30000) if net is not None else None
    resonance = (
        min(1.0, sector_count / max(3, total_count * 0.12))
        if subject.industry and subject.industry != "未分类"
        else None
    )
    news = information.get("items", [])
    info = min(1.0, (len(news) + (1 if subject.reason else 0)) / 4)
    return weighted_score(
        [
            ("涨停强度", 30, strength, {"boards": subject.consecutive_boards, "seal_ratio": seal_ratio}),
            ("个股资金", 25, funds, money),
            ("题材及板块共振", 25, resonance, {"same_sector_limit_up": sector_count, "total_limit_up": total_count}),
            ("公告新闻催化", 20, info, {"reason": subject.reason, "news": news}),
        ]
    )


def risk_metrics(daily: pd.DataFrame, chan_support: float | None) -> dict[str, Any]:
    data = indicator_frame(daily)
    row = data.iloc[-1]
    close = float(row.close)
    atr_pct = float(row.atr14 / close) if pd.notna(row.atr14) and close else None
    peak = daily.close.iloc[-60:].cummax()
    drawdown = float(((daily.close.iloc[-60:] / peak) - 1).min())
    gaps = (daily.open / daily.close.shift(1) - 1).abs().iloc[-20:]
    max_gap = float(gaps.max()) if not gaps.empty else None
    amount, turnover = _number(row.get("amount")), _number(row.get("turnover_rate"))
    supports = [
        value
        for value in (chan_support, _number(daily.low.iloc[-20:].min()), _number(row.ma20))
        if value is not None and value < close
    ]
    support = max(supports) if supports else close * 0.93
    atr = _number(row.atr14) or close * 0.03
    stop = min(close * 0.99, max(0.01, support - atr * 0.25))
    stop_distance = max(0.01, (close - stop) / close)
    liquidity_parts = [
        part
        for part in (
            clamp_fraction(amount, 20000, 300000) if amount is not None else None,
            1 - abs((turnover or 10) - 10) / 20 if turnover is not None else None,
        )
        if part is not None
    ]
    liquidity = sum(liquidity_parts) / len(liquidity_parts) if liquidity_parts else None
    volatility = 1 - clamp_fraction(atr_pct, 0.02, 0.10) if atr_pct is not None else None
    drawdown_gap_parts = [1 - clamp_fraction(abs(drawdown), 0.05, 0.30)]
    if max_gap is not None:
        drawdown_gap_parts.append(1 - clamp_fraction(max_gap, 0.02, 0.12))
    drawdown_gap = sum(drawdown_gap_parts) / len(drawdown_gap_parts)
    scored = weighted_score(
        [
            ("流动性", 25, liquidity, {"amount": amount, "turnover_rate": turnover}),
            ("波动率", 20, volatility, {"atr_pct": atr_pct}),
            ("回撤与跳空", 20, drawdown_gap, {"max_drawdown_60d": drawdown, "max_gap_20d": max_gap}),
            ("止损距离", 20, 1 - clamp_fraction(stop_distance, 0.03, 0.12), {"stop_distance_pct": stop_distance}),
            ("盈亏比可行性", 15, 1 if stop_distance <= 0.10 else 0.5 if stop_distance <= 0.15 else 0, {"minimum_rr": 2}),
        ]
    )
    risk_score = scored["score"] or 0
    cap = 0.15 if risk_score >= 75 else 0.10 if risk_score >= 50 else 0.05
    position = min(cap, 0.005 / stop_distance)
    per_share = close - stop
    scored["plan"] = {
        "reference_price": round(close, 2),
        "stop_price": round(stop, 2),
        "stop_distance_pct": round(stop_distance * 100, 2),
        "position_pct": round(position * 100, 1),
        "position_cap_pct": round(cap * 100, 1),
        "target_2r": round(close + per_share * 2, 2),
        "target_3r": round(close + per_share * 3, 2),
        "exit_rules": [
            "价格触及初始止损",
            "连续两日收盘跌破MA20",
            "出现确认缠论卖点",
            "达到2R后至少减仓三分之一并将保护位上移",
        ],
    }
    return scored


def build_quantitative_analysis(
    subject: LimitUpSubject,
    daily: pd.DataFrame,
    *,
    fundamentals: dict[str, Any] | None = None,
    money: dict[str, Any] | None = None,
    information: dict[str, Any] | None = None,
    sector_count: int,
    total_count: int,
) -> dict[str, Any]:
    if daily.empty:
        raise RuntimeError(f"{subject.code} 在 {subject.trade_date} 前无 v2 日线数据")
    fundamentals = fundamentals or {}
    money = money or {}
    information = information or {"items": []}
    if subject.turnover_rate is not None:
        daily.loc[daily.index[-1], "turnover_rate"] = subject.turnover_rate
    day = score_daily_main_wave(daily)
    week = score_weekly_main_wave(aggregate_weekly(daily))
    value = score_value(subject, fundamentals)
    market = score_market(subject, money, information, sector_count, total_count)
    timing = None
    if day["score"] is not None and week["score"] is not None:
        resonance = (
            100
            if day["score"] >= 65 and week["score"] >= 65
            else 50
            if max(day["score"], week["score"]) >= 65
            else 0
        )
        timing = round(day["score"] * 0.55 + week["score"] * 0.35 + resonance * 0.10, 1)
    risk = risk_metrics(daily, day.get("chan_support"))
    scores = [value["score"], market["score"], timing, risk["score"]]
    overall = (
        round(
            value["score"] * 0.25
            + market["score"] * 0.25
            + timing * 0.35
            + risk["score"] * 0.15,
            1,
        )
        if all(score is not None for score in scores)
        else None
    )
    completeness = round(
        (value["coverage"] + market["coverage"] + (1 if timing is not None else 0) + risk["coverage"]) / 4,
        2,
    )
    details = {
        "day_main_wave": day,
        "week_main_wave": week,
        "value_system": value,
        "market_system": market,
        "timing_system": {"score": timing, "weights": {"day": 0.55, "week": 0.35, "resonance": 0.10}},
        "risk_system": {key: value for key, value in risk.items() if key != "plan"},
    }
    snapshot = {
        "trade_date": subject.trade_date,
        "data_as_of": pd.Timestamp(daily.index[-1]).strftime("%Y%m%d"),
        "stock": {
            "code": subject.code,
            "ts_code": subject.ts_code,
            "name": subject.name,
            "industry": subject.industry,
            "reason": subject.reason,
            "consecutive_boards": subject.consecutive_boards,
        },
        "scores": {
            "day_main_wave": day["score"],
            "week_main_wave": week["score"],
            "value": value["score"],
            "market": market["score"],
            "timing": timing,
            "risk_control": risk["score"],
            "overall": overall,
        },
        "classifications": {"day": day["classification"], "week": week["classification"]},
        "fundamentals": fundamentals,
        "money_flow": money,
        "information": information,
        "data_completeness": completeness,
        "missing_data_policy": "缺失字段不计零分；单系统可用权重不足60%时不评分",
        "risk_plan": risk["plan"],
        "analysis_version": ANALYSIS_VERSION,
    }
    return {
        "trade_date": subject.trade_date,
        "code": subject.code,
        "day_score": day["score"],
        "week_score": week["score"],
        "value_score": value["score"],
        "market_score": market["score"],
        "timing_score": timing,
        "risk_score": risk["score"],
        "overall_score": overall,
        "day_classification": day["classification"],
        "week_classification": week["classification"],
        "completeness": completeness,
        "score_details": details,
        "snapshot": snapshot,
        "risk_plan": risk["plan"],
    }
