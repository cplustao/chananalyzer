from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.core.config import get_settings
from backend.app.db.models import Bar, DataHealthSnapshot, DataSource, Instrument, Job
from backend.app.db.models.common import utcnow
from backend.app.providers.market_data import (
    AkShareMarketDataProvider,
    BaoStockMarketDataProvider,
    MarketBar,
    TushareMarketDataProvider,
)
from backend.app.repositories.ingestion import IngestionRepository
from backend.app.repositories.jobs import JobRepository
from backend.app.services import job_handlers as job_handlers_module
from backend.app.services.bar_access import load_daily_bars
from backend.app.services.data_health import DataHealthService
from backend.app.services.job_handlers import JobHandlers


class Frame:
    empty = False

    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, _orientation):
        return self.rows


def test_tushare_and_akshare_normalize_to_shares_and_yuan(monkeypatch):
    monkeypatch.setattr(
        "tushare.pro_bar",
        lambda **_kwargs: Frame(
            [{"trade_date": "20260730", "open": 10, "high": 11, "low": 9, "close": 10.5, "vol": 123, "amount": 456}]
        ),
    )
    tushare = TushareMarketDataProvider(client=SimpleNamespace(), min_interval_seconds=0)
    tushare_bar = tushare.fetch_bars(
        ts_code="000001.SZ", timeframe="DAY", adjustment="NONE",
        start_date=date(2026, 7, 30), end_date=date(2026, 7, 30),
    )[0]
    assert (tushare_bar.volume, tushare_bar.amount) == (12_300, 456_000)
    assert (tushare_bar.raw_volume, tushare_bar.raw_amount) == (123, 456)

    monkeypatch.setattr(
        "akshare.stock_zh_a_hist",
        lambda **_kwargs: Frame(
            [{"日期": "2026-07-30", "开盘": 10, "最高": 11, "最低": 9, "收盘": 10.5, "成交量": 123, "成交额": 456_000, "换手率": 1.2}]
        ),
    )
    akshare_bar = AkShareMarketDataProvider().fetch_bars(
        ts_code="000001.SZ", timeframe="DAY", adjustment="NONE",
        start_date=date(2026, 7, 30), end_date=date(2026, 7, 30),
    )[0]
    assert (akshare_bar.volume, akshare_bar.amount) == (12_300, 456_000)


def test_baostock_keeps_native_shares_and_yuan():
    class Result:
        error_code = "0"
        error_msg = ""
        rows = [["2026-07-30", "10", "11", "9", "10.5", "12300", "456000", "1.2"]]
        index = -1

        def next(self):
            self.index += 1
            return self.index < len(self.rows)

        def get_row_data(self):
            return self.rows[self.index]

    provider = BaoStockMarketDataProvider()
    provider._client = SimpleNamespace(query_history_k_data_plus=lambda *_args, **_kwargs: Result())
    provider._logged_in = True
    bar = provider.fetch_bars(
        ts_code="000001.SZ", timeframe="DAY", adjustment="QFQ",
        start_date=date(2026, 7, 30), end_date=date(2026, 7, 30),
    )[0]
    assert (bar.volume, bar.amount) == (12_300, 456_000)


def test_research_series_uses_latest_source_without_stitching(session):
    first = DataSource(key="first", name="first")
    second = DataSource(key="second", name="second")
    instrument = Instrument(code="900001", ts_code="900001.SH", exchange="SH", name="样本")
    session.add_all([first, second, instrument])
    session.flush()
    session.add(
        Bar(
            instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ",
            bar_time=datetime(2026, 7, 29), open=10, high=11, low=9, close=10.5,
            volume=10_000, amount=100_000, data_source_id=first.id,
        )
    )
    session.commit()

    IngestionRepository(session).upsert_bars(
        instrument_id=instrument.id, timeframe="DAY", adjustment="QFQ",
        data_source_id=second.id,
        bars=[MarketBar(datetime(2026, 7, 30), 10, 11, 9, 10.5, 12_000, 120_000)],
    )
    session.commit()
    rows = list(session.scalars(select(Bar).order_by(Bar.bar_time)).all())
    assert [(row.quality_status, row.data_source_id) for row in rows] == [
        ("ok", first.id),
        ("ok", second.id),
    ]
    research_rows, adjustment = load_daily_bars(session, instrument.id)
    assert adjustment == "QFQ"
    assert [(row.bar_time.date(), row.data_source_id) for row in research_rows] == [
        (date(2026, 7, 30), second.id),
    ]


def test_structured_watchlist_research_round_trip(client, session):
    instrument = Instrument(code="900002", ts_code="900002.SH", exchange="SH", name="研究样本")
    session.add(instrument)
    session.commit()
    response = client.post(
        "/api/v1/watchlists/default/items",
        json={
            "instrument_id": instrument.id,
            "thesis": "趋势改善",
            "confirmation_trigger": "放量站稳前高",
            "invalidation_condition": "跌破中枢下沿",
            "next_action": "周五复盘",
            "next_review_date": "2026-08-28",
            "research_status": "pending",
            "tag_names": ["今日候选"],
        },
    )
    assert response.status_code == 200
    item = next(value for value in response.json()["items"] if value["instrument"]["id"] == instrument.id)
    assert item["thesis"] == "趋势改善"
    assert item["research_status"] == "pending"
    assert item["next_review_date"] == "2026-08-28"


