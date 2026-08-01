from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from math import isfinite
from typing import Any

from backend.app.chan_core.vendor.Chan import CChan
from backend.app.chan_core.vendor.ChanConfig import CChanConfig
from backend.app.chan_core.vendor.Common.CEnum import AUTYPE, DATA_FIELD, KL_TYPE
from backend.app.chan_core.vendor.Common.CTime import CTime
from backend.app.chan_core.vendor.KLine.KLine_Unit import CKLine_Unit

ALGORITHM_VERSION = "chan-core-v2-memory-1"

DEFAULT_CHAN_CONFIG: dict[str, Any] = {
    "bi_strict": True,
    "trigger_step": False,
    "skip_step": 0,
    "divergence_rate": float("inf"),
    "bsp2_follow_1": False,
    "bsp3_follow_1": False,
    "min_zs_cnt": 0,
    "bs1_peak": False,
    "macd_algo": "peak",
    "bs_type": "1,1p,2,2s,3a,3b",
    "print_warning": False,
    "zs_algo": "normal",
    "macd": {"fast": 12, "slow": 26, "signal": 9},
}

def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else "inf" if value > 0 else "-inf"
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


@dataclass(frozen=True, slots=True)
class ChanBar:
    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None = None
    turnover_rate: float | None = None


def _unit(bar: ChanBar) -> CKLine_Unit:
    payload: dict[str, Any] = {
        DATA_FIELD.FIELD_TIME: CTime(
            bar.bar_time.year,
            bar.bar_time.month,
            bar.bar_time.day,
            bar.bar_time.hour,
            bar.bar_time.minute,
        ),
        DATA_FIELD.FIELD_OPEN: float(bar.open),
        DATA_FIELD.FIELD_HIGH: float(bar.high),
        DATA_FIELD.FIELD_LOW: float(bar.low),
        DATA_FIELD.FIELD_CLOSE: float(bar.close),
        DATA_FIELD.FIELD_VOLUME: float(bar.volume),
        DATA_FIELD.FIELD_TURNOVER: float(bar.amount or 0),
    }
    if bar.turnover_rate is not None:
        payload[DATA_FIELD.FIELD_TURNRATE] = float(bar.turnover_rate)
    return CKLine_Unit(payload)


