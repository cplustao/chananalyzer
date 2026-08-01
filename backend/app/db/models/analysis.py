from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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


class LimitUpEvent(Base):
    __tablename__ = "limit_up_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL"), index=True
    )
    legacy_code: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    market: Mapped[str | None] = mapped_column(String(32))
    theme: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str | None] = mapped_column(Text)
    close: Mapped[float | None] = mapped_column(Float)
    pct_change: Mapped[float | None] = mapped_column(Float)
    turnover_rate: Mapped[float | None] = mapped_column(Float)
    consecutive_boards: Mapped[int] = mapped_column(Integer, default=1)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    quarantined: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    __table_args__ = (UniqueConstraint("trade_date", "legacy_code", name="uq_limit_up_date_code"),)


class IpoEvent(Base):
    __tablename__ = "ipo_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL"), index=True
    )
    legacy_code: Mapped[str] = mapped_column(String(16), index=True)
    listing_date: Mapped[date] = mapped_column(Date, index=True)
    issue_price: Mapped[float | None] = mapped_column(Float)
    issue_pe: Mapped[float | None] = mapped_column(Float)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("listing_date", "legacy_code", name="uq_ipo_date_code"),)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL"), index=True
    )
    subject_date: Mapped[date | None] = mapped_column(Date, index=True)
    job_item_id: Mapped[str | None] = mapped_column(ForeignKey("job_items.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    algorithm_version: Mapped[str] = mapped_column(String(32), default="1.0")
    config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reports: Mapped[list["AnalysisReport"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (
        Index("ix_analysis_kind_date_status", "kind", "subject_date", "status"),
        UniqueConstraint("job_item_id", name="uq_analysis_run_job_item"),
    )


class AnalysisReport(Base):
    __tablename__ = "analysis_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text, default="")
    token_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="completed")
    structured_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_digest: Mapped[str | None] = mapped_column(String(64))
    validation_status: Mapped[str] = mapped_column(String(24), default="legacy")
    validation_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LimitUpMetric(Base):
    __tablename__ = "limit_up_metrics"
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True
    )
    limit_up_event_id: Mapped[int] = mapped_column(
        ForeignKey("limit_up_events.id", ondelete="CASCADE"), index=True
    )
    day_score: Mapped[float | None] = mapped_column(Float)
    week_score: Mapped[float | None] = mapped_column(Float)
    value_score: Mapped[float | None] = mapped_column(Float)
    market_score: Mapped[float | None] = mapped_column(Float)
    timing_score: Mapped[float | None] = mapped_column(Float)
    risk_score: Mapped[float | None] = mapped_column(Float)
    overall_score: Mapped[float | None] = mapped_column(Float, index=True)
    day_classification: Mapped[str | None] = mapped_column(String(32))
    week_classification: Mapped[str | None] = mapped_column(String(32))
    completeness: Mapped[float | None] = mapped_column(Float)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    risk_plan: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class RadarSnapshot(Base):
    __tablename__ = "radar_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    analysis_run_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_runs.id", ondelete="SET NULL"))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True)
    status_label: Mapped[str] = mapped_column(String(32))
    executable_position: Mapped[float | None] = mapped_column(Float)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_summary: Mapped[str | None] = mapped_column(String(128))
    data_time: Mapped[datetime | None] = mapped_column(DateTime)
    freshness: Mapped[str] = mapped_column(String(16), default="fresh")
    coverage_rate: Mapped[float | None] = mapped_column(Float)
    missing_fields: Mapped[list[str] | None] = mapped_column(JSON)
    input_digest: Mapped[str | None] = mapped_column(String(64))
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (
        UniqueConstraint("trade_date", "algorithm_version", name="uq_radar_date_version"),
        Index("ix_radar_date_version", "trade_date", "algorithm_version"),
    )


class ScanResult(Base):
    __tablename__ = "scan_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL"), index=True
    )
    scan_kind: Mapped[str] = mapped_column(String(32), index=True)
    signal_type: Mapped[str | None] = mapped_column(String(32), index=True)
    signal_date: Mapped[date | None] = mapped_column(Date, index=True)
    rank: Mapped[int | None] = mapped_column(Integer)
    score: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class AutomationSchedule(Base):
    __tablename__ = "automation_schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    job_kind: Mapped[str] = mapped_column(String(64))
    cron_expression: Mapped[str] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
