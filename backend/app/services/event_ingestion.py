from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import sanitize_text
from backend.app.db.models import Instrument, IpoEvent, LimitUpEvent
from backend.app.db.models.common import utcnow

LimitUpFetcher = Callable[[str], Any]
IpoFetcher = Callable[[date, date], Any]


def _akshare_limit_up_fetcher(trade_date: str) -> Any:
    import akshare as ak

    return ak.stock_zt_pool_em(date=trade_date)


def build_ipo_fetcher(tushare_token: str | None = None) -> IpoFetcher:
    """Build the IPO provider chain used by scheduled refresh jobs."""

    def fetch(start_date: date, end_date: date) -> tuple[Any, str]:
        errors: list[str] = []
        # Tushare filters new_share by subscription date, not listing date.
        query_start = start_date - timedelta(days=45)
        query_end = end_date + timedelta(days=45)
        if tushare_token:
            try:
                import tushare as ts

                frame = ts.pro_api(tushare_token).new_share(
                    start_date=query_start.strftime("%Y%m%d"),
                    end_date=query_end.strftime("%Y%m%d"),
                )
                if frame is not None and not getattr(frame, "empty", False):
                    return frame, "tushare.new_share"
                errors.append("tushare.new_share returned no rows")
            except Exception as exc:
                errors.append(f"tushare.new_share: {sanitize_text(exc, limit=240)}")
        try:
            import akshare as ak

            frame = ak.stock_xgsglb_em()
            if frame is not None and not getattr(frame, "empty", False):
                return frame, "akshare.eastmoney_ipo"
            errors.append("akshare.eastmoney_ipo returned no rows")
        except Exception as exc:
            errors.append(f"akshare.eastmoney_ipo: {sanitize_text(exc, limit=240)}")
        raise RuntimeError("; ".join(errors) or "IPO providers returned no rows")

    return fetch


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _number(value: Any, cast: Callable[[Any], Any]) -> Any | None:
    value = _clean(value)
    if value in (None, ""):
        return None
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    value = _clean(value)
    if value in (None, "", "NaT"):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    for pattern, length in (("%Y%m%d", 8), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:length], pattern).date()
        except ValueError:
            continue
    return None


