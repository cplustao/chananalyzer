from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.app.core.errors import sanitize_text
from backend.app.db.models import IngestionRun
from backend.app.db.models.common import utcnow
from backend.app.domain.daily_refresh import (
    INITIAL_HISTORY_DAYS,
    OVERLAP_SESSIONS,
    adjustment_base_changed,
    plan_daily_refresh,
)
from backend.app.providers.market_data import (
    CalendarSession,
    HistoricalMarketDataProvider,
    validate_instruments,
    validate_market_bars,
)
from backend.app.repositories.ingestion import IngestionRepository

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]
PROVIDERS_DOWN_ERROR = "all daily market-data providers failed"
MAX_CONSECUTIVE_PROVIDER_OUTAGES = 30
# Each failed stock can wait minutes on socket timeouts, so a streak is also cut off by time.
MAX_PROVIDER_OUTAGE_SECONDS = 600
MIN_OUTAGES_BEFORE_TIME_LIMIT = 3
# Past trading calendars do not change, so a gap-free saved calendar is reused and
# only this many recent days are fetched again (for late holiday announcements).
# Fetching all history every day spent Tushare's trade_cal quota of 5 calls a day.
CALENDAR_REFETCH_DAYS = 30


def _contiguous_from(saved: list[CalendarSession], start: date) -> list[CalendarSession]:
    """The saved calendar days from start onward, up to the first missing day."""
    prefix: list[CalendarSession] = []
    expected = start
    for item in saved:
        if item.trade_date != expected:
            break
        prefix.append(item)
        expected += timedelta(days=1)
    return prefix


def _attempts(provider: HistoricalMarketDataProvider, attribute: str) -> list[dict[str, Any]]:
    values = getattr(provider, attribute, [])
    attempts = [item.as_dict() if hasattr(item, "as_dict") else dict(item) for item in values]
    for item in attempts:
        if item.get("error"):
            item["error"] = sanitize_text(item["error"], limit=500)
    return attempts


