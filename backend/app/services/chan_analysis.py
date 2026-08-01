from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.chan_core.engine import ChanBar, PureChanEngine
from backend.app.chan_core.protocols import ProgressCallback, ScanBatchOutcome, ScanSubjectOutcome
from backend.app.db.models import Instrument
from backend.app.services.bar_access import load_daily_bars


class DatabaseChanEngine:
    """Application adapter: load v2 bars, then invoke the I/O-free Chan core."""

    def __init__(self, session: Session, core: PureChanEngine | None = None):
        self.session = session
        self.core = core or PureChanEngine()
        self.algorithm_version = self.core.algorithm_version

    def analyze(self, code: str) -> dict[str, Any]:
        instrument = self.session.scalar(select(Instrument).where(Instrument.code == code))
        if instrument is None:
            raise ValueError(f"股票 {code} 不存在")
        bars, adjustment = self._bars_with_adjustment(instrument.id)
        analysis_adjustment = adjustment
        analysis = self.core.analyze_bars(code, bars)
        analysis["name"] = instrument.name or code
        analysis["adjustment"] = analysis_adjustment
        return analysis

    def scan(
        self,
        *,
        stock_codes: list[str],
        buy_types: list[str],
        sell_types: list[str],
        progress_callback: ProgressCallback | None = None,
        industries: list[str] | None = None,
        areas: list[str] | None = None,
        exclude_st: bool = True,
    ) -> ScanBatchOutcome:
        requested = set(stock_codes)
        statement = select(Instrument).where(Instrument.code.in_(requested))
        instruments = {item.code: item for item in self.session.scalars(statement).all()}
        results: list[dict[str, Any]] = []
        outcomes: list[ScanSubjectOutcome] = []
        total = len(stock_codes)
        cutoff = date.today() - timedelta(days=30)

        for index, code in enumerate(stock_codes, start=1):
            instrument = instruments.get(code)
            if instrument is None:
                outcomes.append(ScanSubjectOutcome(code=code, status="failed", error="股票主数据不存在"))
            elif not self._eligible(instrument, industries, areas, exclude_st):
                outcomes.append(ScanSubjectOutcome(code=code, status="filtered"))
            else:
                try:
                    bars, adjustment = self._bars_with_adjustment(instrument.id)
                    analysis = self.core.analyze_bars(code, bars)
                    signals = []
                    for signal in analysis["buy_signals"] + analysis["sell_signals"]:
                        signal_date = datetime.strptime(str(signal["date"])[:10], "%Y/%m/%d").date()
                        wanted = signal["type"] in (buy_types if signal["is_buy"] else sell_types)
                        if wanted and signal_date >= cutoff:
                            signals.append(
                                {
                                    "type": signal["type"],
                                    "is_buy": signal["is_buy"],
                                    "direction": "买入" if signal["is_buy"] else "卖出",
                                    "date": signal_date.isoformat(),
                                    "price": float(signal["price"]),
                                    "period": "日线",
                                }
                            )
                    result = None
                    if signals:
                        latest = bars[-1]
                        previous = bars[-2] if len(bars) > 1 else None
                        change = (
                            (latest.close - previous.close) / previous.close * 100
                            if previous and previous.close
                            else 0.0
                        )
                        result = {
                            "code": code,
                            "name": instrument.name or code,
                            "industry": instrument.industry.name if instrument.industry else "",
                            "area": instrument.area or "",
                            "signals": signals,
                            "latest_price": latest.close,
                            "change_pct": change,
                            "adjustment": adjustment,
                            "algorithm_version": self.core.algorithm_version,
                        }
                        results.append(result)
                    outcomes.append(
                        ScanSubjectOutcome(
                            code=code,
                            status="matched" if result else "no_signal",
                            result=result,
                        )
                    )
                except (ValueError, RuntimeError) as exc:
                    outcomes.append(ScanSubjectOutcome(code=code, status="failed", error=str(exc)))
            if progress_callback:
                progress_callback(index, total, len(results))
        return ScanBatchOutcome(matches=results, subjects=outcomes)

    def _bars(self, instrument_id: int) -> list[ChanBar]:
        return self._bars_with_adjustment(instrument_id)[0]

    def _bars_with_adjustment(self, instrument_id: int) -> tuple[list[ChanBar], str]:
        start = datetime.combine(date.today() - timedelta(days=1825), datetime.min.time())
        rows, adjustment = load_daily_bars(
            self.session, instrument_id, start=start, quality_only=True
        )
        bars = [
            ChanBar(
                bar_time=row.bar_time,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
                amount=row.amount,
                turnover_rate=row.turnover_rate,
            )
            for row in rows
        ]
        return bars, adjustment

    @staticmethod
    def _eligible(
        instrument: Instrument,
        industries: list[str] | None,
        areas: list[str] | None,
        exclude_st: bool,
    ) -> bool:
        if exclude_st and "ST" in (instrument.name or "").upper():
            return False
        if industries and (instrument.industry is None or instrument.industry.name not in industries):
            return False
        if areas and (instrument.area or "") not in areas:
            return False
        return True
