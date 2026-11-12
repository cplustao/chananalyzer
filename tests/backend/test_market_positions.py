from backend.app.domain.market_positions import build_executable_positions


def observation(
    trade_date: str,
    score: float,
    *,
    advance_rate: float = 0.55,
    decline_rate: float = 0.35,
    above_ma20_rate: float = 0.60,
    limit_up_count: float = 60,
    limit_down_count: float = 10,
    return_dispersion: float = 2.0,
    amount_ratio: float = 1.0,
    median_return_5d: float = 2.0,
) -> dict:
    return {
        "trade_date": trade_date,
        "score": score,
        "risk_inputs": {
            "advance_rate": advance_rate,
            "decline_rate": decline_rate,
            "above_ma20_rate": above_ma20_rate,
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "return_dispersion": return_dispersion,
            "amount_ratio": amount_ratio,
            "median_return_5d": median_return_5d,
        },
    }


def test_duplicate_trade_date_does_not_count_as_two_confirmation_days():
    positions = build_executable_positions(
        [
            observation("2026-07-01", 30),
            observation("2026-07-02", 60),
            observation("2026-07-02", 60),
        ]
    )

    assert len(positions) == 2
    assert positions[-1]["base_band"] == 0
    assert "等待第 2 个交易日确认" in positions[-1]["base_reason"]


def test_risk_overlay_immediately_caps_a_slow_high_position():
    history = [observation(f"2026-07-{day:02d}", 80) for day in range(1, 6)]
    history.append(
        observation(
            "2026-07-06",
            50,
            advance_rate=0.24,
            decline_rate=0.65,
            limit_up_count=20,
            limit_down_count=45,
            return_dispersion=4.5,
        )
    )

    position = build_executable_positions(history)[-1]

    assert position["base_band"] == 4
    assert position["band"] == 1
    assert position["action"] == "decrease"
    assert position["risk_level"] == "high"
    assert "立即降仓" in position["reason"]


def test_strong_score_with_weak_medium_term_breadth_cannot_use_top_band():
    position = build_executable_positions([observation("2026-07-01", 80, above_ma20_rate=0.42)])[-1]

    assert position["base_band"] == 4
    assert position["band"] == 3
    assert position["risk_level"] == "caution"
    assert position["risk_triggered"] is True


def test_risk_recovery_requires_two_clean_trade_days_and_one_band_at_a_time():
    positions = build_executable_positions(
        [
            observation("2026-07-01", 80),
            observation(
                "2026-07-02",
                40,
                advance_rate=0.18,
                decline_rate=0.72,
                limit_up_count=10,
                limit_down_count=60,
            ),
            observation("2026-07-03", 60),
            observation("2026-07-04", 60),
            observation("2026-07-05", 60),
        ]
    )

    assert positions[1]["band"] == 0
    assert positions[2]["band"] == 0
    assert positions[3]["band"] == 1
    assert positions[4]["band"] == 2
    assert positions[3]["action"] == "increase"
    assert positions[4]["action"] == "increase"
    assert "逐步恢复" in positions[3]["reason"]


def weekdays(start: str, count: int) -> list:
    from datetime import date, timedelta

    day, days = date.fromisoformat(start), []
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_snapshots_weeks_apart_are_not_smoothed_or_confirmed_together():
    sessions = weekdays("2026-07-01", 120)
    # The 2026 case: a weak start, then strong single days weeks apart.
    history = [
        observation("2026-07-30", 36),
        observation("2026-08-26", 57),
        observation("2026-08-27", 75),
        observation("2026-09-18", 79),
    ]

    positions = build_executable_positions(history, sessions)

    # 08-26 follows a 19-session gap: its MA is its own score, not an average with July.
    assert positions[1]["decision_score"] == 57
    assert positions[1]["session_gap"] > 3
    assert "评分中断" in positions[1]["base_reason"]
    # Never above the band held before the gap.
    assert all(item["base_band"] <= positions[0]["base_band"] for item in positions)
    assert positions[-1]["band"] <= positions[0]["band"]


def test_without_a_calendar_snapshots_count_as_consecutive_sessions():
    history = [observation("2026-07-30", 36), observation("2026-08-26", 57), observation("2026-08-27", 75)]

    assert build_executable_positions(history)[2]["decision_score"] == 56.0


def test_a_falling_medium_trend_caps_strong_days():
    def bounce(day: str, ma60: float) -> dict:
        item = observation(day, 76)
        item["risk_inputs"]["above_ma60_rate"] = ma60
        return item

    sessions = weekdays("2026-11-02", 10)
    down = build_executable_positions([bounce(day.isoformat(), 0.26) for day in sessions[:6]], sessions)
    weak = build_executable_positions([bounce(day.isoformat(), 0.40) for day in sessions[:6]], sessions)
    healthy = build_executable_positions([bounce(day.isoformat(), 0.60) for day in sessions[:6]], sessions)

    assert {item["band"] for item in down} == {1}
    assert "中期趋势向下" in down[-1]["reason"]
    assert max(item["band"] for item in weak) == 2
    assert healthy[-1]["band"] == 4


def test_risk_recovery_counts_sessions_not_snapshots():
    sessions = weekdays("2026-07-01", 30)
    crash = observation(sessions[1].isoformat(), 40, advance_rate=0.18, decline_rate=0.72)
    history = [observation(sessions[0].isoformat(), 80), crash,
               observation(sessions[2].isoformat(), 60), observation(sessions[4].isoformat(), 60),
               observation(sessions[5].isoformat(), 60)]

    positions = build_executable_positions(history, sessions)

    # sessions[3] is missing, so sessions[2] and sessions[4] are not two consecutive clean days.
    assert positions[3]["band"] == 0
    assert positions[4]["band"] == 1
