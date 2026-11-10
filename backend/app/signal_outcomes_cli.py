"""Print what happened after the saved scan signals: python -m backend.app.signal_outcomes_cli [--json]

Reads the local database only; nothing is written.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from backend.app.db.session import SessionLocal
from backend.app.services.signal_outcomes import MIN_ENTRY_DAYS, MIN_SAMPLE, SignalOutcomeService

GROUP_TITLES = {
    "overall": "全部信号",
    "signal_type": "按信号类型",
    "freshness": "按发现时信号的新旧",
    "known_date": "按扫描数据日期",
}


def _pct(value: float) -> str:
    return f"{value * 100:+.1f}%"


def _signal_cell(summary: dict[str, Any]) -> str:
    counts = f"n={summary['measured']}，入场日 {summary['entry_days']}"
    if summary["pending"]:
        counts += f"，未到期 {summary['pending']}"
    if summary["unusable"]:
        counts += f"，无法计算 {summary['unusable']}"
    if not summary["sufficient"]:
        return f"样本不足（{counts}）"
    return (
        f"中位 {_pct(summary['median_return'])}，超额中位 {_pct(summary['median_excess'])}，"
        f"跑赢市场 {summary['beat_market_rate'] * 100:.0f}%（{counts}）"
    )


def _radar_cell(summary: dict[str, Any]) -> str:
    if not summary["sufficient"]:
        return f"样本不足（{summary['measured_days']} 天）"
    return (
        f"市场中位均值 {_pct(summary['mean_market_median'])}，上涨天数占比 {summary['up_rate'] * 100:.0f}%"
        f"（{summary['measured_days']} 天）"
    )


def render(report: dict[str, Any]) -> str:
    horizons = report["horizons"]
    header = "| 分组 | 信号数 | " + " | ".join(f"T+{h}" for h in horizons) + " |"
    rule = "|---|---|" + "---|" * len(horizons)
    method = report["method"]
    lines = [
        f"# 信号后验 v0（行情截至 {report['data_through']}）",
        "",
        f"- 入场：{method['entry']}；出场：{method['exit']}；基准：{method['benchmark']}。",
        f"- 只统计扫描当时落库的买点，共 {report['signals']} 个（同一股票、类型、信号日只算一次）。",
        f"- 样本少于 {MIN_SAMPLE} 个，或入场日少于 {MIN_ENTRY_DAYS} 天（同一天入场的信号共用一段行情），只显示数量。",
        "- 未计交易成本、涨停买不进和幸存者偏差，不构成投资建议。",
    ]
    for key, title in GROUP_TITLES.items():
        lines += ["", f"## {title}", "", header, rule]
        for group in report["groups"][key]:
            cells = [_signal_cell(group["horizons"][str(h)]) for h in horizons]
            lines.append(f"| {group['group']} | {group['signals']} | " + " | ".join(cells) + " |")
    lines += [
        "", "## 市场雷达状态之后的市场表现", "",
        "| 算法版本 | 状态 | 天数（其中回填） | " + " | ".join(f"T+{h}" for h in horizons) + " |",
        "|---|---|---|" + "---|" * len(horizons),
    ]
    for row in report["radar"]:
        cells = [_radar_cell(row["horizons"][str(h)]) for h in horizons]
        lines.append(
            f"| {row['algorithm_version']} | {row['status_label']} | {row['days']}（{row['backfilled_days']}） | "
            + " | ".join(cells) + " |"
        )
    return "\n".join(lines)


def run() -> None:
    parser = argparse.ArgumentParser(prog="chan-signal-outcomes", description="Saved scan signal outcomes (v0)")
    parser.add_argument("--json", action="store_true", help="print the raw report as JSON")
    arguments = parser.parse_args()
    with SessionLocal() as session:
        report = SignalOutcomeService(session).build()
        session.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if arguments.json else render(report))


if __name__ == "__main__":
    run()