def test_data_health_cache_avoids_recomputing_snapshot(session, monkeypatch):
    service = DataHealthService(session, get_settings())
    expected = {"status": "missing", "checked_at": "2026-08-27T00:00:00", "categories": []}
    session.add(DataHealthSnapshot(key="current", payload=expected, generated_at=utcnow()))
    session.commit()
    monkeypatch.setattr(service, "snapshot", lambda: (_ for _ in ()).throw(AssertionError("recomputed")))
    assert service.cached_snapshot(max_age_seconds=300) == expected


def _prepare_with_partial_market_data(session, monkeypatch, *, daily_bars_status):
    job = Job(kind="daily.prepare", status="running", payload={"run_screeners": False})
    session.add(job)
    session.commit()
    handler = JobHandlers.__new__(JobHandlers)
    handler.session = session
    handler.jobs = JobRepository(session)
    ran: list[str] = []
    handler._data_refresh = lambda _job, **_kwargs: {
        "status": "partial",
        "failed": 2,
        "coverage_rate": 0.997,
        "errors": [
            {"code": "920023", "error": "补采后仍有 1 个交易日缺少可用行情"},
            {"code": "920058", "error": "补采后仍有 10 个交易日缺少可用行情"},
        ],
    }
    handler._limit_up_refresh = lambda _job: ran.append("limit_up") or {"status": "completed"}
    handler._market_radar = lambda _job: ran.append("market_radar") or {"status": "completed"}
    categories = [
        {"key": "master_data", "status": "fresh"},
        {"key": "daily_bars", "status": daily_bars_status, "coverage_rate": 0.997},
        {"key": "trading_calendar", "status": "fresh"},
    ]

    class FakeHealth:
        def __init__(self, *_args):
            pass

        def snapshot(self):
            return {"categories": categories, "blocking_reasons": []}

        def refresh_cache(self, **_kwargs):
            return {"categories": categories, "blocking_reasons": [], "decision_usable": True}

    monkeypatch.setattr(job_handlers_module, "DataHealthService", FakeHealth)
    return handler, job, ran


def test_daily_prepare_continues_past_a_few_failed_stocks_when_the_gate_passes(session, monkeypatch):
    handler, job, ran = _prepare_with_partial_market_data(session, monkeypatch, daily_bars_status="fresh")

    result = handler._daily_prepare(job)

    assert job.status == "running"
    assert result["status"] == "completed"
    assert result["decision_usable"] is True
    assert ran == ["limit_up", "market_radar"]
    assert result["warnings"] == [{
        "stage": "market_data",
        "failed": 2,
        "codes": ["920023", "920058"],
        "message": "部分股票行情未补齐，已由质量门禁判断覆盖率后继续",
    }]
    assert job.failed == 2
    # The incomplete stage is not checkpointed as done, so a resumed run fetches it again.
    assert job.checkpoint["market_data"]["status"] == "failed"


def test_daily_prepare_still_stops_when_partial_market_data_fails_the_gate(session, monkeypatch):
    handler, job, ran = _prepare_with_partial_market_data(session, monkeypatch, daily_bars_status="partial")

    with pytest.raises(ValueError, match="行情质量门禁未通过：daily_bars"):
        handler._daily_prepare(job)

    assert ran == []


def test_daily_prepare_records_ordered_stages_and_decision_gate(session, monkeypatch):
    job = Job(kind="daily.prepare", status="running", payload={"run_screeners": False})
    session.add(job)
    session.commit()
    handler = JobHandlers.__new__(JobHandlers)
    handler.session = session
    handler.jobs = JobRepository(session)
    def refresh_market_data(current_job, *, finalize_partial):
        assert current_job.payload["auto_backfill"] is True
        assert finalize_partial is False
        return {"status": "completed", "coverage_rate": 1.0}

    handler._data_refresh = refresh_market_data
    handler._limit_up_refresh = lambda _job: {"status": "completed", "coverage_rate": 1.0}
    handler._market_radar = lambda _job: {"status": "completed", "coverage_rate": 1.0}

    categories = [
        {"key": "master_data", "status": "fresh"},
        {"key": "daily_bars", "status": "fresh", "coverage_rate": 1.0},
        {"key": "trading_calendar", "status": "fresh"},
    ]

    class FakeHealth:
        def __init__(self, *_args):
            pass

        def snapshot(self):
            return {"categories": categories, "blocking_reasons": []}

        def refresh_cache(self, **_kwargs):
            return {"categories": categories, "blocking_reasons": [], "decision_usable": True}

    monkeypatch.setattr(job_handlers_module, "DataHealthService", FakeHealth)
    result = handler._daily_prepare(job)

    assert result["decision_usable"] is True
    assert list((job.checkpoint or {}).keys()) == [
        "market_data", "quality_gate", "limit_up", "market_radar", "decision_readiness"
    ]
