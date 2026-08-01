from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.db.models import Bar, Instrument
from backend.app.services.bar_access import preferred_bar_condition

RANK_TYPES = {
    "top_gainers",
    "top_losers",
    "top_volume",
    "top_amount",
    "top_turnover",
    "dragon_tiger",
}


class HotStockService:
    """Hot rankings from v2 bars; only dragon-tiger requires Tushare."""

    def __init__(self, session: Session, tushare_token: str | None = None):
        self.session = session
        self.tushare_token = tushare_token

    def hot_stocks(self, rank_type: str, top_n: int) -> list[dict[str, Any]]:
        if rank_type not in RANK_TYPES:
            raise ValueError(f"不支持的热门榜单：{rank_type}")
        if rank_type == "dragon_tiger":
            return self._dragon_tiger(top_n)

        preferred = preferred_bar_condition()
        latest = self.session.scalar(
            select(func.max(Bar.bar_time))
            .join(Instrument, Instrument.id == Bar.instrument_id)
            .where(Bar.timeframe == "DAY", preferred)
        )
        if latest is None:
            return []
        rows = self.session.execute(
            select(Bar, Instrument)
            .join(Instrument, Instrument.id == Bar.instrument_id)
            .where(
                Bar.bar_time == latest,
                Bar.timeframe == "DAY",
                preferred,
                Instrument.status == "active",
            )
        ).all()
        previous_date = self.session.scalar(
            select(func.max(Bar.bar_time))
            .join(Instrument, Instrument.id == Bar.instrument_id)
            .where(
                Bar.timeframe == "DAY",
                preferred,
                Bar.bar_time < latest,
            )
        )
        previous = (
            dict(
                self.session.execute(
                    select(Bar.instrument_id, Bar.close)
                    .join(Instrument, Instrument.id == Bar.instrument_id)
                    .where(
                        Bar.timeframe == "DAY",
                        preferred,
                        Bar.bar_time == previous_date,
                    )
                ).all()
            )
            if previous_date is not None
            else {}
        )
        items = []
        for bar, instrument in rows:
            previous_close = previous.get(bar.instrument_id)
            pct_change = (
                (bar.close / previous_close - 1) * 100 if previous_close not in (None, 0) else 0.0
            )
            items.append(
                {
                    "code": instrument.code,
                    "ts_code": instrument.ts_code,
                    "name": instrument.name or instrument.code,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "pre_close": previous_close,
                    "change": bar.close - previous_close if previous_close is not None else 0.0,
                    "pct_chg": pct_change,
                    "vol": bar.volume,
                    "amount": bar.amount or 0.0,
                    "turnover_rate": bar.turnover_rate or 0.0,
                    "trade_date": latest.date().isoformat(),
                    "source": "v2_bars",
                }
            )
        sort_keys = {
            "top_gainers": ("pct_chg", True),
            "top_losers": ("pct_chg", False),
            "top_volume": ("vol", True),
            "top_amount": ("amount", True),
            "top_turnover": ("turnover_rate", True),
        }
        key, reverse = sort_keys[rank_type]
        items.sort(key=lambda item: float(item.get(key) or 0), reverse=reverse)
        return items[:top_n]

    def _dragon_tiger(self, top_n: int) -> list[dict[str, Any]]:
        if not self.tushare_token:
            raise RuntimeError("龙虎榜需要先配置有效的 Tushare Token")
        import tushare as ts

        client = ts.pro_api(self.tushare_token)
        latest = self.session.scalar(
            select(func.max(Bar.bar_time)).where(Bar.timeframe == "DAY", Bar.adjustment == "QFQ")
        )
        if latest is None:
            return []
        trade_date = latest.strftime("%Y%m%d")
        frame = client.top_list(trade_date=trade_date)
        if frame is None or frame.empty:
            return []
        frame = frame[frame["ts_code"].str.endswith((".SZ", ".SH"))].drop_duplicates("ts_code").head(top_n)
        names = dict(self.session.execute(select(Instrument.code, Instrument.name)).all())
        items = []
        for record in frame.to_dict("records"):
            code = str(record["ts_code"])[:6]
            items.append(
                {
                    "code": code,
                    "ts_code": record["ts_code"],
                    "name": str(record.get("name") or names.get(code) or code),
                    "close": _number(record.get("close")),
                    "pct_chg": _number(record.get("pct_change")),
                    "reason": str(record.get("exalter") or ""),
                    "buy_amount": _number(record.get("buy_amount")),
                    "sell_amount": _number(record.get("sell_amount")),
                    "net_amount": _number(record.get("net_amount")),
                    "trade_date": trade_date,
                    "source": "tushare",
                }
            )
        return items


def _number(value: Any) -> float:
    try:
        number = float(value)
        return 0.0 if number != number else number
    except (TypeError, ValueError):
        return 0.0
