from datetime import date

import pandas as pd
from sqlalchemy import select

from backend.app.db.models import Instrument, IpoEvent, LimitUpEvent
from backend.app.services.event_ingestion import EventIngestionService


def test_limit_up_refresh_upserts_rows_and_quarantines_unknown_codes(session):
    instrument = Instrument(
        code="600001",
        ts_code="600001.SH",
        exchange="SH",
        name="样本股票",
        status="active",
    )
    session.add(instrument)
    session.commit()

    def fetcher(_: date):
        return pd.DataFrame(
            [
                {
                    "代码": "600001",
                    "名称": "样本股票",
                    "最新价": 12.34,
                    "涨跌幅": 10.01,
                    "换手率": 5.6,
                    "连板数": 2,
                    "所属行业": "软件服务",
                },
                {
                    "代码": "999999",
                    "名称": "未知股票",
                    "最新价": 8.0,
                    "涨跌幅": 9.99,
                    "换手率": 3.2,
                    "连板数": 1,
                    "所属行业": "未知",
                },
            ]
        )

    service = EventIngestionService(session, limit_up_fetcher=fetcher)
    result = service.refresh_limit_ups([date(2026, 7, 29)], max_attempts=1)
    service.refresh_limit_ups([date(2026, 7, 29)], max_attempts=1)

    rows = list(session.scalars(select(LimitUpEvent).order_by(LimitUpEvent.legacy_code)).all())
    assert result["status"] == "completed"
    assert result["rows_written"] == 2
    assert len(rows) == 2
    assert rows[0].instrument_id == instrument.id
    assert rows[0].consecutive_boards == 2
    assert rows[0].quarantined is False
    assert rows[1].instrument_id is None
    assert rows[1].quarantined is True


def test_limit_up_refresh_reports_failed_dates(session):
    def failing_fetcher(_: date):
        raise RuntimeError("upstream unavailable")

    result = EventIngestionService(session, limit_up_fetcher=failing_fetcher).refresh_limit_ups(
        [date(2026, 7, 29)],
        max_attempts=1,
    )

    assert result["status"] == "failed"
    assert result["failed"] == 1
    assert result["errors"][0]["trade_date"] == "2026-07-29"
    assert "upstream unavailable" in result["errors"][0]["error"]

def test_ipo_refresh_upserts_listing_records_and_filters_requested_range(session):
    instrument = Instrument(
        code="301677",
        ts_code="301677.SZ",
        exchange="SZ",
        name="欣兴工具",
        status="active",
    )
    session.add(instrument)
    session.commit()

    def fetcher(_start: date, _end: date):
        return (
            pd.DataFrame(
                [
                    {
                        "ts_code": "301677.SZ",
                        "name": "欣兴工具",
                        "ipo_date": "20260721",
                        "issue_date": "20260730",
                        "price": 15.8,
                        "pe": 22.5,
                    },
                    {
                        "ts_code": "600000.SH",
                        "name": "范围外样本",
                        "issue_date": "20260101",
                        "price": 10,
                    },
                    {"ts_code": "600001.SH", "name": "日期待定", "issue_date": None},
                ]
            ),
            "test.new_share",
        )

    service = EventIngestionService(session, ipo_fetcher=fetcher)
    result = service.refresh_ipos(date(2026, 7, 1), date(2026, 8, 31), max_attempts=1)
    service.refresh_ipos(date(2026, 7, 1), date(2026, 8, 31), max_attempts=1)

    rows = list(session.scalars(select(IpoEvent)).all())
    assert result["status"] == "completed"
    assert result["rows_written"] == 1
    assert result["skipped_outside_range"] == 1
    assert result["skipped_without_listing_date"] == 1
    assert len(rows) == 1
    assert rows[0].instrument_id == instrument.id
    assert rows[0].raw_payload["source"] == "test.new_share"
