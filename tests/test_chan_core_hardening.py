from __future__ import annotations

import ast
import math
from pathlib import Path

import pandas as pd
import pytest

from backend.app.chan_core.engine import ChanBar, PureChanEngine
from backend.app.chan_core.vendor.Chan import CChan
from backend.app.chan_core.vendor.ChanConfig import CChanConfig

GOLDEN_BARS = Path(__file__).parent / "golden" / "limit_up_000997_20260724.csv"
VENDOR_ROOT = Path(__file__).parents[1] / "backend" / "app" / "chan_core" / "vendor"


def test_vendored_core_contains_no_dynamic_execution_or_pickle_loading() -> None:
    violations: list[str] = []
    for source_path in VENDOR_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id in {"eval", "exec"}:
                violations.append(f"{source_path}:{node.lineno}:{node.func.id}")
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "pickle"
                and node.func.attr in {"load", "loads"}
            ):
                violations.append(f"{source_path}:{node.lineno}:pickle.{node.func.attr}")

    assert violations == []


def test_vendored_config_rejects_unknown_dynamic_attributes() -> None:
    with pytest.raises(ValueError, match="unsupported buy/sell point config"):
        CChanConfig({"__dict__-buy": {}})


def test_vendored_config_preserves_infinity_without_eval() -> None:
    config = CChanConfig({"divergence_rate": float("inf")})

    assert math.isinf(config.bs_point_conf.b_conf.divergence_rate)
    assert math.isinf(config.bs_point_conf.s_conf.divergence_rate)


def test_vendored_core_exposes_no_pickle_loading_api() -> None:
    assert not hasattr(CChan, "chan_load_pickle")
    assert not hasattr(CChan, "chan_dump_pickle")


def test_pure_chan_engine_matches_repository_golden_fixture() -> None:
    frame = pd.read_csv(GOLDEN_BARS, parse_dates=["date"])
    bars = [
        ChanBar(
            bar_time=row.date.to_pydatetime(),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
            amount=float(row.amount),
            turnover_rate=None,
        )
        for row in frame.itertuples()
    ]

    analysis = PureChanEngine().analyze_bars("000997", bars)

    assert analysis["kline_count"] == 188
    assert len(analysis["bi_list"]) == 14
    assert len(analysis["seg_list"]) == 4
    assert len(analysis["zs_list"]) == 1
    assert len(analysis["buy_signals"]) == 3
    assert len(analysis["sell_signals"]) == 3
    assert analysis["bi_list"][-1]["start_date"] == "2026/07/14"
    assert analysis["bi_list"][-1]["end_date"] == "2026/07/21"
    assert analysis["bi_list"][-1]["start_price"] == pytest.approx(15.16)
    assert analysis["bi_list"][-1]["end_price"] == pytest.approx(18.28)
