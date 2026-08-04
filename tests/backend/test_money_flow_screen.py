from sqlalchemy import select

from backend.app.db.models import Instrument, ScanResult
from backend.app.repositories.jobs import JobRepository
from backend.app.services.job_handlers import JobHandlers


class MatchingScanEngine:
    def scan(self, *, stock_codes, **kwargs):
        return [
            {
                "code": code,
                "score": 70,
                "signals": [{"type": "2", "date": "2026-08-04", "price": 10}],
            }
            for code in stock_codes
        ]


class FlowProvider:
    def money_flow_map(self):
        return {
            "600001": {
                "source": "fake.moneyflow",
                "trade_date": "20260804",
                "net_amount": 1500,
                "main_net_amount": 800,
            },
            "600002": {
                "source": "fake.moneyflow",
                "trade_date": "20260804",
                "net_amount": 500,
                "main_net_amount": 100,
            },
        }


def test_smart_screen_filters_matches_by_money_flow_and_persists_provenance(session):
    session.add_all(
        [
            Instrument(code="600001", ts_code="600001.SH", exchange="SH", name="样本一", status="active"),
            Instrument(code="600002", ts_code="600002.SH", exchange="SH", name="样本二", status="active"),
        ]
    )
    session.commit()
    repository = JobRepository(session)
    repository.create(
        "screen.smart",
        {
            "codes": ["600001", "600002"],
            "scan_side": "buy",
            "types": ["2"],
            "min_net_mf_amount": 1000,
            "min_main_net_amount": 500,
        },
        None,
        force=True,
        max_attempts=3,
    )
    job = repository.claim_next()
    assert job is not None
    handlers = JobHandlers(session, chan_engine=MatchingScanEngine())
    handlers.market_provider = FlowProvider()

    result = handlers.execute(job)

    assert result["count"] == 1
    row = session.scalar(select(ScanResult).where(ScanResult.job_id == job.id))
    assert row is not None
    assert row.payload["code"] == "600001"
    assert row.payload["money_flow"]["source"] == "fake.moneyflow"
