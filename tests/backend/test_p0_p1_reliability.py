from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError
from backend.app.db.models import (
    Bar,
    DataSource,
    IngestionRun,
    Instrument,
    Job,
    ScanResult,
    SecretSetting,
    TradingCalendar,
)
from backend.app.providers.market_data import (
    BaoStockMarketDataProvider,
    CalendarSession,
    FallbackMarketDataProvider,
    InstrumentRecord,
    MarketBar,
    TushareMarketDataProvider,
)
from backend.app.repositories.jobs import JobRepository
from backend.app.services.backups import BackupService
from backend.app.services.data_health import DataHealthService
from backend.app.services.decision import DecisionService
from backend.app.services.ingestion import IngestionService
from backend.app.services.scan_changes import ScanChangeService
from backend.app.services.structured_ai import generate_validated_report


class ContractProvider:
    def __init__(self, key: str, bars: list[MarketBar], instruments: list[InstrumentRecord] | None = None):
        self.key = key
        self.name = key
        self.bars = bars
        self.instruments = instruments or []

    def fetch_bars(self, **_kwargs):
        return self.bars

    def fetch_calendar(self, *, start_date: date, end_date: date):
        return [CalendarSession(start_date, True), CalendarSession(end_date, True)]

    def fetch_instruments(self):
        return self.instruments


def _bar(day: int, *, high: float = 12) -> MarketBar:
    return MarketBar(datetime(2026, 7, day), 10, high, 9, 11, 100)


def test_daily_provider_chain_rejects_invalid_ohlc_and_uses_next_provider():
    invalid = ContractProvider("invalid", [_bar(27, high=8)])
    valid = ContractProvider("valid", [_bar(27), _bar(28)])
    provider = FallbackMarketDataProvider([invalid, valid], [valid])

    bars = provider.fetch_bars(
        ts_code="000001.SZ",
        timeframe="DAY",
        adjustment="QFQ",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )

    assert len(bars) == 2
    assert provider.last_bar_provider_key == "valid"
    assert [item.status for item in provider.last_bar_attempts] == ["rejected", "accepted"]


def test_daily_provider_chain_rejects_duplicate_dates():
    duplicate = ContractProvider("duplicate", [_bar(27), _bar(27)])
    provider = FallbackMarketDataProvider([duplicate], [duplicate])
    with pytest.raises(RuntimeError, match="all daily"):
        provider.fetch_bars(
            ts_code="000001.SZ",
            timeframe="DAY",
            adjustment="QFQ",
            start_date=date(2026, 7, 27),
            end_date=date(2026, 7, 28),
        )


class _FakeBaoResult:
    error_code = "0"
    error_msg = "success"
    fields = ["calendar_date", "is_trading_day"]

    def __init__(self):
        self.rows = [["2026-07-27", "1"], ["2026-07-28", "1"]]
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


def test_baostock_calendar_adapter_returns_explicit_sessions(monkeypatch):
    fake = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0", error_msg="success"),
        logout=lambda: None,
        query_trade_dates=lambda **_kwargs: _FakeBaoResult(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)

    sessions = BaoStockMarketDataProvider().fetch_calendar(
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )

    assert sessions == [CalendarSession(date(2026, 7, 27), True), CalendarSession(date(2026, 7, 28), True)]


def test_calendar_chain_falls_back_after_tushare_rate_limit():
    primary = ContractProvider("tushare", [_bar(27)])
    fallback = ContractProvider("baostock", [_bar(27)])
    primary.fetch_calendar = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("trade_cal frequency rate limit")
    )
    provider = FallbackMarketDataProvider([primary], [primary, fallback])

    sessions = provider.fetch_calendar(start_date=date(2026, 7, 27), end_date=date(2026, 7, 28))

    assert len(sessions) == 2
    assert provider.last_calendar_provider_key == "baostock"
    assert [item.status for item in provider.last_calendar_attempts] == ["rejected", "accepted"]


