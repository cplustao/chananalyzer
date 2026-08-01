from datetime import date
from types import SimpleNamespace

from pydantic import SecretStr

from backend.app.providers.market_data import (
    TushareMarketDataProvider,
    build_historical_market_provider,
)


def test_provider_factory_uses_v2_settings_when_environment_is_not_exported(monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "backend.app.core.config.get_settings",
        lambda: SimpleNamespace(tushare_token=SecretStr("settings-token")),
    )

    provider = build_historical_market_provider()

    assert isinstance(provider, TushareMarketDataProvider)
    assert provider.token == "settings-token"


def test_akshare_provider_falls_back_to_eastmoney_http(monkeypatch):
    import sys
    from types import SimpleNamespace

    from backend.app.providers.market_data import AkShareMarketDataProvider

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=lambda **_: (_ for _ in ()).throw(ConnectionError("https closed"))),
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-07-29,4.68,4.69,4.72,4.61,4146,2095755.45,2.34,-0.42,-0.02,1.26"
                    ]
                }
            }

    captured = {}

    def get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs["params"]
        return Response()

    import requests

    monkeypatch.setattr(requests, "get", get)
    bars = AkShareMarketDataProvider().fetch_bars(
        ts_code="920000.BJ",
        timeframe="DAY",
        adjustment="QFQ",
        start_date=date(2026, 7, 29),
        end_date=date(2026, 7, 29),
    )

    assert captured["url"].startswith("http://")
    assert captured["params"]["secid"] == "0.920000"
    assert captured["params"]["fqt"] == "1"
    assert len(bars) == 1
    assert bars[0].close == 4.69
    assert bars[0].turnover_rate == 1.26

def test_exchange_mapping_recognizes_new_bj_920_segment():
    from backend.app.providers.market_data import _exchange_for_code

    assert _exchange_for_code("920000") == "BJ"
    assert _exchange_for_code("900901") == "SH"
    assert _exchange_for_code("688001") == "SH"
    assert _exchange_for_code("301001") == "SZ"