from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.db.models import Bar, DataSource, Industry, IngestionRun, Instrument, Job, TradingCalendar
from backend.app.db.models.common import utcnow
from backend.app.providers.market_data import CalendarSession, InstrumentRecord, MarketBar


class IngestionRepository:
    def __init__(self, session: Session):
        self.session = session

    def close_abandoned_runs(self, *, live_job_since: datetime) -> int:
        """Close ingestion runs left "running" by a worker that stopped mid-run.

        A run only advances inside a running job. When no job has a heartbeat
        after ``live_job_since``, nothing can still be writing these runs.
        """

        live = self.session.scalar(
            select(func.count()).select_from(Job).where(Job.status == "running", Job.heartbeat_at >= live_job_since)
        )
        if live:
            return 0
        runs = self.session.scalars(select(IngestionRun).where(IngestionRun.status == "running")).all()
        for run in runs:
            run.status = "interrupted"
            run.finished_at = utcnow()
            run.error = run.error or "Worker stopped before this run finished; the next refresh resumes from saved bars."
        self.session.commit()
        return len(runs)

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
                Instrument.asset_type == "stock",
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

    def first_daily_bar_date(self, codes: list[str] | None, adjustment: str) -> date | None:
        exchange = func.upper(func.coalesce(Instrument.exchange, ""))
        statement = select(func.min(Bar.bar_time)).join(Instrument).where(
            Instrument.status == "active", Instrument.asset_type == "stock",
            func.length(Instrument.code) == 6, Instrument.ts_code.is_not(None),
            Bar.timeframe == "DAY",
            or_(
                and_(exchange == "BJ", Bar.adjustment == "NONE"),
                and_(exchange != "BJ", Bar.adjustment == adjustment),
            ),
        )
        if codes:
            statement = statement.where(Instrument.code.in_(codes))
        value = self.session.scalar(statement)
        return value.date() if value else None

    def daily_series_dates(
        self, instrument_id: int, adjustment: str, end: date,
    ) -> tuple[date | None, set[date]]:
        rows = self.session.execute(
            select(Bar.bar_time, Bar.quality_status, Bar.data_source_id)
            .where(
                Bar.instrument_id == instrument_id, Bar.timeframe == "DAY",
                Bar.adjustment == adjustment,
                Bar.bar_time <= datetime.combine(end, time.max),
            ).order_by(Bar.bar_time)
        ).all()
        usable = [row for row in rows if row.quality_status == "ok"]
        source_id = usable[-1].data_source_id if usable else None
        # Match research's single-provider series. Zero-volume suspension rows
        # are observations too; an absent row is never assumed to be suspended.
        return (
            rows[0].bar_time.date() if rows else None,
            {row.bar_time.date() for row in usable if row.data_source_id == source_id},
        )

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
                    asset_type=item.asset_type,
                    source_payload=source_payload,
                )
                self.session.add(row)
            else:
                row.ts_code = item.ts_code
                row.exchange = item.exchange
                row.name = item.name
                row.status = item.status
                row.asset_type = item.asset_type
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
    def saved_closes(
        self, instrument_id: int, adjustment: str, data_source_id: int, start: date, end: date,
    ) -> dict[date, float]:
        rows = self.session.execute(
            select(Bar.bar_time, Bar.close).where(
                Bar.instrument_id == instrument_id, Bar.timeframe == "DAY",
                Bar.adjustment == adjustment, Bar.data_source_id == data_source_id,
                Bar.quality_status == "ok",
                Bar.bar_time >= datetime.combine(start, time.min),
                Bar.bar_time <= datetime.combine(end, time.max),
            )
        ).all()
        return {row.bar_time.date(): float(row.close) for row in rows}

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
                "raw_volume": item.raw_volume,
                "raw_amount": item.raw_amount,
                "turnover_rate": item.turnover_rate,
                "data_source_id": data_source_id,
                "quality_status": "ok",
                "trade_status": "trading" if item.volume > 0 else "suspended",
                "unit_contract_version": "cn-equity-v1",
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
                "raw_volume",
                "raw_amount",
                "turnover_rate",
                "data_source_id",
                "quality_status",
                "trade_status",
                "unit_contract_version",
                "updated_at",
            )
        }
        if dialect not in {"sqlite", "postgresql"}:
            return self._portable_upsert(rows)
        insert = sqlite_insert if dialect == "sqlite" else postgresql_insert
        for offset in range(0, len(rows), 500):
            statement = insert(Bar).values(rows[offset:offset + 500])
            statement = statement.on_conflict_do_update(index_elements=conflict_columns, set_=update_columns)
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
