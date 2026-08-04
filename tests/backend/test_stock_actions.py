from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select

from backend.app.db.models import (
    AnalysisReport,
    AnalysisRun,
    Instrument,
    Job,
    TradingCalendar,
)
from backend.app.repositories.jobs import JobRepository
from backend.app.services.job_handlers import JobHandlers


def test_stock_routes_separate_ai_and_targeted_refresh(client, session):
    stock = Instrument(
        code="600519",
        ts_code="600519.SH",
        exchange="SH",
        name="贵州茅台",
        status="active",
    )
    legacy_index = Instrument(
        code="sh.000300",
        ts_code="sh.000300",
        name="沪深300",
        status="active",
    )
    session.add_all([stock, legacy_index])
    session.commit()

    analysis = client.post(
        f"/api/v1/instruments/{stock.id}/analyses?force=true&include_ai=true",
        json={},
    )
    assert analysis.status_code == 202
    analysis_job = session.get(Job, analysis.json()["job_id"])
    assert analysis_job is not None
    assert analysis_job.kind == "stock.analyze"
    assert analysis_job.payload == {"code": "600519", "include_ai": True}

    refresh = client.post(f"/api/v1/instruments/{stock.id}/refresh", json={})
    assert refresh.status_code == 202
    refresh_job = session.get(Job, refresh.json()["job_id"])
    assert refresh_job is not None
    assert refresh_job.kind == "data.refresh"
    assert refresh_job.payload == {
        "codes": ["600519"],
        "lookback_days": 30,
        "timeframe": "DAY",
        "adjustment": "QFQ",
        "refresh_master_data": False,
    }

    rejected = client.post(f"/api/v1/instruments/{legacy_index.id}/refresh", json={})
    assert rejected.status_code == 422
    assert "不是规范 A 股个股" in rejected.json()["message"]


class FakeChanEngine:
    algorithm_version = "chan-test-2.0"

    def analyze(self, code: str) -> dict:
        today = date.today().strftime("%Y/%m/%d")
        return {
            "code": code,
            "name": "贵州茅台",
            "kl_type": "日线",
            "start_date": "2025/01/01",
            "end_date": today,
            "kline_count": 300,
            "current_price": 1500.0,
            "macd": {"macd": 1.2, "dif": 2.3, "dea": 1.7},
            "kline_range": {"period": 20, "period_high": 1550, "period_low": 1420},
            "volume_analysis": {"vol_ratio": 1.1, "vol_price_rel": "价涨量增"},
            "zs_position": "中枢上方",
            "latest": {"bi": {"dir": "向上", "end_date": today}},
            "bi_list": [
                {
                    "idx": 1,
                    "dir": "向上",
                    "start_date": "2026/07/01",
                    "end_date": today,
                    "start_price": 1400,
                    "end_price": 1500,
                    "is_sure": True,
                }
            ],
            "seg_list": [],
            "zs_list": [],
            "buy_signals": [
                {"type": "2", "is_buy": True, "date": today, "price": 1480}
            ],
            "sell_signals": [],
        }


class FakeStockMarketContext:
    def snapshot(self, instrument: Instrument) -> dict:
        return {
            "realtime_quote": {
                "status": "fresh", "source": "fake.quote", "price": 1501.0,
                "quote_time": str(date.today()), "is_trading": True,
            },
            "money_flow": {
                "status": "fresh", "source": "fake.moneyflow", "data_date": str(date.today()),
                "items": [{"trade_date": str(date.today()), "net_amount": 1200.0, "main_net_amount": 800.0}],
            },
        }

class EchoStructuredProvider:
    key = "fake-ai"
    model = "fake-model"
    last_provider = "fake-ai"
    last_model = "fake-model"
    last_attempts: list[dict] = []
    fallback_reason = None

    async def generate(self, request) -> str:
        marker = "Authoritative rule fields (read-only):\n"
        raw = request.user_prompt.split(marker, 1)[1].split("\n\n", 1)[0]
        authoritative = json.loads(raw)
        self.last_attempts = [
            {
                "provider": self.key,
                "model": self.model,
                "attempt": 1,
                "outcome": "success",
            }
        ]
        return json.dumps(
            {
                "analysis_status": "complete",
                "summary": "趋势向上但仍需等待确认。",
                "evidence": [
                    {"claim": "最近一笔向上", "source": "缠论结构", "data_time": str(date.today())}
                ],
                "counter_evidence": [
                    {"claim": "存在波动风险", "source": "量价结构", "data_time": str(date.today())}
                ],
                "risks": ["跌破最近结构低点"],
                "missing_data": [],
                "upgrade_conditions": ["放量突破压力位"],
                "downgrade_conditions": ["结构转弱"],
                "confidence": 0.72,
                "requires_human_review": True,
                "authoritative_rule_fields": authoritative,
            },
            ensure_ascii=False,
        )


def test_stock_ai_handler_persists_analyst_and_independent_review(session):
    stock = Instrument(
        code="600519",
        ts_code="600519.SH",
        exchange="SH",
        name="贵州茅台",
        status="active",
    )
    session.add_all(
        [
            stock,
            TradingCalendar(trade_date=date.today() - timedelta(days=1), is_open=True, exchange="SSE"),
            TradingCalendar(trade_date=date.today(), is_open=True, exchange="SSE"),
        ]
    )
    session.commit()
    repository = JobRepository(session)
    repository.create(
        "stock.analyze",
        {"code": stock.code, "include_ai": True},
        None,
        force=True,
        max_attempts=3,
    )
    job = repository.claim_next()
    assert job is not None

    result = JobHandlers(
        session,
        chan_engine=FakeChanEngine(),
        ai_provider=EchoStructuredProvider(),
        stock_market_context=FakeStockMarketContext(),
    ).execute(job)
    if job.status == "running":
        repository.complete(job, result=result)

    run = session.scalar(select(AnalysisRun).where(AnalysisRun.job_item_id.is_not(None)))
    assert run is not None
    reports = list(
        session.scalars(
            select(AnalysisReport)
            .where(AnalysisReport.analysis_run_id == run.id)
            .order_by(AnalysisReport.role)
        ).all()
    )
    assert result["include_ai"] is True
    assert result["partial"] is False
    assert result["report_count"] == 3
    assert run.status == "completed"
    assert run.input_snapshot["input_freshness"] == "fresh"
    assert run.input_snapshot["expected_trade_date"] is not None
    assert run.input_snapshot["code"] == stock.code
    assert "recent_strokes" in run.input_snapshot
    assert {report.role for report in reports} == {"analyst", "review", "decision"}
    assert all(report.validation_status == "validated" for report in reports)
    review = next(report for report in reports if report.role == "review")
    assert review.structured_payload["review_metadata"]["independent"] is True
    assert run.input_snapshot["realtime_quote"]["source"] == "fake.quote"
    assert run.input_snapshot["money_flow"]["items"][0]["net_amount"] == 1200.0
    decision = next(report for report in reports if report.role == "decision")
    assert "趋势向上" in decision.content
