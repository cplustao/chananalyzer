from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.db.models import Job, RadarSnapshot, Watchlist, WatchlistItem
from backend.app.domain.market_positions import build_executable_positions
from backend.app.services.data_health import DataHealthService
from backend.app.services.scan_changes import SCAN_KINDS, ScanChangeService


class DecisionService:
    def __init__(self, session: Session, settings: Settings, user_id: str):
        self.session = session
        self.settings = settings
        self.user_id = user_id
        self.missing: list[str] = []

    def today(self) -> dict[str, Any]:
        health = self._safe("data_health", lambda: DataHealthService(self.session, self.settings).snapshot())
        radar = self._safe("market_radar", self._radar)
        review_context = self._review_context(health, radar)
        if radar:
            radar["freshness"] = review_context["freshness"]
            if not review_context["usable_for_next_session"]:
                radar["executable_position"] = None
        latest_scan = self._safe("scan_changes", self._scan_changes)
        watchlist_hits = self._safe("watchlist_hits", lambda: self._watchlist_hits(latest_scan))
        jobs = self._safe("attention_jobs", self._attention_jobs)
        industries = []
        if radar:
            industries = ((radar.get("snapshot") or {}).get("limit_ecology") or {}).get("industries", [])
        stale = [item["key"] for item in (health or {}).get("categories", []) if item["status"] != "fresh"]
        return {
            "review_context": review_context,
            "market_facts": self._market_facts(radar),
            "market_radar": radar,
            "clustered_industries": {
                "items": industries,
                "source": ((radar or {}).get("snapshot") or {}).get("limit_ecology", {}).get("source"),
                "model": "limit-up aggregation only",
            },
            "scan_changes": latest_scan,
            "watchlist_hits": watchlist_hits,
            "data_health": health,
            "attention_jobs": jobs,
            "missing_or_stale_modules": sorted(set(self.missing + stale)),
        }

    def _safe(self, key: str, callback: Callable[[], Any]) -> Any:
        try:
            return callback()
        except Exception:
            self.session.rollback()
            self.missing.append(key)
            return None

    def _review_context(
        self, health: dict[str, Any] | None, radar: dict[str, Any] | None
    ) -> dict[str, Any]:
        expected = (health or {}).get("expected_trade_date")
        as_of = (radar or {}).get("trade_date")
        categories = {
            item.get("key"): item
            for item in (health or {}).get("categories", [])
            if isinstance(item, dict)
        }
        critical_keys = ("daily_bars", "master_data", "trading_calendar")
        blocking = [
            key for key in critical_keys if (categories.get(key) or {}).get("status") != "fresh"
        ]
        if radar is None:
            return {
                "mode": "unavailable",
                "freshness": "missing",
                "usable_for_next_session": False,
                "as_of_trade_date": None,
                "generated_at": None,
                "expected_trade_date": expected,
                "applicable_to": "\u4e0d\u53ef\u7528",
                "title": "\u6682\u65e0\u53ef\u7528\u590d\u76d8",
                "message": "\u5c1a\u672a\u751f\u6210\u53ef\u8ffd\u6eaf\u7684\u5e02\u573a\u96f7\u8fbe\u5feb\u7167\u3002",
                "blocking_modules": sorted(set(blocking + ["market_radar"])),
            }

        if expected is None:
            blocking.append("trading_calendar")
        elif as_of != expected:
            blocking.append("market_radar")
        if radar.get("freshness") != "fresh":
            blocking.append("market_radar")
        blocking = sorted(set(blocking))
        usable = not blocking and as_of == expected
        if usable:
            freshness = "fresh"
            title = "\u6700\u65b0\u5b8c\u6574\u4ea4\u6613\u65e5\u590d\u76d8"
            message = f"\u6570\u636e\u622a\u81f3 {as_of}\uff0c\u53ef\u7528\u4e8e\u4e0b\u4e00\u4ea4\u6613\u65e5\u5f00\u76d8\u524d\u7684\u7814\u7a76\u51c6\u5907\u3002"
            applicable_to = "\u4e0b\u4e00\u4ea4\u6613\u65e5\u5f00\u76d8\u524d"
            mode = "next_session_preparation"
        else:
            states = [
                str((categories.get(key) or {}).get("status", "missing")) for key in critical_keys
            ]
            state_rank = {"fresh": 0, "partial": 1, "stale": 2, "missing": 3}
            freshness = max(states, key=lambda state: state_rank.get(state, 3))
            if as_of != expected or radar.get("freshness") == "stale":
                freshness = "stale"
            title = "\u5386\u53f2\u590d\u76d8\u5feb\u7167"
            message = f"\u5feb\u7167\u622a\u81f3 {as_of or 'unknown'}\uff1b\u5173\u952e\u6570\u636e\u95e8\u7981\u672a\u901a\u8fc7\uff0c\u4e0d\u63d0\u4f9b\u6b21\u65e5\u4ed3\u4f4d\u7ed3\u8bba\u3002"
            applicable_to = "\u4ec5\u4f9b\u5386\u53f2\u590d\u76d8"
            mode = "historical_review"
        return {
            "mode": mode,
            "freshness": freshness,
            "usable_for_next_session": usable,
            "as_of_trade_date": as_of,
            "generated_at": radar.get("generated_at"),
            "expected_trade_date": expected,
            "applicable_to": applicable_to,
            "title": title,
            "message": message,
            "blocking_modules": blocking,
        }

    def _radar(self) -> dict[str, Any] | None:
        row = self.session.scalar(
            select(RadarSnapshot).order_by(
                RadarSnapshot.trade_date.desc(), RadarSnapshot.calculated_at.desc()
            )
        )
        if row is None:
            self.missing.append("market_radar")
            return None
        history = self.session.scalars(
            select(RadarSnapshot)
            .where(RadarSnapshot.algorithm_version == row.algorithm_version)
            .order_by(RadarSnapshot.trade_date)
        ).all()
        positions = build_executable_positions(
            [
                {"trade_date": item.trade_date.isoformat(), "score": item.score, "status": item.status}
                for item in history
            ]
        )
        snapshot = deepcopy(row.snapshot)
        previous = history[-2] if len(history) > 1 else None
        return {
            "id": row.id,
            "trade_date": row.trade_date.isoformat(),
            "generated_at": snapshot.get("generated_at") or row.calculated_at.isoformat(),
            "algorithm_version": row.algorithm_version,
            "freshness": row.freshness,
            "data_time": row.data_time.isoformat() if row.data_time else row.trade_date.isoformat(),
            "source": row.source_summary or snapshot.get("source"),
            "coverage_rate": row.coverage_rate
            if row.coverage_rate is not None
            else (snapshot.get("coverage") or {}).get("rate"),
            "score": row.score,
            "status": row.status,
            "status_label": row.status_label,
            "executable_position": positions[-1] if positions else None,
            "changes": self._radar_changes(
                snapshot,
                deepcopy(previous.snapshot) if previous else None,
                previous.trade_date.isoformat() if previous else None,
            ),
            "snapshot": snapshot,
        }

    @staticmethod
    def _radar_changes(
        current: dict[str, Any], previous: dict[str, Any] | None, previous_trade_date: str | None
    ) -> dict[str, Any]:
        if previous is None:
            return {"previous_trade_date": None, "items": []}
        definitions = [
            ("advance_rate", "\u4e0a\u6da8\u5360\u6bd4", "breadth", "advance_rate", 100.0, "%"),
            ("above_ma20_rate", "\u7ad9\u4e0a MA20", "breadth", "above_ma20_rate", 100.0, "%"),
            ("amount_yi", "\u5168\u5e02\u573a\u6210\u4ea4\u989d", "liquidity", "amount_yi", 1.0, "\u4ebf\u5143"),
            ("limit_up_count", "\u6da8\u505c\u5bb6\u6570", "limit_ecology", "limit_up_count", 1.0, "\u5bb6"),
            ("limit_down_count", "\u8dcc\u505c\u4f30\u7b97", "breadth", "limit_down_count", 1.0, "\u5bb6"),
        ]
        items = []
        for key, label, group, field, factor, unit in definitions:
            current_value = (current.get(group) or {}).get(field)
            previous_value = (previous.get(group) or {}).get(field)
            if not isinstance(current_value, (int, float)) or not isinstance(
                previous_value, (int, float)
            ):
                continue
            current_number = round(float(current_value) * factor, 2)
            previous_number = round(float(previous_value) * factor, 2)
            items.append(
                {
                    "key": key,
                    "label": label,
                    "current": current_number,
                    "previous": previous_number,
                    "delta": round(current_number - previous_number, 2),
                    "unit": unit,
                }
            )
        return {"previous_trade_date": previous_trade_date, "items": items}

    @staticmethod
    def _market_facts(radar: dict[str, Any] | None) -> list[dict[str, str]]:
        if not radar:
            return []
        snapshot = radar.get("snapshot") or {}
        breadth = snapshot.get("breadth") or {}
        liquidity = snapshot.get("liquidity") or {}
        ecology = snapshot.get("limit_ecology") or {}
        dash = "\u2014"

        def percent(value: Any) -> str:
            return "\u2014" if not isinstance(value, (int, float)) else f"{value * 100:.1f}%"

        amount = liquidity.get("amount_yi")
        amount_text = f"{amount:,.1f} \u4ebf\u5143" if isinstance(amount, (int, float)) else "\u2014"
        ratio = liquidity.get("amount_ratio")
        return [
            {
                "key": "breadth",
                "label": "\u4e0a\u6da8 / \u4e0b\u8dcc",
                "value": f"{breadth.get('advancers', dash)} / {breadth.get('decliners', dash)}",
                "detail": f"\u4e0a\u6da8\u5360\u6bd4 {percent(breadth.get('advance_rate'))}",
            },
            {
                "key": "above_ma20",
                "label": "\u7ad9\u4e0a MA20",
                "value": percent(breadth.get("above_ma20_rate")),
                "detail": f"{breadth.get('above_ma20_count', dash)} \u53ea\u80a1\u7968",
            },
            {
                "key": "liquidity",
                "label": "\u5168\u5e02\u573a\u6210\u4ea4\u989d",
                "value": amount_text,
                "detail": f"\u8f83\u524d\u4e00\u4ea4\u6613\u65e5 {percent(ratio - 1) if isinstance(ratio, (int, float)) else dash}",
            },
            {
                "key": "limit_ecology",
                "label": "\u6da8\u505c / \u8dcc\u505c\u4f30\u7b97",
                "value": f"{ecology.get('limit_up_count', dash)} / {breadth.get('limit_down_count', dash)}",
                "detail": f"\u6700\u9ad8 {ecology.get('highest_board', dash)} \u677f",
            },
        ]

    def _scan_changes(self) -> dict[str, Any] | None:
        job = self.session.scalar(select(Job).where(Job.kind.in_(SCAN_KINDS)).order_by(Job.created_at.desc()))
        if job is None:
            self.missing.append("scan_changes")
            return None
        return ScanChangeService(self.session).compare(job.id)

    def _watchlist_hits(self, changes: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not changes:
            return []
        instrument_ids = {
            item.get("instrument_id")
            for item in changes.get("items", [])
            if item.get("state") in {"new_hit", "continued_hit"}
        }
        if not instrument_ids:
            return []
        rows = self.session.execute(
            select(WatchlistItem, Watchlist.name)
            .join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)
            .where(Watchlist.user_id == self.user_id, WatchlistItem.instrument_id.in_(instrument_ids))
            .order_by(WatchlistItem.position)
        ).all()
        changes_by_id = {item.get("instrument_id"): item for item in changes.get("items", [])}
        return [
            {
                "watchlist": name,
                "item_id": item.id,
                "instrument_id": item.instrument_id,
                "change": changes_by_id[item.instrument_id],
            }
            for item, name in rows
        ]

    def _attention_jobs(self) -> dict[str, Any]:
        failed = self.session.scalars(
            select(Job).where(Job.status.in_(("partial", "failed")), Job.archived_at.is_(None)).order_by(Job.created_at.desc()).limit(8)
        ).all()
        pending = self.session.scalars(
            select(Job).where(Job.status.in_(("queued", "running")), Job.archived_at.is_(None)).order_by(Job.created_at.desc()).limit(8)
        ).all()
        def view(job: Job) -> dict[str, Any]:
            return {
                "id": job.id,
                "kind": job.kind,
                "status": job.status,
                "message": job.message,
                "created_at": job.created_at.isoformat(),
            }

        return {"failed": [view(job) for job in failed], "pending": [view(job) for job in pending]}