def test_bar_chain_circuits_rate_limited_provider_for_remaining_symbols():
    primary = ContractProvider("tushare", [_bar(27)])
    fallback = ContractProvider("baostock", [_bar(27)])
    calls = {"tushare": 0}

    def rate_limited(**_kwargs):
        calls["tushare"] += 1
        raise RuntimeError("API rate limit exceeded")

    primary.fetch_bars = rate_limited
    provider = FallbackMarketDataProvider([primary, fallback], [fallback])
    kwargs = {
        "ts_code": "000001.SZ",
        "timeframe": "DAY",
        "adjustment": "QFQ",
        "start_date": date(2026, 7, 27),
        "end_date": date(2026, 7, 27),
    }

    provider.fetch_bars(**kwargs)
    provider.fetch_bars(**kwargs)

    assert calls["tushare"] == 1
    assert provider.last_bar_attempts[0].status == "skipped"
    assert provider.last_bar_provider_key == "baostock"


def test_large_adjusted_batch_routes_directly_to_baostock():
    primary = ContractProvider("tushare", [_bar(27)])
    fallback = ContractProvider("baostock", [_bar(27)])
    provider = FallbackMarketDataProvider([primary, fallback], [fallback])
    provider.begin_batch(total=100, timeframe="DAY", adjustment="QFQ")

    provider.fetch_bars(
        ts_code="000001.SZ",
        timeframe="DAY",
        adjustment="QFQ",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 27),
    )

    assert provider.last_bar_attempts[0].provider == "tushare"
    assert provider.last_bar_attempts[0].status == "skipped"
    assert provider.last_bar_provider_key == "baostock"


class _EmptyFrame:
    empty = True


def test_tushare_printed_rate_limit_trips_cross_symbol_circuit(monkeypatch):
    calls = {"tushare": 0}

    def rate_limited_pro_bar(**_kwargs):
        calls["tushare"] += 1
        print("抱歉，您访问接口(adj_factor)频率超限(1次/分钟)。")
        return _EmptyFrame()

    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_bar=rate_limited_pro_bar))
    primary = TushareMarketDataProvider(client=SimpleNamespace(), max_attempts=3)
    fallback = ContractProvider("baostock", [_bar(27)])
    provider = FallbackMarketDataProvider([primary, fallback], [fallback])
    kwargs = {
        "ts_code": "000001.SZ",
        "timeframe": "DAY",
        "adjustment": "QFQ",
        "start_date": date(2026, 7, 27),
        "end_date": date(2026, 7, 27),
    }

    provider.fetch_bars(**kwargs)
    provider.fetch_bars(**kwargs)

    assert calls["tushare"] == 1
    assert provider.disabled_bar_providers["tushare"]
    assert provider.last_bar_attempts[0].status == "skipped"
    assert provider.last_bar_provider_key == "baostock"


class _FakeBaoBarResult:
    error_code = "0"
    error_msg = "success"

    def __init__(self):
        self.rows = [["2026-07-27", "10", "12", "9", "11", "100", "1100", "1.2"]]
        self.index = -1

    def next(self):
        self.index += 1
        return self.index < len(self.rows)

    def get_row_data(self):
        return self.rows[self.index]


