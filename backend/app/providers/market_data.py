from __future__ import annotations

import io
import math
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from backend.app.core.errors import sanitize_text


@dataclass(frozen=True, slots=True)
class MarketBar:
    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    turnover_rate: float | None = None
    raw_volume: float | None = None
    raw_amount: float | None = None


@dataclass(frozen=True, slots=True)
class CalendarSession:
    trade_date: date
    is_open: bool


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    code: str
    ts_code: str
    exchange: str
    name: str
    area: str | None = None
    industry: str | None = None
    list_date: date | None = None
    status: str = "active"
    asset_type: str = "stock"


class HistoricalMarketDataProvider(Protocol):
    key: str
    name: str

    def fetch_bars(
        self,
        *,
        ts_code: str,
        timeframe: str,
        adjustment: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBar]: ...

    def fetch_calendar(self, *, start_date: date, end_date: date) -> list[CalendarSession]: ...

    def fetch_instruments(self) -> list[InstrumentRecord]: ...


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)

    except (TypeError, ValueError):
        return None
    return None if number != number else number


def _optional_date(value: Any) -> date | None:
    text = str(value or "").replace("-", "")[:8]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _is_non_retryable_provider_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "频率超限",
            "token不对",
            "没有权限",
            "permission",
            "rate limit",
            "too many requests",
            "unauthorized",
            "forbidden",
            "no module named",
        )
    )


class TushareMarketDataProvider:
    """Tushare adapter returning v2 DTOs without importing legacy database code."""

    key = "tushare"
    name = "Tushare"

    def __init__(
        self,
        token: str | None = None,
        *,
        client: Any | None = None,
        min_interval_seconds: float = 0.21,
        max_attempts: int = 3,
    ):
        self.token = token or os.getenv("TUSHARE_TOKEN")
        self._client = client
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.max_attempts = max(1, max_attempts)
        self._last_request_at = 0.0

    @property
    def client(self) -> Any:
        if self._client is None:
            if not self.token:
                raise RuntimeError("未配置 TUSHARE_TOKEN，无法执行 v2 原生行情刷新")
            import tushare as ts

            self._client = ts.pro_api(self.token)
        return self._client

    def _throttle(self) -> None:
        remaining = self.min_interval_seconds - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _request(self, callback):
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            self._throttle()
            try:
                result = callback()
                self._last_request_at = time.monotonic()
                return result
            except Exception as exc:
                self._last_request_at = time.monotonic()
                last_error = exc
                if _is_non_retryable_provider_error(exc):
                    break
                if attempt < self.max_attempts:
                    time.sleep(min(4.0, float(2 ** (attempt - 1))))
        assert last_error is not None
        raise last_error

    def _request_with_diagnostics(self, callback: Any) -> Any:
        diagnostics = io.StringIO()
        with redirect_stdout(diagnostics), redirect_stderr(diagnostics):
            result = self._request(callback)
        diagnostic_text = diagnostics.getvalue().strip()
        if diagnostic_text and _is_non_retryable_provider_error(RuntimeError(diagnostic_text)):
            raise RuntimeError(sanitize_text(diagnostic_text, limit=500))
        return result

    def fetch_instruments(self) -> list[InstrumentRecord]:
        frame = self._request_with_diagnostics(
            lambda: self.client.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,symbol,name,area,industry,list_date,list_status",
            )
        )
        if frame is None or frame.empty:
            return []
        items: list[InstrumentRecord] = []
        for row in frame.to_dict("records"):
            code = str(row.get("symbol") or "").zfill(6)
            ts_code = str(row.get("ts_code") or "")
            exchange = ts_code.split(".", 1)[1].upper() if "." in ts_code else _exchange_for_code(code)
            items.append(
                InstrumentRecord(
                    code=code,
                    ts_code=f"{code}.{exchange}",
                    exchange=exchange,
                    name=str(row.get("name") or "").strip(),
                    area=str(row.get("area") or "").strip() or None,
                    industry=str(row.get("industry") or "").strip() or None,
                    list_date=_optional_date(row.get("list_date")),
                    status="active" if str(row.get("list_status") or "L") == "L" else "inactive",
                )
            )
        return items

    def fetch_bars(
        self,
        *,
        ts_code: str,
        timeframe: str,
        adjustment: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBar]:
        if timeframe.upper() != "DAY":
            raise ValueError(f"Tushare v2 原生刷新当前只支持 DAY，收到 {timeframe}")
        adjustment_key = adjustment.upper()
        if adjustment_key not in {"QFQ", "HFQ", "NONE"}:
            raise ValueError(f"不支持的复权方式：{adjustment}")

        import tushare as ts

        frame = self._request_with_diagnostics(
            lambda: ts.pro_bar(
                api=self.client,
                ts_code=ts_code,
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adj=None if adjustment_key == "NONE" else adjustment_key.lower(),
                asset="E",
                freq="D",
            )
        )
        if frame is None or frame.empty:
            return []
        bars: list[MarketBar] = []
        for record in frame.to_dict("records"):
            raw_date = str(record.get("trade_date") or record.get("date") or "")
            if not raw_date:
                continue
            bar_time = datetime.strptime(raw_date.replace("-", "")[:8], "%Y%m%d")
            raw_volume = float(record.get("vol") or record.get("volume") or 0)
            raw_amount = _optional_float(record.get("amount"))
            bars.append(
                MarketBar(
                    bar_time=bar_time,
                    open=float(record["open"]),
                    high=float(record["high"]),
                    low=float(record["low"]),
                    close=float(record["close"]),
                    volume=raw_volume * 100.0,
                    amount=raw_amount * 1000.0 if raw_amount is not None else None,
                    turnover_rate=_optional_float(record.get("turnover_rate")),
                    raw_volume=raw_volume,
                    raw_amount=raw_amount,
                )
            )
        bars.sort(key=lambda item: item.bar_time)
        return bars

    def fetch_calendar(self, *, start_date: date, end_date: date) -> list[CalendarSession]:
        frame = self._request_with_diagnostics(
            lambda: self.client.trade_cal(
                exchange="",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                fields="cal_date,is_open",
            )
        )
        if frame is None or frame.empty:
            return []
        sessions = [
            CalendarSession(
                trade_date=datetime.strptime(str(record["cal_date"]), "%Y%m%d").date(),
                is_open=str(record["is_open"]) == "1",
            )
            for record in frame.to_dict("records")
        ]
        sessions.sort(key=lambda item: item.trade_date)
        return sessions


