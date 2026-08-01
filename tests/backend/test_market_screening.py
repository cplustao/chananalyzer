from datetime import datetime, timedelta

from backend.app.db.models import Bar, Instrument
from backend.app.services.market_screening import HotStockService


def test_hot_stock_rankings_use_latest_v2_bars(session):
    latest = datetime(2030, 1, 8)
    for code, previous, current, turnover in (
        ("920001", 10.0, 11.0, 3.0),
        ("920002", 10.0, 9.0, 8.0),
    ):
        instrument = Instrument(code=code, ts_code=f"{code}.SZ", name=code, status="active")
        session.add(instrument)
        session.flush()
        session.add_all(
            [
                Bar(
                    instrument_id=instrument.id,
                    timeframe="DAY",
                    adjustment="QFQ",
                    bar_time=latest - timedelta(days=1),
                    open=previous,
                    high=previous,
                    low=previous,
                    close=previous,
                    volume=100,
                    amount=1000,
                    turnover_rate=1,
                ),
                Bar(
                    instrument_id=instrument.id,
                    timeframe="DAY",
                    adjustment="QFQ",
                    bar_time=latest,
                    open=current,
                    high=current,
                    low=current,
                    close=current,
                    volume=200,
                    amount=2000,
                    turnover_rate=turnover,
                ),
            ]
        )
    session.commit()

    service = HotStockService(session)

    assert service.hot_stocks("top_gainers", 1)[0]["code"] == "920001"
    assert service.hot_stocks("top_losers", 1)[0]["code"] == "920002"
    assert service.hot_stocks("top_turnover", 1)[0]["code"] == "920002"
    assert service.hot_stocks("top_amount", 2)[0]["source"] == "v2_bars"
