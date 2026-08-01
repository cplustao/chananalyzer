from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.db.models import Bar, DataSource, Industry, Instrument, TradingCalendar
from backend.app.db.models.common import utcnow
from backend.app.providers.market_data import CalendarSession, InstrumentRecord, MarketBar


class IngestionRepository:
    def __init__(self, session: Session):
        self.session = session

    def data_source(self, key: str, name: str, capabilities: dict[str, Any] | None = None) -> DataSource:
        source = self.session.scalar(select(DataSource).where(DataSource.key == key))
        if source is None:
            source = DataSource(
                key=key,
                name=name,
                capabilities=capabilities or {"bars": ["DAY"], "adjustments": ["QFQ", "HFQ", "NONE"]},
            )
            self.session.add(source)
            self.session.flush()
        elif source.name != name:
            source.name = name
        if capabilities is not None:
            source.capabilities = {**(source.capabilities or {}), **capabilities}
        self.session.flush()
        return source

    def refresh_targets(self, codes: list[str] | None = None) -> list[Instrument]:
        statement = (
            select(Instrument)
            .where(
                Instrument.status == "active",
                Instrument.ts_code.is_not(None),
                func.length(Instrument.code) == 6,
            )
            .order_by(Instrument.code)
        )
        if codes:
            statement = statement.where(Instrument.code.in_(codes))
        return list(self.session.scalars(statement).all())

    def saved_instrument_count(self) -> int:
        return len(self.session.scalars(select(Instrument.id)).all())

    def upsert_instruments(self, items: Iterable[InstrumentRecord], *, source: str) -> int:
        count = 0
        now = utcnow()
        industries: dict[str, Industry] = {}
        for item in items:
            industry = None
            if item.industry:
                industry = industries.get(item.industry)
                if industry is None:
                    industry = self.session.scalar(select(Industry).where(Industry.name == item.industry))
                    if industry is None:
                        industry = Industry(name=item.industry, source=source)
                        self.session.add(industry)
                        self.session.flush()
                    else:
                        industry.source = source
                    industries[item.industry] = industry
            row = self.session.scalar(select(Instrument).where(Instrument.code == item.code))
            source_payload = {"master_source": source, "master_fetched_at": now.isoformat()}
            if row is None:
                row = Instrument(
                    code=item.code,
                    ts_code=item.ts_code,
                    exchange=item.exchange,
                    name=item.name,
                    area=item.area,
                    industry_id=industry.id if industry else None,
                    list_date=item.list_date,
                    status=item.status,
                    source_payload=source_payload,
                )
                self.session.add(row)
            else:
                row.ts_code = item.ts_code
                row.exchange = item.exchange
                row.name = item.name
                row.status = item.status
                row.source_payload = {**(row.source_payload or {}), **source_payload}
                if item.area is not None:
                    row.area = item.area
                if item.list_date is not None:
                    row.list_date = item.list_date
                if industry is not None:
                    row.industry_id = industry.id
            count += 1
        self.session.flush()
        return count

    def reconcile_active_instruments(self, active_codes: Iterable[str]) -> int:
        authoritative = {str(code).strip() for code in active_codes if str(code).strip()}
        changed = 0
        for row in self.session.scalars(
            select(Instrument).where(
                Instrument.status == "active",
                func.length(Instrument.code) == 6,
            )
        ).all():
            if row.code not in authoritative:
                row.status = "inactive"
                changed += 1
        self.session.flush()
        return changed
    def upsert_bars(
        self,
        *,
        instrument_id: int,
        timeframe: str,
        adjustment: str,
        data_source_id: int | None,
        bars: Iterable[MarketBar],
    ) -> int:
        now = utcnow()
        rows = [
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "adjustment": adjustment,
                "bar_time": item.bar_time,
                "open": item.open,
                "high": item.high,
                "low": item.low,
                "close": item.close,
                "volume": item.volume,
                "amount": item.amount,
                "turnover_rate": item.turnover_rate,
                "data_source_id": data_source_id,
                "quality_status": "ok",
                "created_at": now,
                "updated_at": now,
            }
            for item in bars
        ]
        if not rows:
            return 0
        dialect = self.session.get_bind().dialect.name
        conflict_columns = ["instrument_id", "timeframe", "adjustment", "bar_time"]
        update_columns = {
            key: getattr(
                (sqlite_insert(Bar) if dialect == "sqlite" else postgresql_insert(Bar)).excluded,
                key,
            )
            for key in (
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
                "data_source_id",
                "quality_status",
                "updated_at",
            )
        }
        if dialect == "sqlite":
            statement = sqlite_insert(Bar).values(rows)
            statement = statement.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)
        elif dialect == "postgresql":
            statement = postgresql_insert(Bar).values(rows)
            statement = statement.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)
        else:
            return self._portable_upsert(rows)
        self.session.execute(statement)
        return len(rows)

    def mark_series_stale(self, *, instrument_id: int, timeframe: str, adjustment: str) -> int:
        rows = self.session.scalars(
            select(Bar).where(
                Bar.instrument_id == instrument_id,
                Bar.timeframe == timeframe,
                Bar.adjustment == adjustment,
            )
        ).all()
        for row in rows:
            row.quality_status = "stale"
        return len(rows)

    def saved_calendar(self, *, start_date, end_date, exchange: str = "CN") -> list[CalendarSession]:
        rows = self.session.scalars(
            select(TradingCalendar)
            .where(
                TradingCalendar.exchange == exchange,
                TradingCalendar.trade_date >= start_date,
                TradingCalendar.trade_date <= end_date,
            )
            .order_by(TradingCalendar.trade_date)
        ).all()
        return [CalendarSession(trade_date=row.trade_date, is_open=row.is_open) for row in rows]

    def _portable_upsert(self, rows: list[dict[str, Any]]) -> int:
        for values in rows:
            row = self.session.scalar(
                select(Bar).where(
                    Bar.instrument_id == values["instrument_id"],
                    Bar.timeframe == values["timeframe"],
                    Bar.adjustment == values["adjustment"],
                    Bar.bar_time == values["bar_time"],
                )
            )
            if row is None:
                self.session.add(Bar(**values))
            else:
                for key, value in values.items():
                    if key not in {"instrument_id", "timeframe", "adjustment", "bar_time", "created_at"}:
                        setattr(row, key, value)
        return len(rows)

    def upsert_calendar(
        self, sessions: Iterable[CalendarSession], *, source: str, exchange: str = "CN"
    ) -> int:
        count = 0
        for item in sessions:
            row = self.session.get(TradingCalendar, {"trade_date": item.trade_date, "exchange": exchange})
            if row is None:
                self.session.add(
                    TradingCalendar(
                        trade_date=item.trade_date,
                        exchange=exchange,
                        is_open=item.is_open,
                        source=source,
                    )
                )
            else:
                row.is_open = item.is_open
                row.source = source
            count += 1
        return count

