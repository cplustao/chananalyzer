"""Convert daily market scores into a smoothed, executable position series.

This is a pure v2 domain copy of the protected 1.1 position rule. It intentionally
has no database or provider imports so API reads never initialize the legacy stack.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

POSITION_BANDS = (
    {"status": "risk", "label": "风险", "min": 0.00, "max": 0.20, "mid": 0.10},
    {"status": "weak", "label": "偏弱", "min": 0.15, "max": 0.35, "mid": 0.25},
    {"status": "neutral", "label": "震荡", "min": 0.30, "max": 0.55, "mid": 0.425},
    {"status": "warm", "label": "偏强", "min": 0.45, "max": 0.70, "mid": 0.575},
    {"status": "strong", "label": "强势", "min": 0.60, "max": 0.85, "mid": 0.725},
)
POSITION_THRESHOLDS = (35.0, 45.0, 58.0, 68.0)
MA_WINDOW = 5
HYSTERESIS_POINTS = 3.0
CONFIRM_DAYS = 2
EMERGENCY_SCORE = 20.0


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


def build_executable_positions(history: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if not history:
        return []
    scores = [float(item.get("score") or 0) for item in history]
    ma5_values: list[float] = []
    for index in range(len(scores)):
        values = scores[max(0, index - MA_WINDOW + 1): index + 1]
        ma5_values.append(round(sum(values) / len(values), 1))

    current_band = _band_for_score(ma5_values[0])
    pending_direction = 0
    pending_days = 0
    results: list[dict[str, Any]] = []
    for index, (raw_score, decision_score) in enumerate(zip(scores, ma5_values, strict=False)):
        previous_band = current_band
        reason = "初始仓位按当前平滑评分确定" if index == 0 else "平滑评分未形成连续调仓信号"
        if index > 0 and raw_score < EMERGENCY_SCORE and current_band > 0:
            current_band -= 1
            pending_direction = 0
            pending_days = 0
            reason = f"当日评分低于 {EMERGENCY_SCORE:.0f} 分，触发风险降仓一档"
        elif index > 0:
            direction = 0
            if current_band < len(POSITION_BANDS) - 1 and decision_score >= POSITION_THRESHOLDS[current_band] + HYSTERESIS_POINTS:
                direction = 1
            elif current_band > 0 and decision_score < POSITION_THRESHOLDS[current_band - 1] - HYSTERESIS_POINTS:
                direction = -1
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
                    current_band += direction
                    pending_direction = 0
                    pending_days = 0
                    reason = f"MA5 连续 {CONFIRM_DAYS} 日确认，{'提高' if direction > 0 else '降低'}一档仓位"
                else:
                    reason = f"MA5 已出现{'升仓' if direction > 0 else '降仓'}信号，等待第 {CONFIRM_DAYS} 日确认"

        action = "increase" if current_band > previous_band else "decrease" if current_band < previous_band else "hold"
        band = POSITION_BANDS[current_band]
        results.append({
            "decision_score": decision_score,
            "band": current_band,
            "status": band["status"],
            "status_label": band["label"],
            "min": band["min"],
            "max": band["max"],
            "mid": band["mid"],
            "action": action,
            "reason": reason,
            "effective": "下一交易日",
        })
    return results