def _attempt_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a bounded provider summary for job payloads and stage events.

    Per-symbol attempts remain on ``IngestionRun`` for diagnostics. Duplicating
    thousands of entries into every Job result made polling responses needlessly
    large, so jobs expose only aggregate provider telemetry.
    """

    summary: dict[str, dict[str, Any]] = {}
    for item in items:
        provider = str(item.get("provider") or "unknown")
        status = str(item.get("status") or "unknown")
        bucket = summary.setdefault(
            provider,
            {
                "provider": provider,
                "accepted": 0,
                "rejected": 0,
                "skipped": 0,
                "rows": 0,
                "latency_ms": 0,
                "rate_limited": 0,
            },
        )
        if status in {"accepted", "rejected", "skipped"}:
            bucket[status] += 1
        bucket["rows"] += int(item.get("row_count") or 0)
        bucket["latency_ms"] += int(item.get("latency_ms") or 0)
        error_text = str(item.get("error") or "").lower()
        if "rate limit" in error_text or "频率" in error_text or "too many requests" in error_text:
            bucket["rate_limited"] += 1
    for bucket in summary.values():
        attempts = bucket["accepted"] + bucket["rejected"]
        bucket["success_rate"] = bucket["accepted"] / attempts if attempts else 0.0
        bucket["fallback_or_circuit_count"] = bucket["rejected"] + bucket["skipped"]
    return list(summary.values())


def _digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class IngestionService:
    def __init__(self, session: Session, provider: HistoricalMarketDataProvider):
        self.session = session
        self.provider = provider
        self.repository = IngestionRepository(session)

    def refresh_master_data(self) -> dict[str, Any]:
        source = self.repository.data_source(
            self.provider.key,
            self.provider.name,
            {
                "instruments": [
                    "code",
                    "ts_code",
                    "exchange",
                    "name",
                    "area",
                    "industry",
                    "list_date",
                    "status",
                ],
                "contract_version": "instrument-master-v1",
            },
        )
        run = IngestionRun(
            data_source_id=source.id,
            kind="master_data",
            status="running",
            total=1,
            started_at=utcnow(),
            fetched_at=utcnow(),
            freshness="missing",
            missing_fields=[],
            contract_version="instrument-master-v1",
            input_digest=_digest({"kind": "master_data"}),
        )
        self.session.add(run)
        self.session.commit()
        attempts: list[dict[str, Any]] = []
        try:
            fetch = getattr(self.provider, "fetch_instruments", None)
            if not callable(fetch):
                raise RuntimeError("provider does not support instrument master data")
            items = validate_instruments(fetch())
            attempts = _attempts(self.provider, "last_instrument_attempts")
            selected_key = getattr(self.provider, "last_instrument_provider_key", None) or self.provider.key
            selected_name = next(
                (
                    getattr(item, "name", selected_key)
                    for item in getattr(self.provider, "instrument_providers", [])
                    if item.key == selected_key
                ),
                selected_key,
            )
            optional_fields = ["area", "industry", "list_date", "status"]
            supported_optional = optional_fields if selected_key == "tushare" else []
            missing_optional = [item for item in optional_fields if item not in supported_optional]

            selected_source = self.repository.data_source(
                selected_key,
                selected_name,
                {
                    "instruments": ["code", "ts_code", "exchange", "name"],
                    "optional_instrument_fields": supported_optional,
                    "missing_instrument_fields": missing_optional,
                    "last_contract_check": "accepted",
                    "contract_version": "instrument-master-v1",
                },
            )
            written = self.repository.upsert_instruments(items, source=selected_key)
            self.repository.reconcile_active_instruments(item.code for item in items)
            run.data_source_id = selected_source.id
            run.status = "completed"
            run.total = written
            run.succeeded = written
            run.coverage_rate = 1.0
            run.freshness = "fresh" if not missing_optional else "partial"
            run.missing_fields = missing_optional
            run.data_time = utcnow()
            run.provider_chain = attempts or [
                {"provider": selected_key, "status": "accepted", "row_count": written}
            ]
            run.fallback_errors = [item for item in run.provider_chain if item.get("status") == "rejected"]
            run.finished_at = utcnow()
            self.session.commit()
            return {
                "status": "completed",
                "source": selected_key,
                "rows_written": written,
                "freshness": run.freshness,
            }
        except Exception as exc:
            self.session.rollback()
            restored_run = self.session.get(IngestionRun, run.id)
            if restored_run is None:
                raise RuntimeError("Master-data ingestion run disappeared during rollback") from exc
            run = restored_run
            attempts = attempts or _attempts(self.provider, "last_instrument_attempts")
            saved = self.repository.saved_instrument_count()
            run.status = "partial" if saved else "failed"
            run.total = saved
            run.succeeded = saved
            run.failed = 1
            run.coverage_rate = 1.0 if saved else 0.0
            run.freshness = "stale" if saved else "missing"
            run.missing_fields = ["fresh_instrument_master"]
            run.provider_chain = attempts
            run.fallback_errors = attempts
            run.error = sanitize_text(exc, limit=500)
            run.finished_at = utcnow()
            self.session.commit()
            if not saved:
                raise
            return {
                "status": "partial",
                "source": "saved",
                "rows_written": 0,
                "saved_rows": saved,
                "freshness": "stale",
            }

    def _settled_end_date(self, now: datetime | None = None) -> date:
        local_now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        settled_through = local_now.date()
        if local_now.time() < dt_time(16, 30):
            settled_through -= timedelta(days=1)
        return settled_through

    def refresh_bars(
        self,
        *,
        codes: list[str] | None = None,
        timeframe: str = "DAY",
        adjustment: str = "QFQ",
        start_date: date | None = None,
        end_date: date | None = None,
        lookback_days: int = 10,
        auto_backfill: bool = True,
        progress: ProgressCallback | None = None,
        cancelled: CancelCallback | None = None,
    ) -> dict[str, Any]:
        end = end_date or self._settled_end_date()
        calendar_end = (
            end
            if end_date is not None
            else datetime.now(ZoneInfo("Asia/Shanghai")).date()
        )
        automatic = auto_backfill and timeframe == "DAY" and start_date is None and end_date is None
        start = start_date or end - timedelta(days=max(1, lookback_days))
        if automatic:
            first_stored = self.repository.first_daily_bar_date(codes, adjustment)
            start = min(end - timedelta(days=INITIAL_HISTORY_DAYS), first_stored or end)
        if start > end:
            raise ValueError("行情刷新开始日期不能晚于结束日期")

        source = self.repository.data_source(
            self.provider.key,
            self.provider.name,
            {
                "bars": ["DAY"],
                "adjustments": ["QFQ", "HFQ", "NONE"],
                "calendar": True,
                "contract_version": "market-data-v2",
            },
        )
        targets = self.repository.refresh_targets(codes)
        begin_batch = getattr(self.provider, "begin_batch", None)
        if callable(begin_batch):
            begin_batch(total=len(targets), timeframe=timeframe, adjustment=adjustment)
        run = IngestionRun(
            data_source_id=source.id,
            kind="bars",
            status="running",
            range_start=start,
            range_end=end,
            total=len(targets),
            started_at=utcnow(),
            fetched_at=utcnow(),
            freshness="missing",
            missing_fields=[],
            input_digest=_digest(
                {"codes": codes, "timeframe": timeframe, "adjustment": adjustment,
                 "start": start, "end": end, "auto_backfill": automatic}
            ),
        )
        self.session.add(run)
        self.session.commit()

        errors: list[dict[str, str]] = []
        adjustment_fallbacks: list[str] = []
        rows_written = 0
        calendar_rows = 0
        provider_chain: list[dict[str, Any]] = []
        data_times: list[datetime] = []
        calendar_source = self.provider.key
        requested_ranges = 0
        remaining_gaps = 0
        gap_details: list[dict[str, Any]] = []
        listing_date_unknown_codes: list[str] = []
        rebased_codes: list[str] = []
        skipped = 0
        consecutive_outages = 0
        outage_started_at = 0.0
        stopped_for_outage = False
        try:
            try:
                cursor = start
                reused: list[CalendarSession] = []
                if automatic:
                    known = _contiguous_from(
                        self.repository.saved_calendar(start_date=start, end_date=calendar_end), start
                    )
                    refetch_from = known[-1].trade_date - timedelta(days=CALENDAR_REFETCH_DAYS) if known else start
                    if refetch_from > start:
                        reused = [item for item in known if item.trade_date < refetch_from]
                        cursor = refetch_from
                fetched: list[CalendarSession] = []
                while cursor <= calendar_end:
                    chunk_end = min(calendar_end, cursor + timedelta(days=364)) if automatic else calendar_end
                    fetched.extend(self.provider.fetch_calendar(start_date=cursor, end_date=chunk_end))
                    provider_chain.extend(_attempts(self.provider, "last_calendar_attempts"))
                    cursor = chunk_end + timedelta(days=1)
                calendar_source = (
                    getattr(self.provider, "last_calendar_provider_key", None) or self.provider.key
                )
                calendar = [*reused, *fetched]
                to_store = fetched
            except Exception as exc:
                provider_chain.extend(_attempts(self.provider, "last_calendar_attempts"))
                calendar = self.repository.saved_calendar(start_date=start, end_date=calendar_end)
                if not calendar:
                    raise
                calendar_source = "saved-calendar"
                run.freshness = "stale"
                if automatic:
                    errors.append({"error": "交易日历刷新失败，历史完整性待确认：" + sanitize_text(exc, limit=300)})
                to_store = calendar
            # Reused days are already saved with the source that fetched them.
            calendar_rows = self.repository.upsert_calendar(to_store, source=calendar_source)
            self.session.commit()
            open_dates = sorted({item.trade_date for item in calendar if item.is_open and item.trade_date <= end})
            if automatic and not open_dates:
                raise ValueError("交易日历缺少可验证的开市日期，无法规划自动补全")
            for index, instrument in enumerate(targets, start=1):
                if cancelled and cancelled():
                    run.status = "cancelled"
                    break
                try:
                    if not instrument.ts_code:
                        raise ValueError("股票缺少 ts_code")
                    effective_adjustment = adjustment
                    if str(instrument.exchange or "").upper() == "BJ" and adjustment.upper() != "NONE":
                        effective_adjustment = "NONE"
                        adjustment_fallbacks.append(instrument.code)
                    plan = None
                    ranges: tuple[tuple[date, date], ...] = ((start, end),)
                    if automatic:
                        if instrument.list_date is None:
                            listing_date_unknown_codes.append(instrument.code)
                        first_stored, saved_dates = self.repository.daily_series_dates(
                            instrument.id, effective_adjustment, end,
                        )
                        plan = plan_daily_refresh(
                            open_dates=open_dates, end=end, first_stored=first_stored,
                            saved_dates=saved_dates, list_date=instrument.list_date,
                        )
                        ranges = plan.ranges
                        if not ranges:
                            skipped += 1
                    pending_ranges = list(ranges)
                    rebased = False
                    while pending_ranges:
                        range_start, range_end = pending_ranges.pop(0)
                        if cancelled and cancelled():
                            run.status = "cancelled"
                            break
                        requested_ranges += 1
                        bars = self.provider.fetch_bars(
                            ts_code=instrument.ts_code,
                            timeframe=timeframe,
                            adjustment=effective_adjustment,
                            start_date=range_start,
                            end_date=range_end,
                        )
                        bars = validate_market_bars(bars, start_date=range_start, end_date=range_end)
                        attempts = _attempts(self.provider, "last_bar_attempts")
                        provider_chain.extend({**item, "subject": instrument.code} for item in attempts)
                        selected_key = getattr(self.provider, "last_bar_provider_key", None) or self.provider.key
                        if not attempts:
                            provider_chain.append(
                                {"provider": selected_key, "status": "accepted",
                                 "row_count": len(bars), "subject": instrument.code}
                            )
                        selected_name = next(
                            (getattr(item, "name", selected_key)
                             for item in getattr(self.provider, "bar_providers", []) if item.key == selected_key),
                            selected_key,
                        )
                        selected_source = self.repository.data_source(selected_key, selected_name)
                        if plan and plan.expected_dates and not rebased and effective_adjustment != "NONE":
                            rebased = adjustment_base_changed(
                                self.repository.saved_closes(
                                    instrument.id, effective_adjustment, selected_source.id, range_start, range_end,
                                ),
                                {item.bar_time.date(): item.close for item in bars},
                            )
                            if rebased:
                                # Everything already saved is on the old base; refetch it in one window.
                                rebased_codes.append(instrument.code)
                                pending_ranges = [(min(plan.expected_dates), end)]
                        written = self.repository.upsert_bars(
                            instrument_id=instrument.id, timeframe=timeframe,
                            adjustment=effective_adjustment, data_source_id=selected_source.id, bars=bars,
                        )
                        # Preserve repaired windows even if a later window fails or is cancelled.
                        self.session.commit()
                        rows_written += written
                        data_times.append(max(item.bar_time for item in bars))
                    if run.status == "cancelled":
                        break
                    missing = set()
                    if plan:
                        observed_start, saved_dates = self.repository.daily_series_dates(instrument.id, effective_adjustment, end)
                        expected_dates = set(plan.expected_dates)
                        if instrument.list_date is None and observed_start is not None:
                            expected_dates = {day for day in expected_dates if day >= observed_start}
                        missing = expected_dates - saved_dates
                    if missing:
                        remaining_gaps += len(missing)
                        if len(gap_details) < 50:
                            gap_details.append({
                                "code": instrument.code, "count": len(missing),
                                "first_date": min(missing).isoformat(), "last_date": max(missing).isoformat(),
                                "sample_dates": [day.isoformat() for day in sorted(missing)[:5]],
                            })
                        raise ValueError(
                            f"补采后仍有 {len(missing)} 个交易日缺少可用行情（最早 {min(missing)}）；"
                            "数据源未返回或来源未统一，需核实停牌/缺数，未将其视为已补齐"
                        )
                    run.succeeded += 1
                    consecutive_outages = 0
                    self.session.commit()
                except Exception as exc:
                    self.session.rollback()
                    restored_run = self.session.get(IngestionRun, run.id)
                    if restored_run is None:
                        raise RuntimeError("Ingestion run disappeared during rollback") from exc
                    run = restored_run
                    run.failed += 1
                    errors.append({"code": instrument.code, "error": sanitize_text(exc, limit=500)})
                    self.session.commit()
                    if PROVIDERS_DOWN_ERROR in str(exc):
                        if consecutive_outages == 0:
                            outage_started_at = time.monotonic()
                        consecutive_outages += 1
                    else:
                        consecutive_outages = 0
                    outage_seconds = time.monotonic() - outage_started_at if consecutive_outages else 0.0
                    if consecutive_outages >= MAX_CONSECUTIVE_PROVIDER_OUTAGES or (
                        consecutive_outages >= MIN_OUTAGES_BEFORE_TIME_LIMIT
                        and outage_seconds >= MAX_PROVIDER_OUTAGE_SECONDS
                    ):
                        # Failing the rest one by one took hours. Saved bars stay, so a rerun resumes.
                        stopped_for_outage = True
                        errors.insert(0, {
                            "error": f"连续 {consecutive_outages} 只股票的所有数据源都失败"
                            f"（约 {round(outage_seconds / 60)} 分钟），已提前停止，"
                            f"其余 {len(targets) - index} 只未处理；已写入的数据不会丢失，稍后重新运行会从缺口继续"
                        })
                        break
                if progress:
                    progress(index, len(targets), instrument.code)
            if run.status == "running":
                run.status = "partial" if run.failed or (automatic and calendar_source == "saved-calendar") else "completed"
        except Exception as exc:
            self.session.rollback()
            restored_run = self.session.get(IngestionRun, run.id)
            if restored_run is None:
                raise RuntimeError("Ingestion run disappeared during rollback") from exc
            run = restored_run
            run.status = "failed"
            run.error = sanitize_text(exc, limit=4000)
            run.finished_at = utcnow()
            self.session.commit()
            end_batch = getattr(self.provider, "end_batch", None)
            if callable(end_batch):
                end_batch()
            raise

        run.error = None if not errors else str(errors[:20])
        run.provider_chain = provider_chain
        run.fallback_errors = [item for item in provider_chain if item.get("status") == "rejected"]
        run.data_time = max(data_times) if data_times else None
        run.fetched_at = utcnow()
        run.coverage_rate = run.succeeded / run.total if run.total else 0.0
        run.freshness = (
            "fresh"
            if run.status == "completed" and calendar_source != "saved-calendar"
            else ("stale" if run.succeeded == 0 else "partial")
        )
        run.missing_fields = (
            (["bj_qfq_unavailable_raw_bars_used"] if adjustment_fallbacks else [])
            if run.status == "completed"
            else ["daily_bars"]
        )
        if remaining_gaps:
            run.missing_fields = [*run.missing_fields, "unverified_history_gaps"]
        if automatic and calendar_source == "saved-calendar":
            run.missing_fields = [*run.missing_fields, "fresh_trading_calendar"]
        run.finished_at = utcnow()
        self.session.commit()
        end_batch = getattr(self.provider, "end_batch", None)
        if callable(end_batch):
            end_batch()
        return {
            "ingestion_run_id": run.id,
            "status": run.status,
            "source": sorted(
                {
                    item.get("provider", self.provider.key)
                    for item in provider_chain
                    if item.get("status") == "accepted"
                }
            ),
            "range_start": start.isoformat(),
            "range_end": end.isoformat(),
            "refresh_mode": "auto" if automatic else "range",
            "initial_history_days": INITIAL_HISTORY_DAYS if automatic else None,
            "overlap_sessions": OVERLAP_SESSIONS if automatic else None,
            "requested_ranges": requested_ranges,
            "skipped_not_listed": skipped,
            "history_complete": (
                run.status == "completed" and calendar_source != "saved-calendar"
            ) if automatic else None,
            "remaining_gap_count": remaining_gaps,
            "gap_details": gap_details,
            "listing_date_unknown_codes": listing_date_unknown_codes,
            "rebased_codes": rebased_codes,
            "total": run.total,
            "succeeded": run.succeeded,
            "failed": run.failed,
            "stopped_for_outage": stopped_for_outage,
            "bar_rows_written": rows_written,
            "calendar_rows_written": calendar_rows,
            "failed_codes": [str(item["code"]) for item in errors if item.get("code")],
            "adjustment_fallback_codes": adjustment_fallbacks,
            "errors": errors[:100],
            "freshness": run.freshness,
            "coverage_rate": run.coverage_rate,
            "provider_chain": _attempt_summary(provider_chain),
            "data_time": run.data_time.isoformat() if run.data_time else None,
        }
