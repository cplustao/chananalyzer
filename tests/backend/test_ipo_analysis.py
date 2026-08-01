import asyncio
from datetime import date, datetime, timedelta

from backend.app.db.models import Bar, Instrument, IpoEvent
from backend.app.services.ipo_analysis import IpoAnalysisService


def test_ipo_snapshot_is_reproducible_from_v2_events_and_bars(session):
    instrument = Instrument(code="930001", ts_code="930001.SZ", name="新样本", status="active")
    session.add(instrument)
    session.flush()
    listing = date(2026, 7, 20)
    session.add(
        IpoEvent(
            instrument_id=instrument.id,
            legacy_code=instrument.code,
            listing_date=listing,
            issue_price=10,
            issue_pe=20,
            raw_payload={"industry": "软件", "revenue_growth": 30},
        )
    )
    for offset, close in enumerate((12.0, 13.2)):
        session.add(
            Bar(
                instrument_id=instrument.id,
                timeframe="DAY",
                adjustment="QFQ",
                bar_time=datetime.combine(listing + timedelta(days=offset), datetime.min.time()),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100,
                amount=1000,
                turnover_rate=5,
            )
        )
    session.commit()

    service = IpoAnalysisService(session)
    snapshot = service.build_snapshot(instrument.code, date(2026, 7, 24))
    analyst, decision, partial = asyncio.run(service.generate_reports(snapshot))

    assert snapshot["ipo_price"] == 10
    assert snapshot["market_confirmation"]["trading_days"] == 2
    assert snapshot["market_confirmation"]["return_since_first_close_pct"] == 10
    assert (analyst, partial) == ("", True)
    assert "配置" in decision