class EventIngestionService:
    def __init__(
        self,
        session: Session,
        limit_up_fetcher: LimitUpFetcher | None = None,
        ipo_fetcher: IpoFetcher | None = None,
        tushare_token: str | None = None,
    ):
        self.session = session
        self.limit_up_fetcher = limit_up_fetcher or _akshare_limit_up_fetcher
        self.ipo_fetcher = ipo_fetcher or build_ipo_fetcher(tushare_token)

    def refresh_limit_ups(self, dates: Iterable[date], max_attempts: int = 3) -> dict[str, Any]:
        normalized_dates = sorted(set(dates))
        instruments = {
            item.code: item
            for item in self.session.scalars(select(Instrument).where(Instrument.code.is_not(None))).all()
        }
        written = 0
        errors: list[dict[str, str]] = []
        completed_dates: list[str] = []
        for trade_date in normalized_dates:
            date_key = trade_date.strftime("%Y%m%d")
            frame = None
            last_error: Exception | None = None
            for attempt in range(1, max(1, max_attempts) + 1):
                try:
                    frame = self.limit_up_fetcher(date_key)
                    if frame is None or getattr(frame, "empty", False):
                        raise RuntimeError("涨停池返回空数据")
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max_attempts:
                        time.sleep(float(attempt))
            if frame is None or last_error is not None and getattr(frame, "empty", False):
                errors.append({"trade_date": trade_date.isoformat(), "error": sanitize_text(last_error, limit=500)})
                continue
            try:
                records = frame.to_dict("records")
                if not records:
                    raise RuntimeError("涨停池返回空数据")
                for source_row in records:
                    row = {str(key): _clean(value) for key, value in dict(source_row).items()}
                    code = str(row.get("代码") or row.get("code") or "").split(".")[0].zfill(6)
                    if not code.strip("0"):
                        continue
                    instrument = instruments.get(code)
                    event = self.session.scalar(
                        select(LimitUpEvent).where(
                            LimitUpEvent.trade_date == trade_date,
                            LimitUpEvent.legacy_code == code,
                        )
                    )
                    if event is None:
                        event = LimitUpEvent(legacy_code=code, trade_date=trade_date)
                        self.session.add(event)
                    event.instrument_id = instrument.id if instrument else None
                    event.market = instrument.exchange if instrument else None
                    event.theme = str(row.get("所属行业") or "").strip() or None
                    event.close = _number(row.get("最新价"), float)
                    event.pct_change = _number(row.get("涨跌幅"), float)
                    event.turnover_rate = _number(row.get("换手率"), float)
                    event.consecutive_boards = _number(row.get("连板数"), int) or 1
                    event.raw_payload = {
                        **row,
                        "source": "akshare.eastmoney_limit_up_pool",
                        "name": row.get("名称"),
                        "industry": row.get("所属行业"),
                        "amount": row.get("成交额"),
                        "limit_order": row.get("封板资金"),
                        "first_limit_time": row.get("首次封板时间"),
                        "last_limit_time": row.get("最后封板时间"),
                        "break_count": row.get("炸板次数"),
                        "limit_up_stat": row.get("涨停统计"),
                    }
                    event.fetched_at = utcnow()
                    event.quarantined = instrument is None
                    written += 1
                self.session.commit()
                completed_dates.append(trade_date.isoformat())
            except Exception as exc:
                self.session.rollback()
                errors.append({"trade_date": trade_date.isoformat(), "error": sanitize_text(exc, limit=500)})
        status = "completed" if not errors else "partial" if completed_dates else "failed"
        return {
            "status": status,
            "source": "akshare.eastmoney_limit_up_pool",
            "dates": completed_dates,
            "total": len(normalized_dates),
            "succeeded": len(completed_dates),
            "failed": len(errors),
            "rows_written": written,
            "errors": errors,
            "coverage_rate": len(completed_dates) / len(normalized_dates) if normalized_dates else 0.0,
        }
    def refresh_ipos(
        self,
        start_date: date,
        end_date: date,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        if start_date > end_date:
            raise ValueError("IPO 刷新开始日期不能晚于结束日期")
        frame = None
        source = "unknown"
        last_error: Exception | None = None
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                fetched = self.ipo_fetcher(start_date, end_date)
                if isinstance(fetched, tuple) and len(fetched) == 2:
                    frame, source = fetched
                else:
                    frame, source = fetched, "injected"
                if frame is None or getattr(frame, "empty", False):
                    raise RuntimeError("IPO 数据源返回空数据")
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt < max_attempts:
                    time.sleep(float(attempt))
        if frame is None or last_error is not None:
            error = sanitize_text(last_error, limit=500)
            return {
                "status": "failed",
                "source": source,
                "range_start": start_date.isoformat(),
                "range_end": end_date.isoformat(),
                "total": 1,
                "succeeded": 0,
                "failed": 1,
                "rows_written": 0,
                "errors": [{"range": f"{start_date}/{end_date}", "error": error}],
                "coverage_rate": 0.0,
            }

        instruments = {
            item.code: item
            for item in self.session.scalars(select(Instrument).where(Instrument.code.is_not(None))).all()
        }
        written = 0
        skipped_without_listing_date = 0
        skipped_outside_range = 0
        fetched_at = utcnow()
        try:
            for source_row in frame.to_dict("records"):
                row = {str(key): _clean(value) for key, value in dict(source_row).items()}
                code = str(
                    row.get("ts_code")
                    or row.get("股票代码")
                    or row.get("code")
                    or row.get("证券代码")
                    or ""
                ).split(".")[0].zfill(6)
                listing_date = _date(
                    row.get("issue_date")
                    or row.get("list_date")
                    or row.get("上市日期")
                    or row.get("listing_date")
                )
                if not code.strip("0") or listing_date is None:
                    skipped_without_listing_date += 1
                    continue
                if not start_date <= listing_date <= end_date:
                    skipped_outside_range += 1
                    continue
                instrument = instruments.get(code)
                event = self.session.scalar(
                    select(IpoEvent).where(
                        IpoEvent.listing_date == listing_date,
                        IpoEvent.legacy_code == code,
                    )
                )
                if event is None:
                    event = IpoEvent(legacy_code=code, listing_date=listing_date)
                    self.session.add(event)
                event.instrument_id = instrument.id if instrument else None
                event.issue_price = _number(
                    row.get("price") or row.get("发行价格") or row.get("ipo_price"),
                    float,
                )
                event.issue_pe = _number(
                    row.get("pe") or row.get("发行市盈率") or row.get("ipo_pe"),
                    float,
                )
                subscription_date = _date(row.get("ipo_date") or row.get("申购日期"))
                event.raw_payload = {
                    **row,
                    "source": source,
                    "name": row.get("name")
                    or row.get("股票简称")
                    or (instrument.name if instrument else None),
                    "ts_code": row.get("ts_code")
                    or (instrument.ts_code if instrument else None),
                    "listing_date": listing_date.isoformat(),
                    "subscription_date": subscription_date.isoformat() if subscription_date else None,
                    "industry": row.get("industry")
                    or row.get("所属行业")
                    or (instrument.industry.name if instrument and instrument.industry else None),
                    "ipo_price": event.issue_price,
                    "ipo_pe": event.issue_pe,
                }
                event.fetched_at = fetched_at
                written += 1
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            error = sanitize_text(exc, limit=500)
            return {
                "status": "failed",
                "source": source,
                "range_start": start_date.isoformat(),
                "range_end": end_date.isoformat(),
                "total": 1,
                "succeeded": 0,
                "failed": 1,
                "rows_written": 0,
                "errors": [{"range": f"{start_date}/{end_date}", "error": error}],
                "coverage_rate": 0.0,
            }
        return {
            "status": "completed",
            "source": source,
            "range_start": start_date.isoformat(),
            "range_end": end_date.isoformat(),
            "total": 1,
            "succeeded": 1,
            "failed": 0,
            "rows_written": written,
            "skipped_without_listing_date": skipped_without_listing_date,
            "skipped_outside_range": skipped_outside_range,
            "data_time": fetched_at.isoformat(),
            "errors": [],
            "coverage_rate": 1.0,
        }
