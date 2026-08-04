from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import requests

from backend.app.db.models import Instrument

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return None if number != number else number
    except (TypeError, ValueError):
        return None


class StockMarketContextService:
    """Fetches optional, provenance-rich market context for one stock."""

    def __init__(
        self,
        tushare_token: str | None = None,
        *,
        now: Callable[[], datetime] | None = None,
        quote_get: Callable[..., Any] | None = None,
        tushare_client: Any | None = None,
    ):
        self.tushare_token = tushare_token
        self._now = now or (lambda: datetime.now(SHANGHAI))
        self._quote_get = quote_get or requests.get
        self._tushare_client = tushare_client

    def snapshot(self, instrument: Instrument) -> dict[str, Any]:
        return {
            "realtime_quote": self.realtime_quote(instrument),
            "money_flow": self.money_flow(instrument),
        }

    def realtime_quote(self, instrument: Instrument) -> dict[str, Any]:
        now = self._now().astimezone(SHANGHAI)
        base = {
            "source": "tencent_quote",
            "fetched_at": now.isoformat(),
            "is_trading": self._is_trading(now),
        }
        if not base["is_trading"]:
            return {**base, "status": "market_closed", "reason": "当前为非交易时段"}
        exchange = str(instrument.exchange or "").upper()
        if exchange in {"BJ", "BSE"} or instrument.code.startswith(("8", "9")):
            market = "bj"
        elif exchange in {"SH", "SSE"} or instrument.code.startswith(("5", "6")):
            market = "sh"
        else:
            market = "sz"
        try:
            response = self._quote_get(
                f"https://qt.gtimg.cn/q={market}{instrument.code}",
                timeout=3.0,
                headers={"Referer": "https://stockapp.finance.qq.com/"},
            )
            response.raise_for_status()
            fields = response.content.decode("gbk", errors="replace").split("~")
            if len(fields) < 38 or not fields[3]:
                raise ValueError("行情响应字段不完整")
            return {
                **base,
                "status": "fresh",
                "name": fields[1],
                "code": fields[2],
                "price": _number(fields[3]),
                "previous_close": _number(fields[4]),
                "open": _number(fields[5]),
                "volume": _number(fields[6]),
                "quote_time": fields[30],
                "change": _number(fields[31]),
                "pct_change": _number(fields[32]),
                "high": _number(fields[33]),
                "low": _number(fields[34]),
                "amount_yuan": (_number(fields[37]) or 0) * 10_000,
            }
        except (requests.RequestException, UnicodeError, ValueError, IndexError):
            return {**base, "status": "unavailable", "reason": "实时行情暂不可用"}

    def money_flow(self, instrument: Instrument, days: int = 5) -> dict[str, Any]:
        base = {"source": "tushare.moneyflow", "unit": "万元", "days_requested": days}
        if not self.tushare_token and self._tushare_client is None:
            return {**base, "status": "unavailable", "reason": "未配置 Tushare Token"}
        if not instrument.ts_code:
            return {**base, "status": "unavailable", "reason": "股票缺少 Tushare 代码"}
        try:
            client = self._tushare_client
            if client is None:
                import tushare as ts

                client = ts.pro_api(self.tushare_token)
            frame = client.moneyflow(ts_code=instrument.ts_code)
            if frame is None or frame.empty:
                return {**base, "status": "missing", "reason": "暂无个股资金流数据", "items": []}
            records = frame.sort_values("trade_date", ascending=False).head(days).to_dict("records")
            items = [self._money_flow_item(row) for row in records]
            return {
                **base,
                "status": "fresh",
                "data_date": items[0]["trade_date"] if items else None,
                "days_returned": len(items),
                "items": items,
            }
        except Exception as exc:  # provider SDK exposes multiple exception types
            message = str(exc).lower()
            reason = "Tushare 权限不足" if "权限" in str(exc) or "permission" in message else "资金流暂不可用"
            return {**base, "status": "unavailable", "reason": reason, "items": []}

    @staticmethod
    def _money_flow_item(row: dict[str, Any]) -> dict[str, Any]:
        buy_large = (_number(row.get("buy_lg_amount")) or 0) + (_number(row.get("buy_elg_amount")) or 0)
        sell_large = (_number(row.get("sell_lg_amount")) or 0) + (_number(row.get("sell_elg_amount")) or 0)
        return {
            "trade_date": str(row.get("trade_date") or ""),
            "net_amount": _number(row.get("net_mf_amount")),
            "net_volume": _number(row.get("net_mf_vol")),
            "main_net_amount": round(buy_large - sell_large, 4),
            "buy_small_amount": _number(row.get("buy_sm_amount")),
            "sell_small_amount": _number(row.get("sell_sm_amount")),
            "buy_medium_amount": _number(row.get("buy_md_amount")),
            "sell_medium_amount": _number(row.get("sell_md_amount")),
            "buy_large_amount": _number(row.get("buy_lg_amount")),
            "sell_large_amount": _number(row.get("sell_lg_amount")),
            "buy_extra_large_amount": _number(row.get("buy_elg_amount")),
            "sell_extra_large_amount": _number(row.get("sell_elg_amount")),
        }

    @staticmethod
    def _is_trading(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        current = now.time().replace(tzinfo=None)
        return time(9, 30) <= current <= time(11, 30) or time(13) <= current <= time(15)
