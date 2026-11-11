"""Editable research plan drafts built from the current Chan structure.

The structure is computed from the bars in the database at request time (about
half a second), so the draft always matches the latest data. The draft only
restates structure facts as conditions to check later and is never saved by
itself; the user edits and saves it like any other plan.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Instrument, TradingCalendar
from backend.app.services.chan_analysis import DatabaseChanEngine

REVIEW_AFTER_SESSIONS = 5
SIGNAL_LABELS = {"1": "一类买点", "1p": "盘整背驰买点", "2": "二类买点", "2s": "类二类买点", "3a": "三类买点 A", "3b": "三类买点 B"}
THIRD_BUY_TYPES = {"3a", "3b"}


def _day(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _price(value: Any) -> str:
    return f"{float(value):.2f}"


def review_date_after(session: Session, start: date, sessions: int = REVIEW_AFTER_SESSIONS) -> date:
    """The n-th open session after start; weekdays fill in where the calendar is not loaded yet."""
    known = list(session.scalars(
        select(TradingCalendar.trade_date)
        .where(TradingCalendar.is_open.is_(True), TradingCalendar.trade_date > start)
        .order_by(TradingCalendar.trade_date)
        .limit(sessions)
    ))
    if len(known) >= sessions:
        return known[sessions - 1]
    day = known[-1] if known else start
    remaining = sessions - len(known)
    while remaining:
        day += timedelta(days=1)
        if day.weekday() < 5:
            remaining -= 1
    return day


class ResearchPlanDraftService:
    def __init__(self, session: Session, engine: Any | None = None):
        self.session = session
        self.engine = engine or DatabaseChanEngine(session)

    def build(self, instrument_id: int) -> dict[str, Any] | None:
        instrument = self.session.get(Instrument, instrument_id)
        if instrument is None:
            return None
        try:
            structure = self.engine.analyze(instrument.code)
        except ValueError:
            return None
        structure_date = _day(structure.get("end_date"))
        if structure_date is None:
            return None
        review = review_date_after(self.session, structure_date)
        draft: dict[str, Any] = self._draft(structure, review)
        draft["next_review_date"] = review.isoformat()
        draft["basis"] = {
            "structure_date": structure_date.isoformat(),
            "algorithm_version": getattr(self.engine, "algorithm_version", None),
            "history_sufficient": bool(structure.get("history_sufficient", True)),
            "analysis_bar_count": int(structure.get("analysis_bar_count") or structure.get("kline_count") or 0),
        }
        return draft

    def _draft(self, structure: dict[str, Any], review: date) -> dict[str, str]:
        current = float(structure["current_price"]) if structure.get("current_price") is not None else None
        zs = (structure.get("zs_list") or [None])[-1]
        bi = (structure.get("bi_list") or [None])[-1]
        buy = self._latest(structure.get("buy_signals"))
        sell = self._latest(structure.get("sell_signals"))
        buy_is_latest = bool(buy and (not sell or (_day(buy.get("date")) or date.min) >= (_day(sell.get("date")) or date.min)))
        broken = bool(buy and buy_is_latest and current is not None and current < float(buy["price"]))
        active = buy if buy_is_latest and not broken else None

        facts: list[str] = []
        if buy:
            label = SIGNAL_LABELS.get(str(buy.get("type")), "买点")
            if not buy_is_latest:
                facts.append(f"最近的日线买点是 {_day(buy.get('date'))} 的{label}，之后 {_day(sell.get('date')) if sell else '—'} 出现了卖点")
            elif broken:
                facts.append(f"{_day(buy.get('date'))} 出现日线{label}（{_price(buy['price'])}），当前价已低于买点价格")
            else:
                facts.append(f"{_day(buy.get('date'))} 出现日线{label}（{_price(buy['price'])}）")
        else:
            facts.append("近期没有日线买点")
        position = None
        if zs and current is not None:
            high, low = float(zs["high"]), float(zs["low"])
            position = "above" if current > high else "below" if current < low else "inside"
            where = {"above": "上方", "inside": "之内", "below": "下方"}[position]
            facts.append(
                f"当前价 {_price(current)} 在最近中枢 {_price(low)}–{_price(high)}"
                f"（{_day(zs.get('start_date'))} 至 {_day(zs.get('end_date'))}）{where}"
            )
        if bi:
            up = float(bi["end_price"]) >= float(bi["start_price"])
            facts.append(
                f"最后一笔向{'上' if up else '下'}（{_price(bi['start_price'])} → {_price(bi['end_price'])}，"
                f"{'已确认' if bi.get('is_sure') else '尚未确认'}）"
            )

        bi_low = min(float(bi["start_price"]), float(bi["end_price"])) if bi else None
        prior_low = bi_low if bi_low is not None and (current is None or bi_low < current) else None
        if zs and position == "above":
            confirmation = f"回抽不跌回中枢上沿 {_price(zs['high'])} 之下，并出现三类买点"
        elif zs and position == "inside":
            confirmation = f"收盘向上突破中枢上沿 {_price(zs['high'])}，且回抽不再回到中枢内"
        elif zs and position == "below":
            confirmation = f"重新站上中枢下沿 {_price(zs['low'])}"
            if prior_low is not None:
                confirmation = f"回调不破前低 {_price(prior_low)} 形成二类买点，或{confirmation}"
        else:
            confirmation = "形成第一个日线中枢后再判断方向"

        if active and str(active.get("type")) in THIRD_BUY_TYPES and zs:
            invalidation = f"收盘跌回中枢上沿 {_price(zs['high'])} 之下，三类买点不成立"
        elif active:
            invalidation = f"收盘跌破买点价格 {_price(active['price'])}"
        elif zs and position == "above":
            invalidation = f"收盘跌回中枢上沿 {_price(zs['high'])} 之下"
        elif zs and position == "inside":
            invalidation = f"收盘跌破中枢下沿 {_price(zs['low'])}"
        elif prior_low is not None:
            invalidation = f"收盘跌破前低 {_price(prior_low)}"
        elif current is not None:
            invalidation = f"收盘继续创出新低（低于 {_price(current)}）"
        else:
            invalidation = ""

        return {
            "thesis": "；".join(facts) + "。",
            "confirmation_trigger": confirmation,
            "invalidation_condition": invalidation,
            "next_action": f"{review.isoformat()} 复盘：确认条件是否出现、是否触发失效，成交量是否配合",
        }

    @staticmethod
    def _latest(signals: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        dated = [signal for signal in signals or [] if _day(signal.get("date")) and signal.get("price") is not None]
        return max(dated, key=lambda signal: _day(signal.get("date")) or date.min) if dated else None
