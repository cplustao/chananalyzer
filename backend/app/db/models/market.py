from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.models.common import utcnow, uuid_str


class Industry(Base):
    __tablename__ = "industries"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    instruments: Mapped[list["Instrument"]] = relationship(back_populates="industry")


class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    ts_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    exchange: Mapped[str | None] = mapped_column(String(16), index=True)
    name: Mapped[str | None] = mapped_column(String(128), index=True)
    area: Mapped[str | None] = mapped_column(String(64), index=True)
    industry_id: Mapped[int | None] = mapped_column(
        ForeignKey("industries.id", ondelete="SET NULL"), index=True
    )
    list_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    asset_type: Mapped[str] = mapped_column(String(16), default="stock", index=True)
    source_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    industry: Mapped[Industry | None] = relationship(back_populates="instruments")
    bars: Mapped[list["Bar"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Bar(Base):
    __tablename__ = "bars"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[str] = mapped_column(String(12), default="DAY")
    adjustment: Mapped[str] = mapped_column(String(12), default="QFQ")
    bar_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    amount: Mapped[float | None] = mapped_column(Float)
    raw_volume: Mapped[float | None] = mapped_column(Float)
    raw_amount: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    quality_status: Mapped[str] = mapped_column(String(24), default="ok")
    trade_status: Mapped[str] = mapped_column(String(24), default="trading", index=True)
    unit_contract_version: Mapped[str] = mapped_column(String(32), default="cn-equity-v1", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    instrument: Mapped[Instrument] = relationship(back_populates="bars")
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "adjustment", "bar_time", name="uq_bars_series_time"),
        Index("ix_bars_series_time", "instrument_id", "timeframe", "adjustment", "bar_time"),
        CheckConstraint("high >= low", name="ck_bars_high_low"),
    )


class TradingCalendar(Base):
    __tablename__ = "trading_calendar"
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16), primary_key=True, default="CN")
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str | None] = mapped_column(String(64))


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    data_source_id: Mapped[int | None] = mapped_column(ForeignKey("data_sources.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    range_start: Mapped[date | None] = mapped_column(Date)
    range_end: Mapped[date | None] = mapped_column(Date)
    total: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    provider_chain: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    data_time: Mapped[datetime | None] = mapped_column(DateTime)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
    freshness: Mapped[str] = mapped_column(String(16), default="missing")
    coverage_rate: Mapped[float | None] = mapped_column(Float)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSON)
    fallback_errors: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    contract_version: Mapped[str] = mapped_column(String(32), default="market-data-v2")
    input_digest: Mapped[str | None] = mapped_column(String(64))


class DataHealthSnapshot(Base):
    __tablename__ = "data_health_snapshots"
    key: Mapped[str] = mapped_column(String(64), primary_key=True, default="current")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class DataReliabilitySample(Base):
    __tablename__ = "data_reliability_samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date | None] = mapped_column(Date, index=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    decision_usable: Mapped[bool] = mapped_column(Boolean, default=False)
    coverage_rate: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24), index=True)
    provider_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    blocking_reasons: Mapped[list[str] | None] = mapped_column(JSON)
