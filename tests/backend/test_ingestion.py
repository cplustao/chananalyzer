from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.app.db.models import Bar, DataSource, IngestionRun, Instrument, TradingCalendar
from backend.app.providers.market_data import CalendarSession, MarketBar
from backend.app.services.ingestion import IngestionService


class FakeMarketDataProvider:
    key = "fake-market"
    name = "Fake Market"

    def __init__(self):
        self.calls: list[str] = []

    def fetch_calendar(self, *, start_date: date, end_date: date) -> list[CalendarSession]:
        return [
            CalendarSession(trade_date=start_date, is_open=True),
            CalendarSession(trade_date=end_date, is_open=True),
        ]

    def fetch_bars(
        self,
        *,
        ts_code: str,
        timeframe: str,
        adjustment: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBar]:
        self.calls.append(ts_code)
        return [
            MarketBar(
                bar_time=datetime.combine(start_date, datetime.min.time()),
                open=10,
                high=12,
                low=9,
                close=11.5,
                volume=100,
                amount=1000,
                turnover_rate=1.2,
            ),
            MarketBar(
                bar_time=datetime.combine(end_date, datetime.min.time()),
                open=11.5,
                high=13,
                low=11,
                close=12.5,
                volume=120,
                amount=1200,
                turnover_rate=1.5,
            ),
        ]


def _instrument_with_bar(session, code: str, ts_code: str) -> Instrument:
    instrument = session.scalar(select(Instrument).where(Instrument.code == code))
    if instrument is None:
        instrument = Instrument(code=code, ts_code=ts_code, exchange="SSE", name=f"测试{code}", status="active")
        session.add(instrument)
        session.flush()
    existing = session.scalar(
        select(Bar).where(
            Bar.instrument_id == instrument.id,
            Bar.timeframe == "DAY",
            Bar.adjustment == "QFQ",
            Bar.bar_time == datetime(2026, 7, 27),
        )
    )
    if existing is None:
        session.add(
            Bar(
                instrument_id=instrument.id,
                timeframe="DAY",
                adjustment="QFQ",
                bar_time=datetime(2026, 7, 27),
                open=9,
                high=10,
                low=8,
                close=9.5,
                volume=80,
            )
        )
    session.commit()
    return instrument


def test_native_ingestion_upserts_v2_bars_and_is_idempotent(session):
    instrument = _instrument_with_bar(session, "900001", "900001.SH")
    provider = FakeMarketDataProvider()
    service = IngestionService(session, provider)

    first = service.refresh_bars(
        codes=[instrument.code],
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )
    second = service.refresh_bars(
        codes=[instrument.code],
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )

    bars = session.scalars(
        select(Bar)
        .where(Bar.instrument_id == instrument.id, Bar.timeframe == "DAY", Bar.adjustment == "QFQ")
        .order_by(Bar.bar_time)
    ).all()
    assert len(bars) == 2
    assert bars[0].close == 11.5
    assert first["status"] == second["status"] == "completed"
    assert first["bar_rows_written"] == second["bar_rows_written"] == 2
    assert provider.calls == ["900001.SH", "900001.SH"]
    assert session.scalar(select(func.count()).select_from(DataSource).where(DataSource.key == provider.key)) == 1
    assert session.scalar(
        select(func.count()).select_from(TradingCalendar).where(TradingCalendar.source == provider.key)
    ) == 2
    assert session.scalar(
        select(func.count()).select_from(IngestionRun).where(IngestionRun.data_source_id.is_not(None))
    ) >= 2


def test_native_ingestion_bootstraps_active_instruments_without_existing_bars(session):
    active = Instrument(
        code="900006",
        ts_code="900006.SH",
        exchange="SH",
        name="待补行情",
        status="active",
    )
    inactive = Instrument(
        code="900007",
        ts_code="900007.SH",
        exchange="SH",
        name="已退市",
        status="inactive",
    )
    session.add_all([active, inactive])
    session.commit()
    provider = FakeMarketDataProvider()

    result = IngestionService(session, provider).refresh_bars(
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )

    assert result["status"] == "completed"
    assert "900006.SH" in provider.calls
    assert "900007.SH" not in provider.calls
    assert session.scalar(select(func.count()).select_from(Bar).where(Bar.instrument_id == active.id)) == 2


def test_native_ingestion_records_partial_failures(session):
    good = _instrument_with_bar(session, "900002", "900002.SH")
    bad = _instrument_with_bar(session, "900003", "900003.SH")

    class PartialProvider(FakeMarketDataProvider):
        key = "partial-market"

        def fetch_bars(self, **kwargs):
            if kwargs["ts_code"] == bad.ts_code:
                raise RuntimeError("模拟行情源失败")
            return super().fetch_bars(**kwargs)

    result = IngestionService(session, PartialProvider()).refresh_bars(
        codes=[good.code, bad.code],
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )

    assert result["status"] == "partial"
    assert (result["succeeded"], result["failed"]) == (1, 1)
    assert result["errors"] == [{"code": bad.code, "error": "模拟行情源失败"}]


def test_native_ingestion_uses_explicit_unadjusted_fallback_for_bj(session):
    instrument = Instrument(
        code="920000",
        ts_code="920000.BJ",
        exchange="BJ",
        name="北交所样本",
        status="active",
    )
    session.add(instrument)
    session.commit()

    class CapturingProvider(FakeMarketDataProvider):
        def __init__(self):
            super().__init__()
            self.adjustments = []

        def fetch_bars(self, **kwargs):
            self.adjustments.append(kwargs["adjustment"])
            return super().fetch_bars(**kwargs)

    provider = CapturingProvider()
    result = IngestionService(session, provider).refresh_bars(
        codes=["920000"],
        adjustment="QFQ",
        start_date=date(2026, 7, 27),
        end_date=date(2026, 7, 28),
    )

    assert result["status"] == "completed"
    assert result["adjustment_fallback_codes"] == ["920000"]
    assert provider.adjustments == ["NONE"]
    assert session.scalar(
        select(func.count()).select_from(Bar).where(
            Bar.instrument_id == instrument.id,
            Bar.adjustment == "NONE",
        )
    ) == 2

def test_default_refresh_end_is_not_locked_by_stale_calendar(session):
    session.add(TradingCalendar(trade_date=date(2030, 7, 29), is_open=True, source="test"))
    session.commit()
    service = IngestionService(session, FakeMarketDataProvider())

    before_close = service._settled_end_date(datetime(2030, 7, 30, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    after_close = service._settled_end_date(datetime(2030, 7, 30, 17, 0, tzinfo=ZoneInfo("Asia/Shanghai")))

    assert before_close == date(2030, 7, 29)
    assert after_close == date(2030, 7, 30)