class PureChanEngine:
    """Protected Chan algorithm with caller-supplied bars and no I/O."""

    algorithm_version = ALGORITHM_VERSION

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        inherit_research_defaults: bool = True,
    ):
        base = DEFAULT_CHAN_CONFIG if inherit_research_defaults else {}
        merged = {**base, **(config or {})}
        merged["macd"] = {
            **DEFAULT_CHAN_CONFIG["macd"],
            **(merged.get("macd") or {}),
        }
        self.raw_config = merged

    def analyze_bars(self, code: str, bars: Iterable[ChanBar]) -> dict[str, Any]:
        ordered = sorted(bars, key=lambda item: item.bar_time)
        if not ordered:
            raise ValueError(f"{code} 没有可分析的日线数据")

        # trigger_step prevents CChan.__init__ from opening a data source. Once
        # constructed, turn it off so trigger_load performs the normal final
        # segment/central-zone calculation.
        engine_config = {**self.raw_config, "trigger_step": True}
        engine_config["macd"] = dict(self.raw_config["macd"])
        chan = CChan(
            code=code,
            data_src="memory",
            lv_list=[KL_TYPE.K_DAY],
            config=CChanConfig(engine_config),
            autype=AUTYPE.QFQ,
        )
        chan.conf.trigger_step = False
        chan.trigger_load({KL_TYPE.K_DAY: [_unit(bar) for bar in ordered]})
        analysis = self._serialize_level(chan, 0)
        analysis.update(
            {
                "code": code,
                "name": code,
                "algorithm_version": self.algorithm_version,
                "config": self.config_summary(),
            }
        )
        return json_safe(analysis)

    def config_summary(self) -> dict[str, Any]:
        return {
            "profile": "loose",
            "profile_label": "宽松信号",
            "autype": "QFQ",
            "level": "DAY",
            "data_source": "V2_BARS_MEMORY",
            "config": json_safe(self.raw_config),
        }

    def _serialize_level(self, chan: CChan, level_idx: int) -> dict[str, Any]:
        kl_data = chan[level_idx]
        if not kl_data.lst:
            return {"kl_type": "日线", "kline_count": 0, "error": "无K线数据"}

        first_klc = kl_data.lst[0]
        last_klc = kl_data.lst[-1]
        last_klu = last_klc.lst[-1]
        latest_macd = None
        if getattr(last_klu, "macd", None) is not None:
            latest_macd = {
                "macd": last_klu.macd.macd,
                "dif": last_klu.macd.DIF,
                "dea": last_klu.macd.DEA,
            }

        bi_list = []
        for bi in kl_data.bi_list:
            end_klu = bi.get_end_klu()
            bi_list.append(
                {
                    "idx": bi.idx,
                    "dir": "向上" if bi.is_up() else "向下",
                    "start_date": bi.get_begin_klu().time.to_str(),
                    "end_date": end_klu.time.to_str(),
                    "start_price": bi.get_begin_val(),
                    "end_price": bi.get_end_val(),
                    "is_sure": bi.is_sure,
                    "macd": end_klu.macd.macd if getattr(end_klu, "macd", None) is not None else None,
                }
            )

        seg_list = [
            {
                "idx": seg.idx,
                "dir": "向上" if seg.is_up() else "向下",
                "start_date": seg.get_begin_klu().time.to_str(),
                "end_date": seg.get_end_klu().time.to_str(),
                "start_price": seg.get_begin_val(),
                "end_price": seg.get_end_val(),
                "bi_count": seg.cal_bi_cnt(),
                "is_sure": seg.is_sure,
            }
            for seg in kl_data.seg_list
        ]
        zs_list = [
            {
                "idx": zs.begin_bi.idx,
                "start_date": zs.begin_bi.get_begin_klu().time.to_str(),
                "end_date": zs.end_bi.get_end_klu().time.to_str(),
                "high": zs.high,
                "low": zs.low,
                "center": zs.mid,
                "bi_count": zs.end_bi.idx - zs.begin_bi.idx + 1,
            }
            for zs in kl_data.zs_list
        ]

        buy_signals: list[dict[str, Any]] = []
        sell_signals: list[dict[str, Any]] = []
        for point in kl_data.bs_point_lst.bsp_iter():
            signal = {
                "type": point.type2str(),
                "type_raw": point.type2str(),
                "is_buy": point.is_buy,
                "date": point.klu.time.to_str(),
                "price": point.klu.close,
                "klu_idx": point.klu.idx,
            }
            (buy_signals if point.is_buy else sell_signals).append(signal)

        latest_zs = zs_list[-1] if zs_list else None
        if latest_zs is None:
            zs_position = "无中枢"
        elif last_klu.close > latest_zs["high"]:
            zs_position = "中枢上方（强势）"
        elif last_klu.close < latest_zs["low"]:
            zs_position = "中枢下方（弱势）"
        else:
            zs_position = "中枢内部"

        return {
            "kl_type": "日线",
            "kl_type_enum": "K_DAY",
            "start_date": first_klc.time_begin.to_str(),
            "end_date": last_klc.time_end.to_str(),
            "kline_count": len(kl_data.lst),
            "current_price": last_klu.close,
            "macd": latest_macd,
            "bi_list": bi_list,
            "seg_list": seg_list,
            "zs_list": zs_list,
            "buy_signals": buy_signals,
            "sell_signals": sell_signals,
            "volume_analysis": self._volume_analysis(kl_data),
            "kline_range": self._kline_range(kl_data),
            "zs_position": zs_position,
            "latest": {
                "bi": bi_list[-1] if bi_list else None,
                "seg": seg_list[-1] if seg_list else None,
                "zs": latest_zs,
            },
        }

    @staticmethod
    def _volume_analysis(kl_data) -> dict[str, Any]:
        if len(kl_data.lst) < 5:
            return {"status": "数据不足"}
        recent = kl_data.lst[-5:]
        volumes = [sum(float(unit.trade_info.metric.get("volume") or 0) for unit in item.lst) for item in recent]
        average = sum(volumes) / len(volumes)
        ratio = volumes[-1] / average if average > 0 else 1
        price_up = [item.lst[-1].close > item.lst[0].open for item in recent]
        high_volume = [value > average for value in volumes]
        recent_price_up = sum(price_up[-3:])
        recent_volume_high = sum(high_volume[-3:])
        if recent_price_up >= 2:
            relation = "价涨量增（健康上涨）" if recent_volume_high >= 2 else "价涨量缩（上涨乏力）"
        else:
            relation = "价跌量增（恐慌抛售）" if recent_volume_high >= 2 else "价跌量缩（惜售）"
        return {
            "current_vol": volumes[-1],
            "avg_vol": average,
            "vol_ratio": ratio,
            "vol_status": "缩量（<0.5倍均量）" if ratio < 0.5 else "放量（>2倍均量）" if ratio > 2 else "正常",
            "k_vol_price": [
                {
                    "price_up": up,
                    "vol_high": high,
                    "desc": (
                        "价格涨, 放量"
                        if up and high
                        else "价格涨, 缩量"
                        if up
                        else "价格跌, 放量"
                        if high
                        else "价格跌, 缩量"
                    ),
                }
                for up, high in zip(price_up, high_volume, strict=True)
            ],
            "vol_price_rel": relation,
        }

    @staticmethod
    def _kline_range(kl_data, period: int = 20) -> dict[str, Any]:
        recent = kl_data.lst[-min(period, len(kl_data.lst)) :]
        highs = [max(unit.high for unit in item.lst) for item in recent]
        lows = [min(unit.low for unit in item.lst) for item in recent]
        current = recent[-1].lst[-1].close
        high = max(highs)
        low = min(lows)
        width = high - low
        return {
            "period": len(recent),
            "period_high": round(high, 2),
            "period_low": round(low, 2),
            "current_price": round(current, 2),
            "position_pct": round((current - low) / width * 100 if width > 0 else 50, 1),
            "dist_high_pct": round((current - high) / high * 100 if high > 0 else 0, 2),
            "dist_low_pct": round((current - low) / low * 100 if low > 0 else 0, 2),
        }
