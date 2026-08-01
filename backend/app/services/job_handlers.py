from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.chan_core import ChanEngine, ScanBatchOutcome, ScanSubjectOutcome
from backend.app.core.config import get_settings
from backend.app.core.errors import sanitize_text
from backend.app.db.models import (
    AnalysisReport,
    AnalysisRun,
    Instrument,
    Job,
    JobItem,
    LimitUpEvent,
    LimitUpMetric,
    ScanResult,
    TradingCalendar,
)
from backend.app.providers.ai import AIProvider, AIProviderChain, OpenAICompatibleProvider
from backend.app.providers.market_data import HistoricalMarketDataProvider, build_historical_market_provider
from backend.app.repositories.jobs import JobRepository
from backend.app.services.chan_analysis import DatabaseChanEngine
from backend.app.services.event_ingestion import EventIngestionService
from backend.app.services.ingestion import IngestionService
from backend.app.services.ipo_analysis import IpoAnalysisService
from backend.app.services.ipo_analysis import report_metadata as ipo_report_metadata
from backend.app.services.limit_up_analysis import (
    LimitUpAnalysisService,
)
from backend.app.services.limit_up_analysis import (
    report_metadata as limit_up_report_metadata,
)
from backend.app.services.market_radar import MarketRadarService
from backend.app.services.market_screening import HotStockService
from backend.app.services.secrets import SecretService
from backend.app.services.structured_ai import GeneratedReport

logger = logging.getLogger(__name__)


