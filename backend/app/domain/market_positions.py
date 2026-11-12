"""Build slow trend positions with an asymmetric risk overlay.

The trend layer deliberately changes slowly to reduce whipsaw. The risk layer
can only cap that target; it never raises exposure.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date
from typing import Any

POSITION_BANDS = (
    {"status": "risk", "label": "风险", "min": 0.00, "max": 0.20, "mid": 0.10},
    {"status": "weak", "label": "偏弱", "min": 0.15, "max": 0.35, "mid": 0.25},
    {"status": "neutral", "label": "震荡", "min": 0.30, "max": 0.55, "mid": 0.425},
    {"status": "warm", "label": "偏强", "min": 0.45, "max": 0.70, "mid": 0.575},
    {"status": "strong", "label": "强势", "min": 0.60, "max": 0.85, "mid": 0.725},
)
POSITION_ALGORITHM_VERSION = "2.2-session-aware"
POSITION_THRESHOLDS = (35.0, 45.0, 58.0, 68.0)
MA_WINDOW = 5
HYSTERESIS_POINTS = 3.0
CONFIRM_DAYS = 2
RISK_RECOVERY_DAYS = 2
EMERGENCY_SCORE = 20.0
# Smoothing and confirmations count trading sessions. Snapshots further apart than
# this are not a series: the MA and pending confirmations start over after the gap.
MAX_SESSION_GAP = 3
# Share of stocks above their 60-day average below which the medium trend is weak / down.
MA60_WEAK = 0.45
MA60_DOWN = 0.30


def _band_for_score(score: float) -> int:
    if score >= 68:
        return 4
    if score >= 58:
        return 3
    if score >= 45:
        return 2
    if score >= 35:
        return 1
    return 0


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def risk_inputs_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, float | None]:
    """Extract only point-in-time fields used by the position risk overlay."""
    payload = snapshot or {}
    breadth = payload.get("breadth") or {}
    liquidity = payload.get("liquidity") or {}
    ecology = payload.get("limit_ecology") or {}
    trend_metrics: dict[str, Any] = {}
    for component in payload.get("components") or []:
        if isinstance(component, dict) and component.get("key") == "equal_weight_trend":
            trend_metrics = component.get("metrics") or {}
            break
    return {
        "advance_rate": _number(breadth.get("advance_rate")),
        "decline_rate": _number(breadth.get("decline_rate")),
        "above_ma20_rate": _number(breadth.get("above_ma20_rate")),
        "return_dispersion": _number(breadth.get("return_dispersion")),
        "limit_down_count": _number(breadth.get("limit_down_count")),
        "amount_ratio": _number(liquidity.get("amount_ratio")),
        "limit_up_count": _number(ecology.get("limit_up_count")),
        "median_return_5d": _number(trend_metrics.get("median_return_5d")),
        "above_ma60_rate": _number(trend_metrics.get("above_ma60_rate")),
        "median_return_20d": _number(trend_metrics.get("median_return_20d")),
    }


def dedupe_position_history(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one observation per trade date, preferring the last calculation."""
    dated: dict[str, dict[str, Any]] = {}
    undated: list[dict[str, Any]] = []
    for item in history:
        trade_date = str(item.get("trade_date") or "").strip()
        if trade_date:
            dated[trade_date] = dict(item)
        else:
            undated.append(dict(item))
    return [*sorted(dated.values(), key=lambda item: str(item.get("trade_date"))), *undated]