def _build_tushare_only_provider(token: str | None = None) -> HistoricalMarketDataProvider:
    # Pydantic loads values from .env without mutating os.environ. Resolve the
    # fallback through v2 settings, while allowing a database secret to win.
    if token is None:
        from backend.app.core.config import get_settings

        secret = get_settings().tushare_token
        token = secret.get_secret_value() if secret else None
    return TushareMarketDataProvider(token=token)


class MarketDataContractError(ValueError):
    """A provider response violates the ChanAnalyzer market-data contract."""


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: str
    status: str
    error: str | None = None
    row_count: int = 0
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "error": self.error,
            "row_count": self.row_count,
            "latency_ms": self.latency_ms,
        }


def validate_market_bars(bars: list[MarketBar], *, start_date: date, end_date: date) -> list[MarketBar]:
    if not bars:
        raise MarketDataContractError("provider returned no daily bars")
    previous: datetime | None = None
    seen: set[datetime] = set()
    for index, bar in enumerate(bars):
        numbers = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(value) for value in numbers):
            raise MarketDataContractError(f"row {index} contains a non-finite required value")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise MarketDataContractError(f"row {index} contains a non-positive price")
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            raise MarketDataContractError(f"row {index} has invalid OHLC bounds")
        if bar.volume < 0:
            raise MarketDataContractError(f"row {index} has negative volume")
        if bar.amount is not None and not math.isfinite(bar.amount):
            raise MarketDataContractError(f"row {index} has invalid amount")
        if bar.turnover_rate is not None and not math.isfinite(bar.turnover_rate):
            raise MarketDataContractError(f"row {index} has invalid turnover rate")
        if not start_date <= bar.bar_time.date() <= end_date:
            raise MarketDataContractError(f"row {index} is outside the requested date range")
        if bar.bar_time in seen:
            raise MarketDataContractError(f"duplicate trading date: {bar.bar_time.date()}")
        if previous is not None and bar.bar_time <= previous:
            raise MarketDataContractError("daily bars are not strictly ordered")
        previous = bar.bar_time
        seen.add(bar.bar_time)
    return bars


