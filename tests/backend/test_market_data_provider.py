import socket
import sys
import threading
from datetime import date
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from backend.app.providers import market_data
from backend.app.providers.market_data import (
    BaoStockMarketDataProvider,
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


class FakeBaoStockResult:
    def __init__(self, error_code="0", error_msg="", rows=()):
        self.error_code = error_code
        self.error_msg = error_msg
        self._rows = list(rows)

    def next(self):
        return bool(self._rows)

    def get_row_data(self):
        return self._rows.pop(0)


class FakeBaoStock:
    """One connection per login; a connection that drops keeps failing, like the real client."""

    OK_ROW = ["2026-07-01", "10", "11", "9", "10.5", "1000", "10500", "1.2"]

    def __init__(self, drops=1):
        self.logins = 0
        self.queries = 0
        self.logouts = 0
        self.drops = drops

    def login(self):
        self.logins += 1
        return SimpleNamespace(error_code="0", error_msg="")

    def logout(self):
        self.logouts += 1

    def query_history_k_data_plus(self, *_args, **_kwargs):
        self.queries += 1
        if self.logins <= self.drops:
            return FakeBaoStockResult("10002007", "网络接收错误。")
        return FakeBaoStockResult(rows=[list(self.OK_ROW)])


def fetch_baostock_bars(provider):
    return provider.fetch_bars(
        ts_code="600519.SH", timeframe="DAY", adjustment="QFQ",
        start_date=date(2026, 7, 1), end_date=date(2026, 7, 1),
    )


def test_baostock_reconnects_after_a_dropped_connection(monkeypatch):
    fake = FakeBaoStock(drops=1)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider()

    bars = fetch_baostock_bars(provider)

    assert [bar.close for bar in bars] == [10.5]
    assert fake.logins == 2
    # Logging out would send over the broken socket.
    assert fake.logouts == 0


def test_baostock_failure_does_not_poison_later_stocks(monkeypatch):
    fake = FakeBaoStock(drops=2)
    monkeypatch.setitem(sys.modules, "baostock", fake)
    provider = BaoStockMarketDataProvider()

    with pytest.raises(RuntimeError, match="网络接收错误"):
        fetch_baostock_bars(provider)

    assert [bar.close for bar in fetch_baostock_bars(provider)] == [10.5]
    assert fake.logins == 3


def send_through_real_baostock(monkeypatch, serve):
    """Run BaoStock's own receive loop against a local server, in a thread a spin cannot block."""
    socketutil = pytest.importorskip("baostock.util.socketutil")
    constants = pytest.importorskip("baostock.common.contants")
    monkeypatch.setattr(socketutil, "socket", socketutil.socket)
    server = socket.create_server(("127.0.0.1", 0))
    threading.Thread(target=serve, args=(server,), daemon=True).start()
    monkeypatch.setattr(constants, "BAOSTOCK_SERVER_IP", "127.0.0.1")
    monkeypatch.setattr(constants, "BAOSTOCK_SERVER_PORT", server.getsockname()[1])
    monkeypatch.setattr(market_data, "BAOSTOCK_SOCKET_TIMEOUT_SECONDS", 0.2)

    market_data._guard_baostock_connections()
    socketutil.SocketUtil().connect()
    replies = []
    sender = threading.Thread(target=lambda: replies.append(socketutil.send_msg("ping")), daemon=True)
    sender.start()
    sender.join(5)
    server.close()

    assert not sender.is_alive(), "BaoStock's receive loop did not end"
    return replies


def test_baostock_receive_loop_ends_when_the_server_closes(monkeypatch):
    def close_after_request(server):
        connection, _ = server.accept()
        connection.recv(1024)
        connection.close()

    assert send_through_real_baostock(monkeypatch, close_after_request) == [None]


def test_baostock_receive_loop_ends_when_the_server_goes_silent(monkeypatch):
    held = []

    def hold_connection(server):
        held.append(server.accept()[0])

    assert send_through_real_baostock(monkeypatch, hold_connection) == [None]
    held[0].close()


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