def test_baostock_reuses_one_login_across_calendar_and_symbols(monkeypatch):
    calls = {"login": 0, "logout": 0}

    def login():
        calls["login"] += 1
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout():
        calls["logout"] += 1

    fake = SimpleNamespace(
        login=login,
        logout=logout,
        query_trade_dates=lambda **_kwargs: _FakeBaoResult(),
        query_history_k_data_plus=lambda *_args, **_kwargs: _FakeBaoBarResult(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider()

    provider.fetch_calendar(start_date=date(2026, 7, 27), end_date=date(2026, 7, 28))
    for code in ("000001.SZ", "600000.SH"):
        provider.fetch_bars(
            ts_code=code,
            timeframe="DAY",
            adjustment="QFQ",
            start_date=date(2026, 7, 27),
            end_date=date(2026, 7, 27),
        )

    assert calls == {"login": 1, "logout": 0}
    provider.close()
    assert calls == {"login": 1, "logout": 1}


def test_master_data_chain_rejects_bad_contract_and_persists_akshare_fallback(session):
    invalid = ContractProvider(
        "tushare",
        [_bar(27)],
        [InstrumentRecord("bad", "bad.SZ", "SZ", "")],
    )
    valid = ContractProvider(
        "akshare",
        [_bar(27)],
        [InstrumentRecord("949001", "949001.SH", "SH", "主数据样本")],
    )
    session.add(Instrument(code="949099", ts_code="949099.SH", exchange="SH", name="已退市样本", status="active"))
    session.commit()
    provider = FallbackMarketDataProvider(
        [invalid, valid],
        [invalid],
        instrument_providers=[invalid, valid],
    )

    result = IngestionService(session, provider).refresh_master_data()

    assert result == {
        "status": "completed",
        "source": "akshare",
        "rows_written": 1,
        "freshness": "partial",
    }
    instrument = session.scalar(select(Instrument).where(Instrument.code == "949001"))
    assert instrument is not None and instrument.name == "主数据样本"
    assert session.scalar(select(Instrument).where(Instrument.code == "949099")).status == "inactive"
    assert [item.status for item in provider.last_instrument_attempts] == ["rejected", "accepted"]
    source = session.scalar(select(DataSource).where(DataSource.key == "akshare"))
    assert source is not None and source.capabilities["missing_instrument_fields"] == [
        "area",
        "industry",
        "list_date",
        "status",
    ]


def test_master_data_uses_saved_rows_as_stale_when_remote_is_unavailable(session):
    session.add(Instrument(code="949002", ts_code="949002.SH", exchange="SH", name="已保存样本"))
    session.commit()
    provider = ContractProvider("offline", [_bar(27)])
    provider.fetch_instruments = lambda: (_ for _ in ()).throw(RuntimeError("Authorization: super-secret"))
    result = IngestionService(session, provider).refresh_master_data()
    assert result["status"] == "partial"
    assert result["source"] == "saved"
    assert result["freshness"] == "stale"
    run = session.scalar(select(IngestionRun).where(IngestionRun.kind == "master_data"))
    assert run is not None and "super-secret" not in (run.error or "")
    assert "[REDACTED]" in (run.error or "")


def test_job_heartbeat_event_is_committed_before_concurrent_cancel(session):
    job = Job(kind="data.refresh", status="running", payload={})
    session.add(job)
    session.commit()
    repository = JobRepository(session)

    repository.heartbeat(job, "refreshing", 10)
    with Session(session.get_bind()) as other_session:
        other_job = other_session.get(Job, job.id)
        assert other_job is not None
        JobRepository(other_session).cancel(other_job)

    session.commit()
    session.refresh(job)
    assert sorted(event.sequence for event in job.events) == [1, 2]


def test_job_stages_are_rebuilt_from_existing_events(session):
    job = Job(kind="data.refresh", status="running", payload={})
    session.add(job)
    session.commit()
    repository = JobRepository(session)
    repository.append_stage_event(job, "stage_started", "bars", processed=0, coverage_rate=0)
    repository.append_stage_event(job, "stage_progress", "bars", processed=3, coverage_rate=0.5)
    repository.append_stage_event(job, "stage_completed", "bars", processed=6, coverage_rate=1.0)

    assert repository.stages(job) == [
        pytest.approx(
            {
                "stage_key": "bars",
                "status": "completed",
                "processed": 6,
                "coverage_rate": 1.0,
                "updated_at": repository.stages(job)[0]["updated_at"],
            }
        )
    ]


def test_scan_changes_use_objective_states_for_same_configuration(session):
    instrument_a = Instrument(code="940001", ts_code="940001.SZ", status="active")
    instrument_b = Instrument(code="940002", ts_code="940002.SZ", status="active")
    session.add_all([instrument_a, instrument_b])
    session.flush()
    previous = Job(
        kind="scan.buy",
        status="completed",
        payload={"types": ["2"]},
        result={"algorithm_version": "chan-core-v2-memory-1"},
        created_at=datetime(2026, 7, 28, 9),
    )
    current = Job(
        kind="scan.buy",
        status="completed",
        payload={"types": ["2"]},
        result={"algorithm_version": "chan-core-v2-memory-1"},
        created_at=datetime(2026, 7, 29, 9),
    )
    session.add_all([previous, current])
    session.flush()
    session.add_all(
        [
            ScanResult(
                job_id=previous.id, instrument_id=instrument_a.id, scan_kind="scan.buy", signal_type="2"
            ),
            ScanResult(
                job_id=current.id, instrument_id=instrument_a.id, scan_kind="scan.buy", signal_type="2"
            ),
            ScanResult(
                job_id=current.id, instrument_id=instrument_b.id, scan_kind="scan.buy", signal_type="2"
            ),
        ]
    )
    session.commit()

    result = ScanChangeService(session).compare(current.id)

    assert result["comparable"] is True
    assert {item["code"]: item["state"] for item in result["items"]} == {
        "940001": "continued_hit",
        "940002": "new_hit",
    }


def test_partial_scan_is_not_comparable(session):
    job = Job(kind="scan.buy", status="partial", payload={})
    session.add(job)
    session.commit()
    assert ScanChangeService(session).compare(job.id)["reason"] == "current_job_incomplete"


def test_data_health_marks_outdated_saved_calendar_stale(session):
    service = DataHealthService(session, get_settings())

    item = service._calendar(date(2020, 1, 2), date(2020, 1, 5))

    assert item["status"] == "stale"
    assert item["data_time"] == "2020-01-05"
    assert item["missing_fields"] == ["current_trading_calendar"]


def test_data_health_requires_today_only_after_daily_settlement_cutoff(session):
    session.add_all(
        [
            TradingCalendar(trade_date=date(2030, 7, 29), is_open=True, source="test"),
            TradingCalendar(trade_date=date(2030, 7, 30), is_open=True, source="test"),
        ]
    )
    session.commit()
    service = DataHealthService(session, get_settings())
    timezone = ZoneInfo("Asia/Shanghai")

    assert service._expected_trade_date(datetime(2030, 7, 30, 10, tzinfo=timezone)) == date(2030, 7, 29)
    assert service._expected_trade_date(datetime(2030, 7, 30, 17, tzinfo=timezone)) == date(2030, 7, 30)


def test_decision_review_context_blocks_next_session_output_for_incomplete_data(session):
    service = DecisionService(session, get_settings(), "local-user")
    health = {
        "expected_trade_date": "2026-07-29",
        "categories": [
            {"key": "daily_bars", "status": "partial"},
            {"key": "master_data", "status": "fresh"},
            {"key": "trading_calendar", "status": "missing"},
        ],
    }
    radar = {
        "trade_date": "2026-07-24",
        "freshness": "fresh",
        "generated_at": "2026-07-29T09:00:00Z",
    }

    context = service._review_context(health, radar)

    assert context["mode"] == "historical_review"
    assert context["freshness"] == "stale"
    assert context["usable_for_next_session"] is False
    assert context["applicable_to"] == "\u4ec5\u4f9b\u5386\u53f2\u590d\u76d8"
    assert context["blocking_modules"] == ["daily_bars", "market_radar", "trading_calendar"]


def test_decision_review_context_allows_latest_complete_trade_date(session):
    service = DecisionService(session, get_settings(), "local-user")
    health = {
        "expected_trade_date": "2026-07-29",
        "categories": [
            {"key": "daily_bars", "status": "fresh"},
            {"key": "master_data", "status": "fresh"},
            {"key": "trading_calendar", "status": "fresh"},
        ],
    }
    radar = {
        "trade_date": "2026-07-29",
        "freshness": "fresh",
        "generated_at": "2026-07-29T09:00:00Z",
    }

    context = service._review_context(health, radar)

    assert context["mode"] == "next_session_preparation"
    assert context["freshness"] == "fresh"
    assert context["usable_for_next_session"] is True
    assert context["blocking_modules"] == []


def test_decision_radar_changes_are_objective_adjacent_snapshot_deltas():
    current = {
        "breadth": {"advance_rate": 0.2, "above_ma20_rate": 0.3, "limit_down_count": 44},
        "liquidity": {"amount_yi": 10000},
        "limit_ecology": {"limit_up_count": 40},
    }
    previous = {
        "breadth": {"advance_rate": 0.4, "above_ma20_rate": 0.35, "limit_down_count": 20},
        "liquidity": {"amount_yi": 12000},
        "limit_ecology": {"limit_up_count": 30},
    }

    result = DecisionService._radar_changes(current, previous, "2026-07-28")

    assert result["previous_trade_date"] == "2026-07-28"
    assert {item["key"]: item["delta"] for item in result["items"]} == {
        "advance_rate": -20.0,
        "above_ma20_rate": -5.0,
        "amount_yi": -2000.0,
        "limit_up_count": 10.0,
        "limit_down_count": 24.0,
    }


def test_sqlite_backup_removes_secrets_and_verifies(session, tmp_path):
    session.add(SecretSetting(key="TEST_KEY", encrypted_value="encrypted-secret"))
    session.commit()
    settings = get_settings().model_copy(update={"backup_dir": tmp_path})
    service = BackupService(settings)

    created = service.create()
    verified = service.verify(created["name"])

    assert verified["valid"] is True
    import zipfile

    with zipfile.ZipFile(tmp_path / created["name"]) as archive:
        database = tmp_path / "verified.sqlite"
        database.write_bytes(archive.read("database.sqlite"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM secret_settings").fetchone()[0] == 0
    with pytest.raises(AppError):
        service.verify("../escape.zip")
    with pytest.raises(AppError) as missing:
        service.verify("chan-backup-2026-08-27T00-00-00Z-1234abcd.zip")
    assert missing.value.code == "backup_not_found"

    outside = tmp_path.parent / "chan-backup-2026-08-27T00-00-00Z-deadbeef.zip"
    outside.write_bytes((tmp_path / created["name"]).read_bytes())
    linked = tmp_path / outside.name
    try:
        linked.symlink_to(outside)
    except OSError:
        pass
    else:
        with pytest.raises(AppError) as escaped_link:
            service.verify(linked.name)
        assert escaped_link.value.code == "backup_not_found"
    finally:
        outside.unlink(missing_ok=True)


def test_server_backup_download_requires_explicit_opt_in(tmp_path):
    settings = get_settings().model_copy(
        update={
            "environment": "server",
            "backup_dir": tmp_path,
            "backup_download_enabled": False,
        }
    )

    with pytest.raises(AppError) as exc_info:
        BackupService(settings).download_path("chan-backup-2026-08-27T00-00-00Z-1234abcd.zip")

    assert exc_info.value.code == "backup_download_disabled"


class JsonProvider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    async def generate(self, _request):
        self.calls += 1
        return "not-json" if self.calls == 1 else json.dumps(self.payload, ensure_ascii=False)


@pytest.mark.asyncio
async def test_structured_ai_retries_bad_json_and_protects_authoritative_fields():
    authoritative = {"score": 73, "classification": "rule-owned", "input_freshness": "fresh"}
    payload = {
        "analysis_status": "complete",
        "summary": "Evidence is mixed.",
        "evidence": [{"claim": "positive", "source": "bars", "data_time": "2026-07-29"}],
        "counter_evidence": [{"claim": "risk", "source": "bars", "data_time": "2026-07-29"}],
        "risks": ["volatility"],
        "missing_data": [],
        "upgrade_conditions": ["more evidence"],
        "downgrade_conditions": ["structure breaks"],
        "confidence": 0.6,
        "requires_human_review": True,
        "authoritative_rule_fields": authoritative,
    }
    provider = JsonProvider(payload)

    report = await generate_validated_report(
        provider,
        system_prompt="system",
        user_prompt="user",
        authoritative_rule_fields=authoritative,
        snapshot={"data_time": "2026-07-29"},
    )

    assert report.successful is True
    assert provider.calls == 2
    assert report.payload["authoritative_rule_fields"] == authoritative


def test_daily_health_excludes_inactive_and_not_yet_listed_instruments(session):
    expected = date(2030, 7, 29)
    eligible = Instrument(
        code="949010", ts_code="949010.SH", exchange="SH", name="正常样本", status="active", list_date=expected
    )
    future = Instrument(
        code="949011", ts_code="949011.SH", exchange="SH", name="待上市样本", status="active", list_date=date(2030, 7, 30)
    )
    inactive = Instrument(
        code="949012", ts_code="949012.SH", exchange="SH", name="退市样本", status="inactive", list_date=date(2020, 1, 1)
    )
    session.add_all([eligible, future, inactive])
    session.flush()
    for instrument in (eligible, inactive):
        session.add(
            Bar(
                instrument_id=instrument.id,
                timeframe="DAY",
                adjustment="QFQ",
                bar_time=datetime(2030, 7, 29),
                open=10,
                high=11,
                low=9,
                close=10.5,
                volume=100,
            )
        )
    session.commit()

    health = DataHealthService(session, get_settings())._daily_bars(expected)

    assert health["status"] == "fresh"
    assert health["coverage_rate"] == 1.0