def validate_calendar(
    sessions: list[CalendarSession], *, start_date: date, end_date: date
) -> list[CalendarSession]:
    if not sessions:
        raise MarketDataContractError("provider returned no trading calendar")
    dates = [item.trade_date for item in sessions]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise MarketDataContractError("trading calendar dates must be unique and ordered")
    if any(item < start_date or item > end_date for item in dates):
        raise MarketDataContractError("trading calendar contains dates outside the requested range")
    return sessions


def validate_instruments(items: list[InstrumentRecord]) -> list[InstrumentRecord]:
    if not items:
        raise MarketDataContractError("provider returned no instrument master data")
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not item.code.isdigit() or len(item.code) != 6:
            raise MarketDataContractError(f"instrument row {index} has invalid code")
        if not item.name.strip():
            raise MarketDataContractError(f"instrument row {index} has no name")
        if item.exchange not in {"SH", "SZ", "BJ"}:
            raise MarketDataContractError(f"instrument row {index} has invalid exchange")
        if item.ts_code != f"{item.code}.{item.exchange}":
            raise MarketDataContractError(f"instrument row {index} has inconsistent ts_code")
        if item.code in seen:
            raise MarketDataContractError(f"duplicate instrument code: {item.code}")
        seen.add(item.code)
    return sorted(items, key=lambda item: item.code)


def _exchange_for_code(code: str) -> str:
    return "BJ" if code.startswith(("4", "8", "920")) else "SH" if code.startswith(("5", "6", "9")) else "SZ"


class BaoStockMarketDataProvider:
    key = "baostock"
    name = "BaoStock"

    def __init__(self) -> None:
        self._client: Any | None = None
        self._logged_in = False

    def _ensure_login(self) -> Any:
        if self._logged_in and self._client is not None:
            return self._client
        import baostock as bs

        login = bs.login()
        if getattr(login, "error_code", "0") != "0":
            raise RuntimeError(getattr(login, "error_msg", "BaoStock login failed"))
        self._client = bs
        self._logged_in = True
        return bs

    def close(self) -> None:
        if not self._logged_in or self._client is None:
            return
        try:
            self._client.logout()
        finally:
            self._client = None
            self._logged_in = False

    def fetch_instruments(self) -> list[InstrumentRecord]:
        raise NotImplementedError("BaoStock is not used for instrument master data")

    def fetch_bars(
        self, *, ts_code: str, timeframe: str, adjustment: str, start_date: date, end_date: date
    ) -> list[MarketBar]:
        if timeframe.upper() != "DAY":
            raise ValueError("BaoStock only supports DAY in this adapter")
        bs = self._ensure_login()
        code, exchange = ts_code.split(".", 1)
        if exchange.upper() not in {"SH", "SZ"}:
            raise ValueError(f"BaoStock does not support exchange: {exchange}")
        symbol = f"{'sh' if exchange.upper() == 'SH' else 'sz'}.{code}"
        adjust_flag = {"NONE": "3", "QFQ": "2", "HFQ": "1"}.get(adjustment.upper())
        if adjust_flag is None:
            raise ValueError(f"unsupported adjustment: {adjustment}")
        result = bs.query_history_k_data_plus(
            symbol,
            "date,open,high,low,close,volume,amount,turn",
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            frequency="d",
            adjustflag=adjust_flag,
        )
        if result.error_code != "0":
            raise RuntimeError(result.error_msg)
        bars: list[MarketBar] = []
        while result.next():
            row = result.get_row_data()
            raw_volume = float(row[5] or 0)
            raw_amount = _optional_float(row[6])
            bars.append(
                MarketBar(
                    bar_time=datetime.strptime(row[0], "%Y-%m-%d"),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=raw_volume,
                    amount=raw_amount,
                    turnover_rate=_optional_float(row[7]),
                )
            )
        return bars

    def fetch_calendar(self, *, start_date: date, end_date: date) -> list[CalendarSession]:
        bs = self._ensure_login()
        result = bs.query_trade_dates(
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
        )
        if getattr(result, "error_code", "0") != "0":
            raise RuntimeError(getattr(result, "error_msg", "BaoStock calendar query failed"))
        fields = {name: index for index, name in enumerate(result.fields)}
        sessions: list[CalendarSession] = []
        while result.next():
            row = result.get_row_data()
            sessions.append(
                CalendarSession(
                    trade_date=datetime.strptime(row[fields["calendar_date"]], "%Y-%m-%d").date(),
                    is_open=str(row[fields["is_trading_day"]]) == "1",
                )
            )
        return sessions


