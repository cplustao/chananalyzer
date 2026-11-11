"""End to end over the real chain: bars -> radar -> data gate -> Chan scan -> API.

Nothing in the chain is faked. Bars are deterministic random walks; the seeds
were picked because the real engine finds a recent buy signal in them, so this
test also breaks if the engine, the gate or the candidate rules drift apart.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta

from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import (
    DataHealthSnapshot,
    IngestionRun,
    Instrument,
    Job,
    LimitUpEvent,
    ScanResult,
    TradingCalendar,
)
from backend.app.db.models.common import utcnow
from backend.app.providers.market_data import MarketBar
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services.data_health import ANALYSIS_SERIES_KEY, DataHealthService, expected_trade_date
from backend.app.services.job_handlers import JobHandlers

HISTORY = 400
# Seeds whose last three sessions hold a buy signal of the balanced preset (2 / 3a / 3b).
SIGNAL_SEEDS = {"000001": 0, "000002": 40}
QUIET_SEED = 1


def walk(seed: int, days: list[date]) -> list[MarketBar]:
    rng = random.Random(seed)
    price, bars = 10.0, []
    for day in days:
        change = rng.gauss(0, 0.022)
        open_ = price
        close = max(1.0, price * (1 + change))
        high = max(open_, close) * (1 + abs(rng.gauss(0, 0.006)))
        low = min(open_, close) * (1 - abs(rng.gauss(0, 0.006)))
        bars.append(MarketBar(datetime.combine(day, datetime.min.time()), round(open_, 2), round(high, 2),
                              round(low, 2), round(close, 2), 1e6, close * 1e6))
        price = close
    return bars


def market(session) -> tuple[date, dict[str, Instrument]]:
    today = date.today()
    start = today - timedelta(days=HISTORY * 2)
    day = start
    while day <= today + timedelta(days=30):
        session.add(TradingCalendar(exchange="CN", trade_date=day, is_open=day.weekday() < 5))
        day += timedelta(days=1)
    session.flush()
    expected = expected_trade_date(session)
    assert expected is not None
    sessions = list(session.scalars(
        select(TradingCalendar.trade_date)
        .where(TradingCalendar.is_open.is_(True), TradingCalendar.trade_date <= expected)
        .order_by(TradingCalendar.trade_date.desc())
        .limit(HISTORY)
    ))[::-1]

    repository = IngestionRepository(session)
    source = repository.data_source("baostock", "baostock")
    stocks: dict[str, Instrument] = {}
    plan = {**SIGNAL_SEEDS, "000003": QUIET_SEED, "000004": QUIET_SEED + 1}
    for code in plan:
        stocks[code] = Instrument(code=code, ts_code=f"{code}.SZ", exchange="SZSE", name=f"测试{code}",
                                  status="active", list_date=sessions[0] - timedelta(days=400))
    # Listed a few months ago: too short for Chan analysis, and too new to count against coverage.
    stocks["301999"] = Instrument(code="301999", ts_code="301999.SZ", exchange="SZSE", name="次新股",
                                  status="active", list_date=sessions[-60])
    session.add_all(stocks.values())
    session.flush()
    for code, instrument in stocks.items():
        days = sessions if code != "301999" else sessions[-60:]
        repository.upsert_bars(instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ",
                               data_source_id=source.id, bars=walk(plan.get(code, 99), days))
    session.add(IngestionRun(kind="master_data", status="completed", freshness="fresh", data_time=utcnow(),
                             provider_chain=[{"provider": "baostock", "status": "accepted"}]))
    session.add(LimitUpEvent(instrument_id=stocks["000004"].id, legacy_code="000004", trade_date=expected,
                             close=10.0, pct_change=10.0, consecutive_boards=1))
    session.commit()
    return expected, stocks


def run(session, kind: str, payload: dict) -> Job:
    job = Job(kind=kind, status="running", payload=payload, started_at=utcnow())
    session.add(job)
    session.commit()
    # What the worker does around a handler.
    result = JobHandlers(session).execute(job)
    if job.status == "running":
        JobRepository(session).complete(job, result=result)
    session.commit()
    session.refresh(job)
    return job


def test_bars_flow_through_the_real_engine_and_gate_to_today_candidates(client, session):
    expected, stocks = market(session)

    # The real radar and the real gate, computed from the bars above.
    radar = run(session, "market_radar.refresh", {})
    assert radar.status == "completed", radar.error
    DataHealthService(session, get_settings()).refresh_analysis_series()
    health = DataHealthService(session, get_settings()).refresh_cache()
    session.commit()
    assert health["decision_usable"] is True, health["blocking_reasons"]
    assert health["as_of_trade_date"] == expected.isoformat()
    series = session.get(DataHealthSnapshot, ANALYSIS_SERIES_KEY).payload
    assert (series["eligible_count"], series["usable_count"]) == (4, 4)

    scan = run(session, "screen.smart", {"types": ["2", "3a", "3b"], "scan_side": "buy", "exclude_st": True})
    assert scan.status == "completed", scan.error
    assert scan.result["data_as_of"] == expected.isoformat()
    assert scan.result["insufficient_history"] == 1
    found = {row.instrument_id for row in session.scalars(select(ScanResult).where(ScanResult.job_id == scan.id))}
    assert {stocks[code].id for code in SIGNAL_SEEDS} <= found
    assert stocks["301999"].id not in found

    today = client.get("/api/v1/decision/today").json()
    candidates = today["today_candidates"]
    assert candidates["is_current"] is True
    assert candidates["basis_trade_date"] == expected.isoformat()
    codes = {item["code"] for item in candidates["items"]}
    assert set(SIGNAL_SEEDS) <= codes
    assert "301999" not in codes
    window_start = date.fromisoformat(candidates["signal_window"]["start"])
    assert all(window_start <= date.fromisoformat(item["signal_date"]) <= expected for item in candidates["items"])
    assert all(item["signal_type"] in {"2", "3a", "3b"} for item in candidates["items"])

    # The same engine drives the stock page and the plan draft for a candidate.
    stock_id = stocks["000002"].id
    added = client.post("/api/v1/watchlists/default/items", json={"instrument_id": stock_id})
    item_id = next(item["id"] for item in added.json()["items"] if item["instrument"]["id"] == stock_id)
    draft = client.get(f"/api/v1/watchlists/default/items/{item_id}/plan-draft").json()
    assert draft["basis"]["structure_date"] == expected.isoformat()
    assert draft["basis"]["analysis_bar_count"] == HISTORY
    assert "买点" in draft["thesis"]
