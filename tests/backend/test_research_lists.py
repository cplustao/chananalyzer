from __future__ import annotations

from datetime import date, datetime

from backend.app.db.models import (
    AnalysisReport,
    AnalysisRun,
    Instrument,
    IpoEvent,
    LimitUpEvent,
    LimitUpMetric,
)


def test_limit_up_list_restores_frequency_market_data_and_scores(client, session):
    instrument = Instrument(
        code="300001",
        ts_code="300001.SZ",
        exchange="SZSE",
        name="测试涨停股",
        status="active",
    )
    session.add(instrument)
    session.flush()
    events = []
    for day in (1, 2, 3):
        event = LimitUpEvent(
            instrument_id=instrument.id,
            legacy_code=instrument.code,
            trade_date=date(2026, 7, day),
            market="创业板",
            close=20 + day,
            pct_change=20,
            turnover_rate=8.5,
            consecutive_boards=day,
            raw_payload={"limit_order": 25_000_000},
        )
        session.add(event)
        events.append(event)
    session.flush()
    run = AnalysisRun(
        kind="limit_up",
        instrument_id=instrument.id,
        subject_date=date(2026, 7, 3),
        status="completed",
        algorithm_version="test",
        created_at=datetime(2026, 7, 3),
    )
    session.add(run)
    session.flush()
    session.add(
        LimitUpMetric(
            analysis_run_id=run.id,
            limit_up_event_id=events[-1].id,
            day_score=82,
            week_score=76,
            overall_score=80,
            day_classification="强主升",
            week_classification="主升候选",
        )
    )
    session.commit()

    response = client.get(
        "/api/v1/limit-ups?start_date=20260703&end_date=20260703"
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["instrument_id"] == instrument.id
    assert item["limit_up_count_7d"] == 3
    assert item["limit_up_count_180d"] == 3
    assert item["limit_order"] == 25_000_000
    assert item["analysis"]["day_score"] == 82
    assert item["analysis"]["overall_score"] == 80
    assert item["analysis"]["status"] == "completed"


def test_ipo_list_restores_potential_scores_and_instrument_link(
    client,
    session,
):
    instrument = Instrument(
        code="688001",
        ts_code="688001.SH",
        exchange="SSE",
        name="测试新股",
        status="active",
    )
    session.add(instrument)
    session.flush()
    run = AnalysisRun(
        kind="ipo",
        instrument_id=instrument.id,
        subject_date=date(2026, 7, 20),
        status="completed",
        algorithm_version="test",
        input_snapshot={"code": instrument.code},
        created_at=datetime(2026, 7, 20),
    )
    session.add(run)
    session.flush()
    session.add_all(
        [
            AnalysisReport(
                analysis_run_id=run.id,
                role="analyst",
                content="10倍潜力评分：8.5/10",
            ),
            AnalysisReport(
                analysis_run_id=run.id,
                role="decision",
                content="100倍潜力评分：6/10",
            ),
        ]
    )
    session.add(
        IpoEvent(
            instrument_id=instrument.id,
            legacy_code=instrument.code,
            listing_date=date(2026, 7, 18),
            issue_price=18.8,
            issue_pe=24.5,
            raw_payload={"industry": "软件服务", "market": "科创板"},
        )
    )
    session.commit()

    response = client.get(
        "/api/v1/ipos?start_date=20260701&end_date=20260731"
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["instrument_id"] == instrument.id
    assert item["analysis"]["status"] == "completed"
    assert item["analysis"]["ten_x_score"] == 8.5
    assert item["analysis"]["hundred_x_score"] == 6