class AkShareMarketDataProvider:
    def __init__(self) -> None:
        self._last_eastmoney_request_at = 0.0

    key = "akshare"
    name = "AKShare"

    def fetch_instruments(self) -> list[InstrumentRecord]:
        import akshare as ak

        frame = ak.stock_info_a_code_name()
        if frame is None or frame.empty:
            return []
        items: list[InstrumentRecord] = []
        for row in frame.to_dict("records"):
            code = str(row.get("code") or row.get("证券代码") or "").zfill(6)
            exchange = _exchange_for_code(code)
            name = str(row.get("name") or row.get("证券简称") or "").strip()
            items.append(
                InstrumentRecord(
                    code=code,
                    ts_code=f"{code}.{exchange}",
                    exchange=exchange,
                    name=name,
                    list_date=date.today() if name.upper().startswith("N") else None,
                )
            )
        return items

    def fetch_bars(
        self, *, ts_code: str, timeframe: str, adjustment: str, start_date: date, end_date: date
    ) -> list[MarketBar]:
        if timeframe.upper() != "DAY":
            raise ValueError("AKShare only supports DAY in this adapter")
        adjustment_key = adjustment.upper()
        if adjustment_key not in {"QFQ", "HFQ", "NONE"}:
            raise ValueError(f"unsupported adjustment: {adjustment}")
        import akshare as ak

        primary_error: Exception | None = None
        try:
            frame = ak.stock_zh_a_hist(
                symbol=ts_code.split(".", 1)[0],
                period="daily",
                start_date=start_date.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="" if adjustment_key == "NONE" else adjustment_key.lower(),
            )
            if frame is not None and not frame.empty:
                bars: list[MarketBar] = []
                for row in frame.to_dict("records"):
                    raw_volume = float(row.get("成交量") or 0)
                    raw_amount = _optional_float(row.get("成交额"))
                    bars.append(MarketBar(
                        bar_time=datetime.strptime(str(row["日期"])[:10], "%Y-%m-%d"),
                        open=float(row["开盘"]),
                        high=float(row["最高"]),
                        low=float(row["最低"]),
                        close=float(row["收盘"]),
                        volume=raw_volume * 100.0,
                        amount=raw_amount,
                        turnover_rate=_optional_float(row.get("换手率")),
                        raw_volume=raw_volume,
                        raw_amount=raw_amount,
                    ))
                return bars
        except Exception as exc:
            primary_error = exc
        try:
            return self._fetch_eastmoney_http(ts_code, adjustment_key, start_date, end_date)
        except Exception as fallback_error:
            raise RuntimeError(
                f"AKShare HTTPS failed ({sanitize_text(primary_error, limit=200)}); "
                f"Eastmoney HTTP fallback failed ({sanitize_text(fallback_error, limit=200)})"
            ) from fallback_error

    def _fetch_eastmoney_http(
        self,
        ts_code: str,
        adjustment: str,
        start_date: date,
        end_date: date,
    ) -> list[MarketBar]:
        import requests

        code, exchange = ts_code.split(".", 1)
        market_id = "1" if exchange.upper() == "SH" else "0"
        params = {
            "secid": f"{market_id}.{code}",
            "klt": "101",
            "fqt": {"NONE": "0", "QFQ": "1", "HFQ": "2"}[adjustment],
            "beg": start_date.strftime("%Y%m%d"),
            "end": end_date.strftime("%Y%m%d"),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        last_error: Exception | None = None
        lines: list[str] = []
        for attempt in range(1, 4):
            remaining = 0.8 - (time.monotonic() - self._last_eastmoney_request_at)
            if remaining > 0:
                time.sleep(remaining)
            try:
                response = requests.get(
                    "http://push2his.eastmoney.com/api/qt/stock/kline/get",
                    params=params,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"},
                    timeout=20,
                )
                self._last_eastmoney_request_at = time.monotonic()
                response.raise_for_status()
                payload = response.json()
                lines = ((payload.get("data") or {}).get("klines") or [])
                if lines:
                    break
                last_error = RuntimeError("Eastmoney returned no kline data")
            except Exception as exc:
                self._last_eastmoney_request_at = time.monotonic()
                last_error = exc
            if attempt < 3:
                time.sleep(float(attempt * 2))
        if not lines:
            raise RuntimeError(sanitize_text(last_error, limit=300))
        bars: list[MarketBar] = []
        for line in lines:
            fields = str(line).split(",")
            if len(fields) < 11:
                continue
            raw_volume = float(fields[5] or 0)
            raw_amount = _optional_float(fields[6])
            bars.append(
                MarketBar(
                    bar_time=datetime.strptime(fields[0], "%Y-%m-%d"),
                    open=float(fields[1]),
                    close=float(fields[2]),
                    high=float(fields[3]),
                    low=float(fields[4]),
                    volume=raw_volume * 100.0,
                    amount=raw_amount,
                    turnover_rate=_optional_float(fields[10]),
                    raw_volume=raw_volume,
                    raw_amount=raw_amount,
                )
            )
        return bars

    def fetch_calendar(self, *, start_date: date, end_date: date) -> list[CalendarSession]:
        raise NotImplementedError("AKShare is not used to infer the trading calendar")


class FallbackMarketDataProvider(TushareMarketDataProvider):
    key = "daily-provider-chain"
    name = "Tushare -> BaoStock -> AKShare"

    def __init__(
        self,
        bar_providers: list[HistoricalMarketDataProvider],
        calendar_providers: list[HistoricalMarketDataProvider],
        instrument_providers: list[HistoricalMarketDataProvider] | None = None,
    ):
        self.token = getattr(bar_providers[0], "token", None) if bar_providers else None
        self.instrument_providers = instrument_providers or [
            provider for provider in bar_providers if provider.key in {"tushare", "akshare"}
        ]
        self.last_instrument_attempts: list[ProviderAttempt] = []
        self.bar_providers = bar_providers
        self.calendar_providers = calendar_providers
        self.last_instrument_provider_key: str | None = None
        self.last_calendar_attempts: list[ProviderAttempt] = []
        self.last_bar_provider_key: str | None = None
        self.last_calendar_provider_key: str | None = None

        self.last_bar_attempts: list[ProviderAttempt] = []
        self.disabled_bar_providers: dict[str, str] = {}
        self._last_bj_request_at = 0.0

    def begin_batch(self, **kwargs: Any) -> None:
        self.disabled_bar_providers.clear()
        adjustment = str(kwargs.get("adjustment") or "NONE").upper()
        if adjustment in {"QFQ", "HFQ"}:
            self.disabled_bar_providers["tushare"] = (
                "adjusted research series uses BaoStock consistently; Tushare is reserved for raw bars"
            )

    def end_batch(self) -> None:
        seen: set[int] = set()
        for provider in [*self.bar_providers, *self.calendar_providers]:
            identity = id(provider)
            if identity in seen:
                continue
            seen.add(identity)
            close = getattr(provider, "close", None)
            if callable(close):
                close()

    def fetch_instruments(self) -> list[InstrumentRecord]:

        self.last_instrument_attempts = []
        self.last_instrument_provider_key = None
        for provider in self.instrument_providers:
            started = time.monotonic()
            try:
                items = validate_instruments(provider.fetch_instruments())
                self.last_instrument_attempts.append(
                    ProviderAttempt(provider.key, "accepted", row_count=len(items), latency_ms=round((time.monotonic() - started) * 1000))
                )
                self.last_instrument_provider_key = provider.key
                return items
            except Exception as exc:
                self.last_instrument_attempts.append(
                    ProviderAttempt(provider.key, "rejected", sanitize_text(exc, limit=500), latency_ms=round((time.monotonic() - started) * 1000))
                )
        raise RuntimeError("all instrument master-data providers failed contract validation")

    def fetch_bars(self, **kwargs: Any) -> list[MarketBar]:
        if (
            str(kwargs.get("ts_code") or "").upper().endswith(".BJ")
            and str(kwargs.get("adjustment") or "").upper() == "NONE"
        ):
            remaining = 1.3 - (time.monotonic() - self._last_bj_request_at)
            if remaining > 0:
                time.sleep(remaining)
            self._last_bj_request_at = time.monotonic()
        self.last_bar_attempts = []
        self.last_bar_provider_key = None
        for provider in self.bar_providers:
            disabled_reason = self.disabled_bar_providers.get(provider.key)
            if str(kwargs.get("adjustment") or "").upper() == "NONE":
                disabled_reason = None
            if disabled_reason:
                self.last_bar_attempts.append(ProviderAttempt(provider.key, "skipped", disabled_reason))
                continue
            started = time.monotonic()
            try:
                bars = provider.fetch_bars(**kwargs)
                validated = validate_market_bars(
                    bars, start_date=kwargs["start_date"], end_date=kwargs["end_date"]
                )
                self.last_bar_attempts.append(
                    ProviderAttempt(provider.key, "accepted", row_count=len(validated), latency_ms=round((time.monotonic() - started) * 1000))
                )
                self.last_bar_provider_key = provider.key
                return validated
            except Exception as exc:
                safe_error = sanitize_text(exc, limit=500)
                self.last_bar_attempts.append(ProviderAttempt(provider.key, "rejected", safe_error, latency_ms=round((time.monotonic() - started) * 1000)))
                if _is_non_retryable_provider_error(exc):
                    self.disabled_bar_providers[provider.key] = safe_error
        failures = "; ".join(
            f"{item.provider}: {item.error or item.status}" for item in self.last_bar_attempts
        )
        raise RuntimeError(f"all daily market-data providers failed ({failures})")

    def fetch_calendar(self, **kwargs: Any) -> list[CalendarSession]:
        self.last_calendar_attempts = []
        self.last_calendar_provider_key = None
        for provider in self.calendar_providers:
            started = time.monotonic()
            try:
                sessions = validate_calendar(
                    provider.fetch_calendar(**kwargs),
                    start_date=kwargs["start_date"],
                    end_date=kwargs["end_date"],
                )
                self.last_calendar_attempts.append(
                    ProviderAttempt(provider.key, "accepted", row_count=len(sessions), latency_ms=round((time.monotonic() - started) * 1000))
                )
                self.last_calendar_provider_key = provider.key
                return sessions
            except Exception as exc:
                self.last_calendar_attempts.append(
                    ProviderAttempt(provider.key, "rejected", sanitize_text(exc, limit=500), latency_ms=round((time.monotonic() - started) * 1000))
                )
        failures = "; ".join(
            f"{item.provider}: {item.error or item.status}" for item in self.last_calendar_attempts
        )
        raise RuntimeError(f"authoritative trading calendar is unavailable ({failures})")


def build_historical_market_provider(token: str | None = None) -> HistoricalMarketDataProvider:
    if token is None:
        from backend.app.core.config import get_settings

        secret = get_settings().tushare_token
        token = secret.get_secret_value() if secret else None
    tushare = TushareMarketDataProvider(token=token)
    baostock = BaoStockMarketDataProvider()
    akshare = AkShareMarketDataProvider()
    return FallbackMarketDataProvider(
        bar_providers=[tushare, baostock, akshare],
        calendar_providers=[tushare, baostock],
        instrument_providers=[tushare, akshare],
    )