class JobHandlers:
    def __init__(
        self,
        session: Session,
        chan_engine: ChanEngine | None = None,
        ingestion_provider: HistoricalMarketDataProvider | None = None,
        ai_provider: AIProvider | None = None,
        worker_instance_id: str | None = None,
    ):
        self.session = session
        self.jobs = JobRepository(session, worker_instance_id)
        self.chan_engine = chan_engine or DatabaseChanEngine(session)
        settings = get_settings()
        secrets = SecretService(session, settings)
        token = secrets.get("TUSHARE_TOKEN")
        self.tushare_token = token
        self.market_provider = HotStockService(session, tushare_token=token)
        self.ingestion_provider = ingestion_provider or build_historical_market_provider(token=token)
        providers: list[OpenAICompatibleProvider] = []
        for provider_key in settings.ai_provider_order:
            if provider_key == "deepseek":
                key = secrets.get("DEEPSEEK_API_KEY")
                if key and settings.deepseek_model:
                    providers.append(
                        OpenAICompatibleProvider(
                            key,
                            key="deepseek",
                            model=settings.deepseek_model,
                            base_url="https://api.deepseek.com",
                        )
                    )
            elif provider_key == "siliconflow":
                key = secrets.get("SILICONFLOW_API_KEY")
                if key and settings.siliconflow_model:
                    providers.append(
                        OpenAICompatibleProvider(
                            key,
                            key="siliconflow",
                            model=settings.siliconflow_model,
                            base_url="https://api.siliconflow.cn/v1",
                        )
                    )
        self.ai_provider = ai_provider or (AIProviderChain(providers) if providers else None)

    def execute(self, job: Job) -> dict[str, Any]:
        if job.cancel_requested:
            self.jobs.mark_cancelled(job)
            return {"cancelled": True}
        handlers = {
            "market_radar.refresh": self._market_radar,
            "limit_up.refresh": self._limit_up_refresh,
            "ipo.refresh": self._ipo_refresh,
            "scan.buy": self._scan,
            "scan.sell": self._scan,
            "screen.hot": self._hot_screen,
            "screen.smart": self._scan,
            "limit_up.analyze": self._limit_up_analysis,
            "ipo.analyze": self._ipo_analysis,
            "stock.analyze": self._stock_analysis,
            "data.refresh": self._data_refresh,
        }
        handler = handlers.get(job.kind)
        if handler is None:
            raise ValueError(f"Unsupported job kind: {job.kind}")
        started = time.monotonic()
        self.jobs.append_stage_event(job, "stage_started", job.kind, processed=0, coverage_rate=0.0)
        try:
            result = handler(job)
        except Exception as exc:
            self.jobs.append_stage_event(
                job,
                "stage_failed",
                job.kind,
                processed=job.completed,
                coverage_rate=job.progress / 100.0,
                duration_ms=round((time.monotonic() - started) * 1000),
                error=str(exc),
            )
            raise
        self.jobs.append_stage_event(
            job,
            "stage_completed",
            job.kind,
            processed=result.get("count", result.get("succeeded", job.completed)),
            coverage_rate=result.get("coverage_rate", 1.0 if job.status != "partial" else 0.0),
            provider_chain=result.get("provider_chain", []),
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return result

    def _limit_up_refresh(self, job: Job) -> dict[str, Any]:
        payload = job.payload or {}
        raw_dates = payload.get("dates") or []
        if raw_dates:
            dates = sorted({date.fromisoformat(str(value)) for value in raw_dates})
        else:
            raw_start = payload.get("start_date")
            raw_end = payload.get("end_date")
            end_date = date.fromisoformat(str(raw_end)) if raw_end else date.today()
            start_date = date.fromisoformat(str(raw_start)) if raw_start else end_date
            dates = list(
                self.session.scalars(
                    select(TradingCalendar.trade_date)
                    .where(
                        TradingCalendar.is_open.is_(True),
                        TradingCalendar.trade_date >= start_date,
                        TradingCalendar.trade_date <= end_date,
                    )
                    .order_by(TradingCalendar.trade_date)
                ).all()
            )
        if not dates:
            raise ValueError("所选范围内没有已确认的开市日")
        self.jobs.heartbeat(job, f"正在刷新涨停原始事件（{len(dates)} 个交易日）", 5)
        result = EventIngestionService(self.session).refresh_limit_ups(dates)
        job.total = len(dates)
        job.completed = int(result["succeeded"])
        job.failed = int(result["failed"])
        if result["status"] == "failed":
            job.result = result
            self.session.commit()
            errors = result.get("errors") or []
            message = errors[0].get("error") if errors else "涨停事件刷新失败"
            raise RuntimeError(str(message))
        if result["status"] == "partial":
            self.jobs.complete(job, result=result, partial=True)
        self.jobs.heartbeat(job, "涨停原始事件已刷新", 95)
        return result

    def _ipo_refresh(self, job: Job) -> dict[str, Any]:
        payload = job.payload or {}
        today = date.today()
        start_date = (
            date.fromisoformat(str(payload["start_date"]))
            if payload.get("start_date")
            else today - timedelta(days=int(payload.get("lookback_days") or 365))
        )
        end_date = (
            date.fromisoformat(str(payload["end_date"]))
            if payload.get("end_date")
            else today + timedelta(days=int(payload.get("lookahead_days") or 90))
        )
        self.jobs.heartbeat(job, f"正在刷新 {start_date} 至 {end_date} 的新股上市数据", 5)
        result = EventIngestionService(
            self.session,
            tushare_token=self.tushare_token,
        ).refresh_ipos(start_date, end_date)
        job.total = int(result["total"])
        job.completed = int(result["succeeded"])
        job.failed = int(result["failed"])
        if result["status"] == "failed":
            job.result = result
            self.session.commit()
            errors = result.get("errors") or []
            message = errors[0].get("error") if errors else "新股数据刷新失败"
            raise RuntimeError(str(message))
        self.jobs.heartbeat(job, f"新股数据已刷新，共写入 {result['rows_written']} 条", 95)
        return result

    def _market_radar(self, job: Job) -> dict[str, Any]:
        self.jobs.heartbeat(job, "正在从 v2 行情重新计算市场雷达", 10)
        snapshot = MarketRadarService(self.session).refresh()
        self.jobs.heartbeat(job, "v2 市场雷达快照已生成", 95)
        result = {
            "trade_date": snapshot["trade_date"],
            "algorithm_version": "2.0",
            "freshness": snapshot.get("freshness", "partial"),
            "coverage_rate": (snapshot.get("coverage") or {}).get("rate", 0.0),
            "snapshot": snapshot,
        }
        if result["freshness"] != "fresh":
            self.jobs.complete(job, result=result, partial=True)
        return result

    def _codes(self, payload: dict[str, Any]) -> list[str]:
        codes = [str(code).strip() for code in payload.get("codes", []) if str(code).strip()]
        if codes:
            return codes
        return list(self.session.scalars(select(Instrument.code).where(Instrument.status == "active")).all())

    @staticmethod
    def _normalize_scan_outcome(
        raw: ScanBatchOutcome | list[dict[str, Any]], codes: list[str]
    ) -> ScanBatchOutcome:
        if isinstance(raw, ScanBatchOutcome):
            return raw
        matched_by_code = {str(item.get("code")): item for item in raw}
        return ScanBatchOutcome(
            matches=raw,
            subjects=[
                ScanSubjectOutcome(
                    code=code,
                    status="matched" if code in matched_by_code else "no_signal",
                    result=matched_by_code.get(code),
                )
                for code in codes
            ],
        )

    def _finish_scan_items(
        self,
        job: Job,
        outcome: ScanBatchOutcome,
        items_by_code: dict[str, Any],
    ) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for subject in outcome.subjects:
            item = items_by_code.get(subject.code)
            if item is None:
                continue
            if subject.status == "failed":
                error = sanitize_text(subject.error or "缠论扫描失败", limit=500)
                errors.append({"code": subject.code, "error": error})
                self.jobs.finish_item(job, item, {"outcome": "failed"}, error=error)
            else:
                self.jobs.finish_item(
                    job,
                    item,
                    {"outcome": subject.status, "match": subject.result},
                )
        return errors

    def _scan(self, job: Job) -> dict[str, Any]:
        codes = self._codes(job.payload)
        instrument_ids = dict(self.session.execute(select(Instrument.code, Instrument.id)).all())
        items_by_code = self.jobs.prepare_items(job, codes, instrument_ids)
        scan_side = str(job.payload.get("scan_side") or "buy")
        is_buy = job.kind == "scan.buy" or (job.kind == "screen.smart" and scan_side == "buy")
        is_sell = job.kind == "scan.sell" or (job.kind == "screen.smart" and scan_side == "sell")
        buy_types = job.payload.get("types", ["1", "1p", "2", "3a", "3b"]) if is_buy else []
        sell_types = job.payload.get("types", ["1", "2", "3a", "3b"]) if is_sell else []
        self.jobs.heartbeat(job, f"准备扫描 {len(codes)} 只股票", 2)

        def progress(current: int, total: int, found: int) -> None:
            if current == total or current % max(1, total // 100) == 0:
                self.jobs.heartbeat(
                    job, f"扫描中 {current}/{total}，发现 {found}", current / max(1, total) * 90
                )

        outcome = self._normalize_scan_outcome(
            self.chan_engine.scan(
                stock_codes=codes,
                buy_types=buy_types,
                sell_types=sell_types,
                progress_callback=progress,
                industries=job.payload.get("industries"),
                areas=job.payload.get("areas"),
                exclude_st=bool(job.payload.get("exclude_st", True)),
            ),
            codes,
        )
        results = outcome.matches
        self.session.execute(delete(ScanResult).where(ScanResult.job_id == job.id))
        for rank, result in enumerate(results, start=1):
            signals = result.get("signals") or []
            latest = max(signals, key=lambda item: item.get("date", ""), default={})
            raw_date = str(latest.get("date", ""))[:10]
            try:
                signal_date = date.fromisoformat(raw_date)
            except ValueError:
                signal_date = None
            self.session.add(
                ScanResult(
                    job_id=job.id,
                    instrument_id=instrument_ids.get(str(result.get("code"))),
                    scan_kind=job.kind,
                    signal_type=latest.get("type"),
                    signal_date=signal_date,
                    rank=rank,
                    score=result.get("score"),
                    payload=result,
                )
            )
        self.session.commit()
        errors = self._finish_scan_items(job, outcome, items_by_code)
        result = {
            "count": len(results),
            "processed": len(outcome.subjects),
            "matched": len(results),
            "failed": len(errors),
            "coverage_rate": (len(outcome.subjects) - len(errors)) / max(1, len(outcome.subjects)),
            "errors": errors,
            "scan_kind": job.kind,
            "algorithm_version": "chan-core-v2-memory-1",
        }
        if errors:
            self.jobs.complete(job, result=result, partial=True)
        return result

    def _hot_screen(self, job: Job) -> dict[str, Any]:
        rank_type = job.payload.get("rank_type", "top_gainers")
        top_n = min(500, max(1, int(job.payload.get("top_n", 200))))
        self.jobs.heartbeat(job, "正在获取热门股票", 10)
        stocks = self.market_provider.hot_stocks(rank_type=rank_type, top_n=top_n)
        codes = [str(stock.get("code") or "").strip() for stock in stocks]
        codes = [code for code in codes if code]
        instrument_ids = dict(self.session.execute(select(Instrument.code, Instrument.id)).all())
        items_by_code = self.jobs.prepare_items(job, codes, instrument_ids)
        buy_types = job.payload.get("types", ["1", "2", "3a", "3b"])

        def progress(current: int, total: int, found: int) -> None:
            if current == total or current % max(1, total // 100) == 0:
                self.jobs.heartbeat(
                    job,
                    f"热门股票缠论扫描 {current}/{total}，发现 {found}",
                    10 + current / max(1, total) * 80,
                )

        outcome = self._normalize_scan_outcome(
            self.chan_engine.scan(
                stock_codes=codes,
                buy_types=buy_types,
                sell_types=[],
                progress_callback=progress,
                industries=None,
                areas=None,
                exclude_st=True,
            ),
            codes,
        )
        results = outcome.matches
        hot_by_code = {str(stock.get("code")): stock for stock in stocks}
        self.session.execute(delete(ScanResult).where(ScanResult.job_id == job.id))
        for rank, result in enumerate(results, start=1):
            signals = result.get("signals") or []
            latest = max(signals, key=lambda item: item.get("date", ""), default={})
            raw_date = str(latest.get("date", ""))[:10]
            try:
                signal_date = date.fromisoformat(raw_date)
            except ValueError:
                signal_date = None
            payload = {**result, "market_rank": hot_by_code.get(str(result.get("code")), {})}
            self.session.add(
                ScanResult(
                    job_id=job.id,
                    instrument_id=instrument_ids.get(str(result.get("code"))),
                    scan_kind=job.kind,
                    signal_type=latest.get("type"),
                    signal_date=signal_date,
                    rank=rank,
                    score=result.get("score"),
                    payload=payload,
                )
            )
        self.session.commit()
        errors = self._finish_scan_items(job, outcome, items_by_code)
        result = {
            "rank_type": rank_type,
            "candidate_count": len(stocks),
            "processed": len(outcome.subjects),
            "match_count": len(results),
            "matched": len(results),
            "failed": len(errors),
            "coverage_rate": (len(outcome.subjects) - len(errors)) / max(1, len(outcome.subjects)),
            "errors": errors,
        }
        if errors:
            self.jobs.complete(job, result=result, partial=True)
        return result

    def _limit_up_analysis(self, job: Job) -> dict[str, Any]:
        trade_date_text = str(job.payload.get("trade_date") or date.today().strftime("%Y%m%d")).replace(
            "-", ""
        )
        trade_date = datetime.strptime(trade_date_text, "%Y%m%d").date()
        codes = self._codes(job.payload)
        instrument_ids = dict(self.session.execute(select(Instrument.code, Instrument.id)).all())
        items_by_code = self.jobs.prepare_items(job, codes, instrument_ids)
        service = LimitUpAnalysisService(self.session, self.ai_provider)
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for index, code in enumerate(codes, start=1):
            self.session.refresh(job)
            if job.cancel_requested:
                self.jobs.mark_cancelled(job)
                return {"cancelled": True, "completed": len(results), "errors": errors}
            item = items_by_code[code]
            if item.status == "completed" and item.result:
                results.append(dict(item.result))
                continue
            self.jobs.start_item(job, item)
            self.jobs.heartbeat(
                job,
                f"正在分析 {code}（{index}/{len(codes)}）",
                index / max(1, len(codes)) * 90,
            )
            try:
                existing = self.session.scalar(
                    select(AnalysisRun).where(AnalysisRun.job_item_id == item.id)
                )
                if existing is not None:
                    result = {
                        "code": code,
                        "analysis_run_id": existing.id,
                        "partial": existing.status == "partial",
                    }
                else:
                    quantitative = service.build_quantitative(trade_date, code)
                    reports = asyncio.run(service.generate_structured_reports(quantitative))
                    partial = not all(report.successful for report in reports)
                    run_id = self._save_limit_up_run(quantitative, reports, partial, item.id)
                    result = {
                        "code": code,
                        "analysis_run_id": run_id,
                        "overall_score": quantitative.get("overall_score"),
                        "partial": partial,
                    }
                results.append(result)
                status = "partial" if result["partial"] else "completed"
                self.jobs.finish_item(job, item, result, status=status)
            except Exception as exc:
                self.session.rollback()
                safe_error = sanitize_text(exc, limit=500)
                errors.append({"code": code, "error": safe_error})
                restored_job = self.jobs.get(job.id)
                restored_item = self.session.get(JobItem, item.id)
                if restored_job is None or restored_item is None:
                    raise
                job = restored_job
                items_by_code[code] = restored_item
                self.jobs.finish_item(job, restored_item, {"code": code}, error=safe_error)
        partial_count = sum(bool(item.get("partial")) for item in results)
        result = {
            "trade_date": trade_date_text,
            "items": results,
            "count": len(results),
            "failed": len(errors),
            "partial": partial_count,
            "coverage_rate": len(results) / max(1, len(codes)),
            "errors": errors,
        }
        if errors or partial_count:
            self.jobs.complete(job, result=result, partial=True)
        return result

    def _save_limit_up_run(
        self,
        result: dict[str, Any],
        reports: tuple[GeneratedReport, GeneratedReport],
        partial: bool,
        job_item_id: str,
    ) -> str:
        trade_date = datetime.strptime(result["trade_date"], "%Y%m%d").date()
        code = result["code"]
        instrument = self.session.scalar(select(Instrument).where(Instrument.code == code))
        event = self.session.scalar(
            select(LimitUpEvent).where(
                LimitUpEvent.trade_date == trade_date, LimitUpEvent.legacy_code == code
            )
        )
        run = AnalysisRun(
            kind="limit_up",
            instrument_id=instrument.id if instrument else None,
            subject_date=trade_date,
            job_item_id=job_item_id,
            status="partial" if partial else "completed",
            algorithm_version="limit-up-v2.0",
            input_snapshot=result.get("snapshot"),
            finished_at=datetime.now(),
        )
        self.session.add(run)
        self.session.flush()
        for role, report in zip(("analyst", "review"), reports, strict=True):
            payload = dict(report.payload or {})
            payload["generation_metadata"] = report.generation_metadata()
            if role == "review":
                payload = {**payload, "review_metadata": {"independent": report.independent_review}}
            self.session.add(
                AnalysisReport(
                    analysis_run_id=run.id,
                    role=role,
                    content=report.markdown,
                    structured_payload=payload,
                    input_digest=report.input_digest,
                    validation_status=report.validation_status,
                    validation_error=report.validation_error,
                    status="completed" if report.successful else "failed",
                    provider=report.provider or limit_up_report_metadata()["provider"],
                    model=report.model or limit_up_report_metadata()["model"],
                    prompt_version=limit_up_report_metadata()["prompt_version"],
                )
            )
        if event:
            self.session.add(
                LimitUpMetric(
                    analysis_run_id=run.id,
                    limit_up_event_id=event.id,
                    day_score=result.get("day_score"),
                    week_score=result.get("week_score"),
                    value_score=result.get("value_score"),
                    market_score=result.get("market_score"),
                    timing_score=result.get("timing_score"),
                    risk_score=result.get("risk_score"),
                    overall_score=result.get("overall_score"),
                    day_classification=result.get("day_classification"),
                    week_classification=result.get("week_classification"),
                    completeness=result.get("completeness"),
                    evidence=result.get("score_details"),
                    risk_plan=result.get("risk_plan"),
                )
            )
        self.session.commit()
        return run.id

    def _ipo_analysis(self, job: Job) -> dict[str, Any]:
        codes = self._codes(job.payload)
        instrument_ids = dict(self.session.execute(select(Instrument.code, Instrument.id)).all())
        items_by_code = self.jobs.prepare_items(job, codes, instrument_ids)
        analysis_date = datetime.strptime(
            str(job.payload.get("analysis_date") or date.today().strftime("%Y%m%d")).replace("-", ""),
            "%Y%m%d",
        ).date()
        service = IpoAnalysisService(self.session, self.ai_provider)
        metadata = ipo_report_metadata()
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        for index, code in enumerate(codes, start=1):
            self.session.refresh(job)
            if job.cancel_requested:
                self.jobs.mark_cancelled(job)
                return {"cancelled": True, "completed": len(results), "errors": errors}
            item = items_by_code[code]
            if item.status == "completed" and item.result:
                results.append(dict(item.result))
                continue
            self.jobs.start_item(job, item)
            self.jobs.heartbeat(
                job,
                f"正在分析新股 {code}（{index}/{len(codes)}）",
                index / max(1, len(codes)) * 90,
            )
            try:
                run = self.session.scalar(select(AnalysisRun).where(AnalysisRun.job_item_id == item.id))
                if run is None:
                    snapshot = service.build_snapshot(code, analysis_date)
                    reports = asyncio.run(service.generate_structured_reports(snapshot))
                    partial = not all(report.successful for report in reports)
                    instrument = self.session.scalar(select(Instrument).where(Instrument.code == code))
                    run = AnalysisRun(
                        kind="ipo",
                        instrument_id=instrument.id if instrument else None,
                        subject_date=analysis_date,
                        job_item_id=item.id,
                        status="partial" if partial else "completed",
                        algorithm_version="ipo-v2.0",
                        input_snapshot=snapshot,
                        finished_at=datetime.now(),
                    )
                    self.session.add(run)
                    self.session.flush()
                    for role, report in zip(("analyst", "review"), reports, strict=True):
                        payload = dict(report.payload or {})
                        payload["generation_metadata"] = report.generation_metadata()
                        if role == "review":
                            payload = {**payload, "review_metadata": {"independent": report.independent_review}}
                        self.session.add(
                            AnalysisReport(
                                analysis_run_id=run.id,
                                role=role,
                                content=report.markdown,
                                structured_payload=payload,
                                input_digest=report.input_digest,
                                validation_status=report.validation_status,
                                validation_error=report.validation_error,
                                status="completed" if report.successful else "failed",
                                provider=report.provider or metadata["provider"],
                                model=report.model or metadata["model"],
                                prompt_version=metadata["prompt_version"],
                            )
                        )
                    self.session.commit()
                result = {"code": code, "analysis_run_id": run.id, "partial": run.status == "partial"}
                results.append(result)
                self.jobs.finish_item(
                    job,
                    item,
                    result,
                    status="partial" if result["partial"] else "completed",
                )
            except Exception as exc:
                self.session.rollback()
                safe_error = sanitize_text(exc, limit=500)
                errors.append({"code": code, "error": safe_error})
                restored_job = self.jobs.get(job.id)
                restored_item = self.session.get(JobItem, item.id)
                if restored_job is None or restored_item is None:
                    raise
                job = restored_job
                items_by_code[code] = restored_item
                self.jobs.finish_item(job, restored_item, {"code": code}, error=safe_error)
        partial_count = sum(bool(item.get("partial")) for item in results)
        result = {
            "analysis_date": analysis_date.isoformat(),
            "items": results,
            "count": len(results),
            "failed": len(errors),
            "partial": partial_count,
            "coverage_rate": len(results) / max(1, len(codes)),
            "errors": errors,
        }
        if errors or partial_count:
            self.jobs.complete(job, result=result, partial=True)
        return result

    def _stock_analysis(self, job: Job) -> dict[str, Any]:
        code = str(job.payload.get("code", "")).strip()
        if not code:
            raise ValueError("code is required")
        instrument_ids = dict(self.session.execute(select(Instrument.code, Instrument.id)).all())
        item = self.jobs.prepare_items(job, [code], instrument_ids)[code]
        existing = self.session.scalar(select(AnalysisRun).where(AnalysisRun.job_item_id == item.id))
        if existing is not None:
            result = {"analysis_run_id": existing.id, "code": code, "analysis": existing.input_snapshot}
            self.jobs.finish_item(job, item, {"analysis_run_id": existing.id, "code": code})
            return result
        self.jobs.start_item(job, item)
        self.jobs.heartbeat(job, f"正在生成 {code} 缠论结构", 30)
        logger.info("Chan structure calculation started", extra={"job_id": job.id, "instrument_code": code})
        analysis = self.chan_engine.analyze(code)
        logger.info("Chan structure calculation completed", extra={"job_id": job.id, "instrument_code": code})
        instrument = self.session.scalar(select(Instrument).where(Instrument.code == code))
        run = AnalysisRun(
            kind="stock",
            instrument_id=instrument.id if instrument else None,
            subject_date=date.today(),
            job_item_id=item.id,
            status="completed",
            algorithm_version=getattr(self.chan_engine, "algorithm_version", "chan-core-v2-memory-1"),
            input_snapshot=analysis,
            finished_at=datetime.now(),
        )
        self.session.add(run)
        logger.info("Persisting Chan structure snapshot", extra={"job_id": job.id, "instrument_code": code})
        self.session.commit()
        logger.info("Chan structure snapshot persisted", extra={"job_id": job.id, "instrument_code": code})
        result = {"analysis_run_id": run.id, "code": code, "analysis": analysis}
        self.jobs.finish_item(job, item, {"analysis_run_id": run.id, "code": code})
        return result

    def _data_refresh(self, job: Job) -> dict[str, Any]:
        payload = job.payload or {}
        raw_start = payload.get("start_date")
        raw_end = payload.get("end_date")
        start_date = date.fromisoformat(str(raw_start)) if raw_start else None
        end_date = date.fromisoformat(str(raw_end)) if raw_end else None
        codes = [str(code).strip() for code in payload.get("codes", []) if str(code).strip()] or None
        lookback_days = max(1, min(3650, int(payload.get("lookback_days") or 10)))
        service = IngestionService(self.session, self.ingestion_provider)
        progress_started_at = time.monotonic()

        def cancelled() -> bool:
            self.session.refresh(job)
            return bool(job.cancel_requested)

        def progress(current: int, total: int, code: str) -> None:
            job.total = total
            job.completed = current
            report_interval = max(1, min(25, total // 100))
            if current == total or current <= 3 or current % report_interval == 0:
                elapsed = max(0.001, time.monotonic() - progress_started_at)
                rate_per_minute = current / elapsed * 60
                remaining_seconds = (total - current) / max(current / elapsed, 0.001)
                remaining_minutes = max(0, int((remaining_seconds + 59) // 60))
                hours, minutes = divmod(remaining_minutes, 60)
                eta = f"{hours}小时{minutes}分钟" if hours else f"{minutes}分钟"
                self.jobs.heartbeat(
                    job,
                    (
                        f"正在刷新行情 {current}/{total}（{code}）"
                        f" · {rate_per_minute:.1f}只/分钟 · 预计剩余{eta}"
                    ),
                    current / max(1, total) * 95,
                )

        should_refresh_master = bool(payload.get("refresh_master_data", codes is None))
        if should_refresh_master:
            self.jobs.heartbeat(job, "正在刷新股票主数据", 1)
            master_data = service.refresh_master_data()
        else:
            master_data = {"status": "skipped", "reason": "targeted_refresh"}
        self.jobs.heartbeat(job, "正在准备 v2 原生行情刷新", 2)
        progress_started_at = time.monotonic()
        result = service.refresh_bars(
            codes=codes,
            timeframe=str(payload.get("timeframe") or "DAY").upper(),
            adjustment=str(payload.get("adjustment") or "QFQ").upper(),
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
            progress=progress,
            cancelled=cancelled,
        )
        result["master_data"] = master_data
        if result["status"] == "cancelled":
            self.jobs.mark_cancelled(job)
        elif result["status"] == "partial":
            job.failed = int(result["failed"])
            self.jobs.complete(job, result=result, partial=True)
        return result
