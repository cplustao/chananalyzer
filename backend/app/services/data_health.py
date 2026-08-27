from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import (
    Bar,
    DataHealthSnapshot,
    DataReliabilitySample,
    IngestionRun,
    Instrument,
    IpoEvent,
    Job,
    LimitUpEvent,
    RadarSnapshot,
    TradingCalendar,
)
from backend.app.db.models.common import utcnow
from backend.app.services.secrets import SecretService

_STATE_RANK = {"fresh": 0, "partial": 1, "stale": 2, "missing": 3}


def expected_trade_date(session: Session, now: datetime | None = None) -> date | None:
    """Return the latest trading day whose daily data should already be settled."""
    local_now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    settled_through = local_now.date()
    if local_now.time() < time(16, 30):
        settled_through -= timedelta(days=1)
    return session.scalar(
        select(func.max(TradingCalendar.trade_date)).where(
            TradingCalendar.is_open.is_(True),
            TradingCalendar.trade_date <= settled_through,
        )
    )


class DataHealthService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def snapshot(self) -> dict[str, Any]:
        latest_open = self.session.scalar(
            select(func.max(TradingCalendar.trade_date)).where(TradingCalendar.is_open.is_(True))
        )
        expected_trade_date = self._expected_trade_date()
        calendar_coverage_end = self.session.scalar(select(func.max(TradingCalendar.trade_date)))
        categories = [
            self._master_data(),
            self._daily_bars(expected_trade_date),
            self._calendar(latest_open, calendar_coverage_end),
            self._limit_up(expected_trade_date),
            self._ipo(expected_trade_date),
            self._radar(expected_trade_date),
            self._ai(),
        ]
        overall = max(
            (item["status"] for item in categories if item["affects_overall"]),
            key=lambda value: _STATE_RANK[value],
        )
        backend = self.session.get_bind().dialect.name
        blocking_categories = [
            item for item in categories
            if item["affects_overall"] and item["status"] != "fresh"
        ]
        unit_anomalies = int(
            self.session.scalar(
                select(func.count()).select_from(Bar).where(
                    or_(
                        Bar.unit_contract_version != "cn-equity-v1",
                        Bar.quality_status == "quarantined",
                    )
                )
            )
            or 0
        )
        if unit_anomalies:
            overall = "stale"
        eligible_count = next(
            (item.get("eligible_count") for item in categories if item["key"] == "daily_bars"),
            None,
        )
        latest_full_run = self.session.scalar(
            select(IngestionRun)
            .where(
                IngestionRun.kind == "bars",
                IngestionRun.status == "completed",
                IngestionRun.coverage_rate >= self.settings.radar_min_current_coverage,
                IngestionRun.total >= max(1, int((eligible_count or 0) * 0.95)),
            )
            .order_by(IngestionRun.finished_at.desc())
        )
        provider_summary = self._provider_summary(latest_full_run)
        blocking_reasons = [item["key"] for item in blocking_categories]
        if unit_anomalies:
            blocking_reasons.append("unit_contract")
        decision_usable = not blocking_reasons
        recommendation = next(
            (item.get("recommendation") for item in blocking_categories if item.get("recommendation")),
            None,
        )
        if unit_anomalies:
            recommendation = "存在单位未知或被隔离的行情，请重新拉取对应序列后再生成决策结论。"
        return {
            "status": overall,
            "checked_at": utcnow().isoformat(),
            "expected_trade_date": expected_trade_date.isoformat() if expected_trade_date else None,
            "decision_usable": decision_usable,
            "as_of_trade_date": expected_trade_date.isoformat() if decision_usable and expected_trade_date else None,
            "last_full_refresh": latest_full_run.finished_at.isoformat() if latest_full_run and latest_full_run.finished_at else None,
            "blocking_reasons": blocking_reasons,
            "provider_summary": provider_summary,
            "recommended_action": recommendation,
            "database_backend": backend,
            "backup_mode": "application" if backend == "sqlite" else "external_required",
            "categories": categories,
        }

    @staticmethod
    def _provider_summary(run: IngestionRun | None) -> dict[str, Any]:
        if run is None:
            return {}
        summary: dict[str, dict[str, int | float]] = {}
        for attempt in run.provider_chain or []:
            provider = str(attempt.get("provider") or "unknown")
            status = str(attempt.get("status") or "unknown")
            bucket = summary.setdefault(provider, {"accepted": 0, "rejected": 0, "skipped": 0, "rows": 0, "latency_ms": 0, "rate_limited": 0})
            if status in bucket:
                bucket[status] += 1
            bucket["rows"] += int(attempt.get("row_count") or 0)
            bucket["latency_ms"] += int(attempt.get("latency_ms") or 0)
            error_text = str(attempt.get("error") or "").lower()
            if "rate limit" in error_text or "频率" in error_text or "too many requests" in error_text:
                bucket["rate_limited"] += 1
        for bucket in summary.values():
            attempts = bucket["accepted"] + bucket["rejected"]
            bucket["success_rate"] = bucket["accepted"] / attempts if attempts else 0.0
            bucket["fallback_or_circuit_count"] = bucket["rejected"] + bucket["skipped"]
        return {"run_id": run.id, "providers": summary, "coverage_rate": run.coverage_rate}

    def cached_snapshot(self, max_age_seconds: int = 300) -> dict[str, Any]:
        cached = self.session.get(DataHealthSnapshot, "current")
        if cached and cached.generated_at >= utcnow() - timedelta(seconds=max_age_seconds):
            return dict(cached.payload or {})
        return self.refresh_cache(record_sample=False)

    def refresh_cache(self, *, record_sample: bool = False) -> dict[str, Any]:
        payload = self.snapshot()
        cached = self.session.get(DataHealthSnapshot, "current")
        if cached is None:
            cached = DataHealthSnapshot(key="current")
            self.session.add(cached)
        cached.payload = payload
        cached.generated_at = utcnow()
        if record_sample:
            daily: dict[str, Any] = next(
                (item for item in payload["categories"] if item["key"] == "daily_bars"), {}
            )
            self.session.add(
                DataReliabilitySample(
                    trade_date=date.fromisoformat(payload["expected_trade_date"])
                    if payload.get("expected_trade_date")
                    else None,
                    decision_usable=bool(payload["decision_usable"]),
                    coverage_rate=daily.get("coverage_rate"),
                    status=str(payload["status"]),
                    provider_summary=payload.get("provider_summary"),
                    blocking_reasons=payload.get("blocking_reasons"),
                )
            )
        self.session.commit()
        return payload

    def _expected_trade_date(self, now: datetime | None = None) -> date | None:
        return expected_trade_date(self.session, now)

    def _failure(self, kind: str) -> dict[str, Any] | None:
        job = self.session.scalar(
            select(Job)
            .where(Job.kind == kind, Job.status.in_(("partial", "failed")), Job.archived_at.is_(None))
            .order_by(Job.created_at.desc())
        )
        if not job:
            return None
        return {
            "job_id": job.id,
            "status": job.status,
            "error": job.error,
            "created_at": job.created_at.isoformat(),
        }

    def _item(
        self,
        key: str,
        status: str,
        *,
        source: str | None = None,
        data_time: date | datetime | None = None,
        coverage_rate: float | None = None,
        missing_fields: list[str] | None = None,
        single_source: bool = False,
        failure_kind: str | None = None,
        recommendation: str | None = None,
        affects_overall: bool = True,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "status": status,
            "source": source,
            "data_time": data_time.isoformat() if data_time else None,
            "coverage_rate": coverage_rate,
            "missing_fields": missing_fields or [],
            "single_source": single_source,
            "last_failure": self._failure(failure_kind) if failure_kind else None,
            "recommendation": recommendation,
            "affects_overall": affects_overall,
        }

    def _master_data(self) -> dict[str, Any]:
        count, updated = self.session.execute(
            select(func.count(Instrument.id), func.max(Instrument.updated_at)).where(
                func.length(Instrument.code) == 6
            )
        ).one()
        run = self.session.scalar(
            select(IngestionRun)
            .where(IngestionRun.kind == "master_data")
            .order_by(IngestionRun.created_at.desc())
        )
        status = (
            "missing"
            if not count
            else run.freshness
            if run and run.freshness in {"fresh", "partial", "stale"}
            else "stale"
        )
        accepted = (
            [
                str(item.get("provider"))
                for item in (run.provider_chain or [])
                if item.get("status") == "accepted"
            ]
            if run
            else []
        )
        return self._item(
            "master_data",
            status,
            source=", ".join(accepted) or ("saved" if count else None),
            data_time=run.data_time if run and run.data_time else updated,
            coverage_rate=1.0 if count else 0.0,
            missing_fields=(run.missing_fields or []) if run else ["fresh_instrument_master"],
            failure_kind="data.refresh",
            recommendation=(
                "在市股票清单完整；地区、行业、上市日期和状态等可选字段受数据源配额限制，"
                "不阻塞行情、涨停和市场雷达。"
                if status == "partial" and count
                else "刷新股票主数据；远端失败时保留已保存主数据并标记 stale。"
                if status != "fresh"
                else None
            ),
        )

    def _daily_bars(self, expected: date | None) -> dict[str, Any]:
        eligibility = [
            Instrument.status == "active",
            func.length(Instrument.code) == 6,
        ]
        if expected:
            eligibility.append(or_(Instrument.list_date.is_(None), Instrument.list_date <= expected))
        eligible_count = int(
            self.session.scalar(select(func.count(Instrument.id)).where(*eligibility)) or 0
        )
        canonical_adjustment = or_(
            (Instrument.exchange == "BJ") & (Bar.adjustment == "NONE"),
            or_(Instrument.exchange.is_(None), Instrument.exchange != "BJ")
            & (Bar.adjustment == "QFQ"),
        )
        canonical_series = (
            select(Bar.id)
            .where(
                Bar.instrument_id == Instrument.id,
                Bar.timeframe == "DAY",
                Bar.quality_status == "ok",
                canonical_adjustment,
            )
            .limit(1)
            .exists()
        )
        series_count = int(
            self.session.scalar(
                select(func.count(Instrument.id)).where(*eligibility, canonical_series)
            )
            or 0
        )
        if expected:
            day_start = datetime.combine(expected, time.min)
            day_end = day_start + timedelta(days=1)
            current_series = (
                select(Bar.id)
                .where(
                    Bar.instrument_id == Instrument.id,
                    Bar.timeframe == "DAY",
                    Bar.quality_status == "ok",
                    canonical_adjustment,
                    Bar.bar_time >= day_start,
                    Bar.bar_time < day_end,
                )
                .limit(1)
                .exists()
            )
            current_count = int(
                self.session.scalar(
                    select(func.count(Instrument.id)).where(*eligibility, current_series)
                )
                or 0
            )
        else:
            current_count = series_count
        # A current canonical series proves the maximum date without repeatedly
        # aggregating the complete multi-million-row bar table. Only stale or empty
        # datasets need the slower fallback query.
        latest = datetime.combine(expected, time.min) if expected and current_count else None
        if latest is None:
            latest = self.session.scalar(
                select(func.max(Bar.bar_time))
                .join(Instrument, Instrument.id == Bar.instrument_id)
                .where(
                    *eligibility,
                    Bar.timeframe == "DAY",
                    Bar.quality_status == "ok",
                    canonical_adjustment,
                )
            )
        missing_count = max(0, eligible_count - series_count)
        lagging_count = max(0, series_count - current_count)
        coverage = current_count / eligible_count if eligible_count else 0.0
        if eligible_count == 0 or latest is None:
            status = "missing"
        elif expected and latest.date() < expected:
            status = "stale"
        elif coverage >= self.settings.radar_min_current_coverage:
            status = "fresh"
        elif coverage >= 0.95:
            status = "partial"
        else:
            status = "stale"
        run = self.session.scalar(
            select(IngestionRun).where(IngestionRun.kind == "bars").order_by(IngestionRun.created_at.desc())
        )
        source = None
        if run and run.provider_chain:
            source = ", ".join(
                sorted(
                    {
                        str(item.get("provider"))
                        for item in run.provider_chain
                        if item.get("status") == "accepted"
                    }
                )
            )
        recommendation = None
        if status != "fresh":
            recommendation = (
                f"目标交易日覆盖 {current_count}/{eligible_count}；"
                f"落后 {lagging_count}、缺失 {missing_count}。请补刷失败标的后再生成可执行结论。"
            )
        return {
            **self._item(
                "daily_bars",
                status,
                source=source or "saved",
                data_time=latest,
                coverage_rate=coverage,
                missing_fields=[] if status == "fresh" else ["current_daily_bars"],
                failure_kind="data.refresh",
                recommendation=recommendation,
            ),
            "eligible_count": eligible_count,
            "current_count": current_count,
            "lagging_count": lagging_count,
            "missing_count": missing_count,
            "fresh_threshold": self.settings.radar_min_current_coverage,
        }

    def _calendar(self, latest_open: date | None, coverage_end: date | None) -> dict[str, Any]:
        if latest_open is None or coverage_end is None:
            status = "missing"
        elif coverage_end < utcnow().date():
            status = "stale"
        else:
            status = "fresh"
        run = self.session.scalar(
            select(IngestionRun).where(IngestionRun.kind == "bars").order_by(IngestionRun.created_at.desc())
        )
        accepted = (
            [
                str(item.get("provider"))
                for item in (run.provider_chain or [])
                if item.get("status") == "accepted" and not item.get("subject")
            ]
            if run
            else []
        )
        return self._item(
            "trading_calendar",
            status,
            source=", ".join(dict.fromkeys(accepted)) or "saved",
            data_time=coverage_end,
            coverage_rate=1.0 if status == "fresh" else 0.0,
            missing_fields=[] if status == "fresh" else ["current_trading_calendar"],
            failure_kind="data.refresh",
            recommendation="刷新交易日历覆盖范围；禁止用自然日推测交易日。" if status != "fresh" else None,
        )

    def _limit_up(self, expected: date | None) -> dict[str, Any]:
        latest = self.session.scalar(select(func.max(LimitUpEvent.trade_date)))
        status = "missing" if not latest else "stale" if expected and latest < expected else "fresh"
        return self._item(
            "limit_up",
            status,
            source="akshare.eastmoney_limit_up_pool",
            data_time=latest,
            single_source=True,
            missing_fields=[] if status == "fresh" else ["current_limit_up"],
            failure_kind="limit_up.refresh",
            recommendation="刷新涨停原始事件后重新计算市场雷达。" if status != "fresh" else None,
        )

    def _ipo(self, expected: date | None) -> dict[str, Any]:
        latest = self.session.scalar(select(IpoEvent).order_by(IpoEvent.fetched_at.desc()))
        if latest is None:
            status = "missing"
        elif expected and latest.fetched_at.date() < expected:
            status = "stale"
        else:
            status = "fresh"
        source = (latest.raw_payload or {}).get("source") if latest else None
        return self._item(
            "ipo",
            status,
            source=str(source or "saved") if latest else None,
            data_time=latest.fetched_at if latest else None,
            single_source=False,
            missing_fields=[] if status == "fresh" else ["current_ipo_events"],
            failure_kind="ipo.refresh",
            recommendation=(
                "刷新新股原始数据；该模块不影响行情和市场雷达的总体可用状态。"
                if status != "fresh"
                else None
            ),
            affects_overall=False,
        )

    def _radar(self, expected: date | None) -> dict[str, Any]:
        row = self.session.scalar(
            select(RadarSnapshot).order_by(
                RadarSnapshot.trade_date.desc(), RadarSnapshot.calculated_at.desc()
            )
        )
        if not row:
            return self._item(
                "radar", "missing", missing_fields=["radar_snapshot"], failure_kind="market_radar.refresh"
            )
        status = row.freshness
        if expected and row.trade_date < expected:
            status = "stale"
        return self._item(
            "radar",
            status,
            source=row.source_summary,
            data_time=row.data_time or row.calculated_at,
            coverage_rate=row.coverage_rate,
            missing_fields=row.missing_fields,
            failure_kind="market_radar.refresh",
        )

    def _ai(self) -> dict[str, Any]:
        statuses = {
            str(item["key"]): bool(item["configured"])
            for item in SecretService(self.session, self.settings).status()
        }
        usable: list[str] = []
        missing_fields: list[str] = []
        if statuses.get("DEEPSEEK_API_KEY"):
            if self.settings.deepseek_model:
                usable.append("deepseek")
            else:
                missing_fields.append("deepseek_model")
        if statuses.get("SILICONFLOW_API_KEY"):
            if self.settings.siliconflow_model:
                usable.append("siliconflow")
            else:
                missing_fields.append("siliconflow_model")
        if not usable and not missing_fields:
            missing_fields.append("ai_provider")
        return self._item(
            "ai",
            "fresh" if usable else "missing",
            source=", ".join(usable) or None,
            coverage_rate=1.0 if usable else 0.0,
            missing_fields=missing_fields,
            recommendation="AI 密钥与模型均完整时才生成报告；未配置时保留确定性分析。",
            affects_overall=False,
        )
