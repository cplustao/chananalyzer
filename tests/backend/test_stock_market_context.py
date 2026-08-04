from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from backend.app.db.models import Instrument
from backend.app.services.stock_market_context import StockMarketContextService


class QuoteResponse:
    fields = [""] * 38
    fields[0] = "v_sh600519=1"
    fields[1] = "贵州茅台"
    fields[2] = "600519"
    fields[3] = "1501.00"
    fields[4] = "1490.00"
    fields[5] = "1495.00"
    fields[6] = "1000"
    fields[30] = "20260804101500"
    fields[31] = "11.00"
    fields[32] = "0.74"
    fields[33] = "1510.00"
    fields[34] = "1488.00"
    fields[37] = "2.50"
    content = "~".join(fields).encode("gbk")

    @staticmethod
    def raise_for_status() -> None:
        return None


def test_realtime_quote_is_structured_and_time_bounded():
    service = StockMarketContextService(
        now=lambda: datetime(2026, 8, 4, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")),
        quote_get=lambda *args, **kwargs: QuoteResponse(),
    )
    instrument = Instrument(code="600519", ts_code="600519.SH", exchange="SH", name="贵州茅台")

    quote = service.realtime_quote(instrument)

    assert quote["status"] == "fresh"
    assert quote["source"] == "tencent_quote"
    assert quote["price"] == 1501.0
    assert quote["quote_time"] == "20260804101500"


def test_money_flow_uses_provider_amount_fields_without_close_price_approximation():
    frame = pd.DataFrame(
        [
            {
                "trade_date": "20260804",
                "net_mf_amount": 123.4,
                "net_mf_vol": 12,
                "buy_lg_amount": 80,
                "sell_lg_amount": 30,
                "buy_elg_amount": 40,
                "sell_elg_amount": 10,
            }
        ]
    )
    client = SimpleNamespace(moneyflow=lambda **kwargs: frame)
    service = StockMarketContextService(tushare_client=client)
    instrument = Instrument(code="600519", ts_code="600519.SH", exchange="SH", name="贵州茅台")

    flow = service.money_flow(instrument)

    assert flow["status"] == "fresh"
    assert flow["source"] == "tushare.moneyflow"
    assert flow["items"][0]["net_amount"] == 123.4
    assert flow["items"][0]["main_net_amount"] == 80.0