def _risk_overlay(item: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    score = float(item.get("score") or 0)
    inputs = item.get("risk_inputs") or {}
    previous_score = float(previous.get("score") or 0) if previous else None
    advance_rate = _number(inputs.get("advance_rate"))
    decline_rate = _number(inputs.get("decline_rate"))
    above_ma20 = _number(inputs.get("above_ma20_rate"))
    dispersion = _number(inputs.get("return_dispersion"))
    limit_down = _number(inputs.get("limit_down_count"))
    limit_up = _number(inputs.get("limit_up_count"))
    amount_ratio = _number(inputs.get("amount_ratio"))
    median_return_5d = _number(inputs.get("median_return_5d"))
    above_ma60 = _number(inputs.get("above_ma60_rate"))

    cap_band = len(POSITION_BANDS) - 1
    level = "normal"
    reasons: list[str] = []

    def cap(target: int, risk_level: str, reason: str) -> None:
        nonlocal cap_band, level
        cap_band = min(cap_band, target)
        levels = {"normal": 0, "caution": 1, "high": 2, "extreme": 3}
        if levels[risk_level] > levels[level]:
            level = risk_level
        reasons.append(reason)

    if score < EMERGENCY_SCORE:
        cap(0, "extreme", f"当日环境评分低于 {EMERGENCY_SCORE:.0f} 分")
    if advance_rate is not None and advance_rate <= 0.20:
        cap(0, "extreme", f"上涨家数占比仅 {advance_rate:.1%}")
    elif (advance_rate is not None and advance_rate <= 0.30) or (
        decline_rate is not None and decline_rate >= 0.60
    ):
        cap(1, "high", "市场宽度快速恶化")
    if dispersion is not None and dispersion >= 4.0 and decline_rate is not None and decline_rate >= 0.50:
        cap(1, "high", "个股涨跌分化与下跌扩散同时升高")
    if limit_down is not None and limit_up is not None and limit_down >= max(30.0, limit_up * 1.2):
        cap(1, "high", f"跌停估算 {limit_down:.0f} 家，不少于涨停生态")
    if previous_score is not None and previous_score - score >= 25:
        cap(1 if score < 45 else 2, "high", f"环境评分单日回落 {previous_score - score:.1f} 分")
    # A strong day inside a falling market is a bounce, not a new regime.
    if above_ma60 is not None and above_ma60 < MA60_DOWN:
        cap(1, "high", f"仅 {above_ma60:.1%} 的股票位于 60 日均线上方，中期趋势向下")
    elif above_ma60 is not None and above_ma60 < MA60_WEAK:
        cap(2, "caution", f"仅 {above_ma60:.1%} 的股票位于 60 日均线上方，中期趋势偏弱")
    if score >= 68 and above_ma20 is not None and above_ma20 < 0.50:
        cap(3, "caution", f"强势评分下仅 {above_ma20:.1%} 的样本位于 MA20 上方")
    if (
        median_return_5d is not None
        and median_return_5d >= 8.0
        and amount_ratio is not None
        and amount_ratio >= 1.20
    ):
        cap(3, "caution", "全市场短期涨幅与成交同步过热")

    return {
        "cap_band": cap_band,
        "level": level,
        "triggered": cap_band < len(POSITION_BANDS) - 1,
        "reasons": reasons,
    }


def _session_gaps(observations: Sequence[dict[str, Any]], open_sessions: Sequence[date] | None) -> list[int]:
    """Open sessions between each observation and the previous one (1 = consecutive sessions)."""
    if not open_sessions:
        return [1] * len(observations)
    sessions = sorted(open_sessions)
    indexes: list[int | None] = []
    for item in observations:
        try:
            day = date.fromisoformat(str(item.get("trade_date"))[:10])
        except ValueError:
            indexes.append(None)
            continue
        indexes.append(bisect_left(sessions, day))
    gaps = [1]
    for previous, current in zip(indexes, indexes[1:], strict=False):
        gaps.append(1 if previous is None or current is None else max(1, current - previous))
    return gaps


def build_executable_positions(
    history: Sequence[dict[str, Any]], open_sessions: Sequence[date] | None = None
) -> list[dict[str, Any]]:
    """Turn radar scores into next-session positions.

    ``open_sessions`` (the trading calendar) lets the MA, the confirmations and the
    risk recovery count real sessions. Without it, observations are taken as
    consecutive sessions.
    """
    observations = dedupe_position_history(history)
    if not observations:
        return []
    gaps = _session_gaps(observations, open_sessions)
    scores = [float(item.get("score") or 0) for item in observations]
    ma_values: list[float] = []
    segment_start = 0
    for index in range(len(scores)):
        if gaps[index] > MAX_SESSION_GAP:
            segment_start = index
        values = scores[max(segment_start, index - MA_WINDOW + 1) : index + 1]
        ma_values.append(round(sum(values) / len(values), 1))

    base_band = _band_for_score(ma_values[0])
    pending_direction = 0
    pending_days = 0
    recovery_days = 0
    executed_band: int | None = None
    results: list[dict[str, Any]] = []
    for index, (item, raw_score, decision_score) in enumerate(zip(observations, scores, ma_values, strict=False)):
        previous_base_band = base_band
        gap = gaps[index]
        base_reason = "初始仓位按当前平滑评分确定" if index == 0 else "平滑评分未形成连续调仓信号"
        if index > 0 and gap > MAX_SESSION_GAP:
            # Nothing is known about the missing sessions: start over, never higher than before.
            pending_direction = 0
            pending_days = 0
            recovery_days = 0
            base_band = min(base_band, _band_for_score(decision_score))
            base_reason = f"评分中断 {gap - 1} 个交易日，平滑与确认重新开始，基础仓位不高于中断前"
        elif index > 0:
            direction = 0
            if (
                base_band < len(POSITION_BANDS) - 1
                and decision_score >= POSITION_THRESHOLDS[base_band] + HYSTERESIS_POINTS
            ):
                direction = 1
            elif base_band > 0 and decision_score < POSITION_THRESHOLDS[base_band - 1] - HYSTERESIS_POINTS:
                direction = -1
            if gap > 1:
                # A skipped session breaks a run of confirmations.
                pending_direction = 0
                pending_days = 0
            if direction == 0:
                pending_direction = 0
                pending_days = 0
            else:
                if direction == pending_direction:
                    pending_days += 1
                else:
                    pending_direction = direction
                    pending_days = 1
                if pending_days >= CONFIRM_DAYS:
                    base_band += direction
                    pending_direction = 0
                    pending_days = 0
                    base_reason = f"MA5 连续 {CONFIRM_DAYS} 个交易日确认，{'提高' if direction > 0 else '降低'}一档基础仓位"
                else:
                    base_reason = f"MA5 已出现{'升仓' if direction > 0 else '降仓'}信号，等待第 {CONFIRM_DAYS} 个交易日确认"

        previous_item = observations[index - 1] if index > 0 and gap <= MAX_SESSION_GAP else None
        overlay = _risk_overlay(item, previous_item)
        cap_band = int(overlay["cap_band"])
        desired_band = min(base_band, cap_band)
        cap_note = f"；上限：{'；'.join(overlay['reasons'])}" if cap_band < base_band else ""
        previous_executed = executed_band
        if executed_band is None or index > 0 and gap > MAX_SESSION_GAP:
            executed_band = desired_band if executed_band is None else min(executed_band, desired_band)
            recovery_days = 0
            final_reason = base_reason + cap_note
        elif desired_band < executed_band:
            executed_band = desired_band
            recovery_days = 0
            final_reason = (
                f"风险覆盖层立即降仓：{'；'.join(overlay['reasons'])}" if desired_band < base_band else base_reason
            )
        elif desired_band > executed_band:
            if gap > 1:
                recovery_days = 0
            if base_band > previous_base_band and executed_band == previous_base_band:
                executed_band += 1
                recovery_days = 0
                final_reason = base_reason + cap_note
            else:
                recovery_days = min(recovery_days + 1, RISK_RECOVERY_DAYS)
                if recovery_days >= RISK_RECOVERY_DAYS:
                    # Once confirmed, one band per consecutive session, never above the cap.
                    executed_band += 1
                    final_reason = "风险缓和已确认，按每个交易日一档逐步恢复仓位" + cap_note
                else:
                    final_reason = f"风险缓和，等待第 {RISK_RECOVERY_DAYS} 个交易日确认后恢复一档仓位" + cap_note
        else:
            recovery_days = 0
            final_reason = (
                f"风险覆盖层限制仓位：{'；'.join(overlay['reasons'])}" if desired_band < base_band else base_reason
            )

        action = (
            "hold"
            if previous_executed is None or executed_band == previous_executed
            else "increase"
            if executed_band > previous_executed
            else "decrease"
        )
        band = POSITION_BANDS[executed_band]
        base = POSITION_BANDS[base_band]
        results.append(
            {
                "position_algorithm_version": POSITION_ALGORITHM_VERSION,
                "decision_score": decision_score,
                "session_gap": gap,
                "band": executed_band,
                "status": band["status"],
                "status_label": band["label"],
                "min": band["min"],
                "max": band["max"],
                "mid": band["mid"],
                "base_band": base_band,
                "base_status": base["status"],
                "base_status_label": base["label"],
                "base_min": base["min"],
                "base_max": base["max"],
                "base_mid": base["mid"],
                "base_reason": base_reason,
                "risk_cap_band": cap_band,
                "risk_level": overlay["level"],
                "risk_triggered": overlay["triggered"],
                "risk_reasons": overlay["reasons"],
                "action": action,
                "reason": final_reason,
                "effective": "下一交易日",
                "raw_score": raw_score,
            }
        )
    return results
