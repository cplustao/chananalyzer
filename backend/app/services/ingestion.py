from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from backend.app.core.errors import sanitize_text
from backend.app.db.models import IngestionRun
from backend.app.db.models.common import utcnow
from backend.app.providers.market_data import (
    HistoricalMarketDataProvider,
    validate_instruments,
    validate_market_bars,
)
from backend.app.repositories.ingestion import IngestionRepository

ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def _attempts(provider: HistoricalMarketDataProvider, attribute: str) -> list[dict[str, Any]]:
    values = getattr(provider, attribute, [])
    attempts = [item.as_dict() if hasattr(item, "as_dict") else dict(item) for item in values]
    for item in attempts:
        if item.get("error"):
            item["error"] = sanitize_text(item["error"], limit=500)
    return attempts


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
        progress: ProgressCallback | None = None,
        cancelled: CancelCallback | None = None,
    ) -> dict[str, Any]:
        end = end_date or self._settled_end_date()
        calendar_end = (
            end
            if end_date is not None
            else datetime.now(ZoneInfo("Asia/Shanghai")).date()
        )
        start = start_date or end - timedelta(days=max(1, lookback_days))
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
                {"codes": codes, "timeframe": timeframe, "adjustment": adjustment, "start": start, "end": end}
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
        try:
            try:
                calendar = self.provider.fetch_calendar(start_date=start, end_date=calendar_end)
                provider_chain.extend(_attempts(self.provider, "last_calendar_attempts"))
                calendar_source = (
                    getattr(self.provider, "last_calendar_provider_key", None) or self.provider.key
                )
            except Exception:
                provider_chain.extend(_attempts(self.provider, "last_calendar_attempts"))
                calendar = self.repository.saved_calendar(start_date=start, end_date=calendar_end)
                if not calendar:
                    raise
                calendar_source = "saved-calendar"
                run.freshness = "stale"
            calendar_rows = self.repository.upsert_calendar(calendar, source=calendar_source)
            self.session.commit()
            for index, instrument in enumerate(targets, start=1):
                if cancelled and cancelled():
                    run.status = "cancelled"
                    break
                try:
                    if not instrument.ts_code:
                        raise ValueError("股票缺少 ts_code")
                    effective_adjustment = adjustment
                    if instrument.exchange.upper() == "BJ" and adjustment.upper() != "NONE":
                        effective_adjustment = "NONE"
                        adjustment_fallbacks.append(instrument.code)
                    bars = self.provider.fetch_bars(
                        ts_code=instrument.ts_code,
                        timeframe=timeframe,
                        adjustment=effective_adjustment,
                        start_date=start,
                        end_date=end,
                    )
                    bars = validate_market_bars(bars, start_date=start, end_date=end)
                    attempts = _attempts(self.provider, "last_bar_attempts")
                    provider_chain.extend({**item, "subject": instrument.code} for item in attempts)
                    selected_key = getattr(self.provider, "last_bar_provider_key", None) or self.provider.key
                    if not attempts:
                        provider_chain.append(
                            {
                                "provider": selected_key,
                                "status": "accepted",
                                "row_count": len(bars),
                                "subject": instrument.code,
                            }
                        )
                    selected_name = next(
                        (
                            getattr(item, "name", selected_key)
                            for item in getattr(self.provider, "bar_providers", [])
                            if item.key == selected_key
                        ),
                        selected_key,
                    )
                    selected_source = self.repository.data_source(selected_key, selected_name)
                    rows_written += self.repository.upsert_bars(
                        instrument_id=instrument.id,
                        timeframe=timeframe,
                        adjustment=effective_adjustment,
                        data_source_id=selected_source.id,
                        bars=bars,
                    )
                    run.succeeded += 1
                    self.session.commit()
                    data_times.append(max(item.bar_time for item in bars))
                except Exception as exc:
                    self.session.rollback()
                    restored_run = self.session.get(IngestionRun, run.id)
                    if restored_run is None:
                        raise RuntimeError("Ingestion run disappeared during rollback") from exc
                    run = restored_run
                    run.failed += 1
                    errors.append({"code": instrument.code, "error": sanitize_text(exc, limit=500)})
                    self.session.commit()
                if progress:
                    progress(index, len(targets), instrument.code)
            if run.status == "running":
                run.status = "partial" if run.failed else "completed"
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
            "total": run.total,
            "succeeded": run.succeeded,
            "failed": run.failed,
            "bar_rows_written": rows_written,
            "calendar_rows_written": calendar_rows,
            "failed_codes": [str(item["code"]) for item in errors if item.get("code")],
            "adjustment_fallback_codes": adjustment_fallbacks,
            "errors": errors[:100],
            "freshness": run.freshness,
            "coverage_rate": run.coverage_rate,
            "provider_chain": provider_chain,
            "data_time": run.data_time.isoformat() if run.data_time else None,
        }
