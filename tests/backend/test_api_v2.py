from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from backend.app.db.models import (
    AnalysisRun,
    Bar,
    Industry,
    Instrument,
    Job,
    JobItem,
    LimitUpEvent,
    RadarSnapshot,
)


def test_health_and_system_status(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    assert response.json()["auth_mode"] == "local"


def test_instrument_bars_and_search(client, session):
    instrument = Instrument(
        code="600519", ts_code="600519.SH", exchange="SSE", name="贵州茅台", status="active"
    )
    session.add(instrument)
    session.flush()
    session.add(
        Bar(
            instrument_id=instrument.id,
            timeframe="DAY",
            adjustment="QFQ",
            bar_time=datetime(2026, 7, 27),
            open=1400,
            high=1430,
            low=1390,
            close=1420,
            volume=12345,
        )
    )
    session.commit()
    found = client.get("/api/v1/instruments?q=茅台&page_size=10")
    assert found.status_code == 200 and found.json()["total"] == 1
    bars = client.get(f"/api/v1/instruments/{instrument.id}/bars?limit=500")
    assert bars.status_code == 200
    assert bars.json()["items"][0]["close"] == 1420


def test_job_deduplication_and_cancel(client):
    payload = {"mode": "buy", "codes": ["600519"], "types": ["2"]}
    first = client.post("/api/v1/scans", json=payload)
    second = client.post("/api/v1/scans", json=payload)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert second.json()["deduplicated"] is True
    cancelled = client.post(f"/api/v1/jobs/{first.json()['job_id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"


def test_terminal_job_can_be_removed_from_task_center_without_deleting_history(client, session):
    job = Job(kind="scan.buy", status="failed", payload={"codes": ["600519"]}, error="模拟失败")
    session.add(job)
    session.commit()

    response = client.delete(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 204
    session.refresh(job)
    assert job.archived_at is not None
    assert all(item["id"] != job.id for item in client.get("/api/v1/jobs").json())
    assert client.get(f"/api/v1/jobs/{job.id}").status_code == 200


def test_running_job_cannot_be_removed(client, session):
    job = Job(kind="scan.buy", status="running", payload={})
    session.add(job)
    session.commit()

    response = client.delete(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 409
    assert "先取消" in response.json()["message"]


def test_data_refresh_rerun_only_retries_failed_result_codes(client, session):
    original = Job(
        kind="data.refresh",
        status="partial",
        payload={
            "codes": ["000001", "000002", "600519"],
            "start_date": "2026-07-20",
            "end_date": "2026-07-29",
        },
        result={
            "failed_codes": ["000002", "600519", "300750"],
            "errors": [
                {"code": "000002", "error": "provider returned no daily bars"},
                {"code": "600519", "error": "remote connection closed"},
                {"code": "000002", "error": "duplicate provider error"},
            ],
        },
        total=3,
        completed=3,
        failed=2,
        progress=100,
    )
    session.add(original)
    session.commit()

    response = client.post(f"/api/v1/jobs/{original.id}/rerun")

    assert response.status_code == 202
    rerun = session.get(Job, response.json()["job_id"])
    assert rerun is not None
    assert rerun.retry_of_id == original.id
    assert rerun.payload["codes"] == ["000002", "600519", "300750"]
    assert rerun.payload["start_date"] == "2026-07-20"
    assert rerun.payload["end_date"] == "2026-07-29"


def test_scan_rerun_filters_non_stock_legacy_subjects(client, session):
    stock = Instrument(code="600519", ts_code="600519.SH", status="active")
    legacy = Instrument(code="sh.000002", ts_code="sh.000002", status="active")
    original = Job(kind="screen.smart", status="partial", payload={"codes": []})
    session.add_all([stock, legacy, original])
    session.flush()
    session.add_all(
        [
            JobItem(job_id=original.id, subject_key=stock.code, status="failed", error="temporary"),
            JobItem(job_id=original.id, subject_key=legacy.code, status="failed", error="no bars"),
        ]
    )
    session.commit()

    response = client.post(f"/api/v1/jobs/{original.id}/rerun")

    assert response.status_code == 202
    rerun = session.get(Job, response.json()["job_id"])
    assert rerun is not None and rerun.payload["codes"] == ["600519"]

    legacy_only = Job(kind="screen.smart", status="partial", payload={"codes": []})
    session.add(legacy_only)
    session.flush()
    session.add(JobItem(job_id=legacy_only.id, subject_key=legacy.code, status="failed"))
    session.commit()

    rejected = client.post(f"/api/v1/jobs/{legacy_only.id}/rerun")

    assert rejected.status_code == 409
    assert "无需再次补跑" in rejected.json()["message"]


def test_watchlist_is_normalized_and_idempotent(client, session):
    stock = Instrument(code="600519", ts_code="600519.SH", name="贵州茅台", status="active")
    session.add(stock)
    session.commit()
    instrument = client.get("/api/v1/instruments?q=600519&page_size=1").json()["items"][0]
    payload = {"instrument_id": instrument["id"], "note": "核心观察", "tag_names": ["白酒", "价值"]}
    assert client.post("/api/v1/watchlists/default/items", json=payload).status_code == 200
    assert client.post("/api/v1/watchlists/default/items", json=payload).status_code == 200
    watchlist = client.get("/api/v1/watchlists/default").json()
    matching = [item for item in watchlist["items"] if item["instrument"]["id"] == instrument["id"]]
    assert len(matching) == 1
    assert set(matching[0]["tags"]) == {"白酒", "价值"}


def test_secret_plaintext_is_never_returned(client):
    response = client.put("/api/v1/settings/secrets/DEEPSEEK_API_KEY", json={"value": "test-secret-value"})
    assert response.status_code == 200
    assert response.json() == {"key": "DEEPSEEK_API_KEY", "configured": True, "source": "database"}
    assert "test-secret-value" not in client.get("/api/v1/settings/secrets").text


def test_admin_mode_requires_login_and_uses_configured_cookie(client, session):
    from backend.app.api.deps import settings_dep
    from backend.app.core.config import Settings
    from backend.app.core.security import ensure_identity
    from backend.app.main import app

    admin_settings = Settings(
        environment="test",
        auth_mode="admin",
        admin_username="admin",
        admin_password="correct-horse-battery-staple",
        session_cookie_name="custom_research_session",
        cookie_secure=False,
    )
    ensure_identity(session, admin_settings)
    app.dependency_overrides[settings_dep] = lambda: admin_settings
    try:
        assert client.get("/api/v1/system/status").status_code == 401
        login = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correct-horse-battery-staple"},
        )
        assert login.status_code == 200
        assert "custom_research_session=" in login.headers["set-cookie"]
        assert "HttpOnly" in login.headers["set-cookie"]
        assert client.get("/api/v1/system/status").status_code == 200
    finally:
        app.dependency_overrides.pop(settings_dep, None)


def test_radar_history_contains_executable_position(client, session):
    start = date(2026, 7, 1)
    scores = [30, 34, 42, 52, 62, 72]
    for index, score in enumerate(scores):
        trade_date = start + timedelta(days=index)
        session.add(
            RadarSnapshot(
                trade_date=trade_date,
                algorithm_version="test-1.1",
                score=score,
                status="risk" if score < 35 else "neutral",
                status_label="风险" if score < 35 else "震荡",
                snapshot={
                    "trade_date": trade_date.isoformat(),
                    "regime": {
                        "score": score,
                        "status": "risk" if score < 35 else "neutral",
                        "status_label": "风险" if score < 35 else "震荡",
                        "position_range": {"min": 0, "max": 0.2},
                    },
                    "components": [{"key": "market_breadth", "score": score}],
                },
                calculated_at=datetime(2026, 7, index + 1),
            )
        )
    session.commit()
    history = client.get("/api/v1/market/radar/history?limit=6")
    assert history.status_code == 200
    assert history.json()["items"][-1]["executable_position"]["mid"] >= 0
    current = client.get("/api/v1/market/radar")
    assert current.status_code == 200
    assert current.json()["regime"]["executable_position"]["effective"] == "下一交易日"


def test_partial_radar_never_exposes_executable_position(client, session):
    trade_date = date(2026, 7, 20)
    session.add(
        RadarSnapshot(
            trade_date=trade_date,
            algorithm_version="partial-gate-test",
            score=72,
            status="strong",
            status_label="强势",
            freshness="partial",
            snapshot={
                "trade_date": trade_date.isoformat(),
                "freshness": "partial",
                "regime": {
                    "score": 72,
                    "status": "strong",
                    "status_label": "强势",
                    "position_range": {"min": 0.6, "max": 0.85},
                },
                "components": [],
            },
            calculated_at=datetime(2026, 7, 20),
        )
    )
    session.commit()

    history = client.get("/api/v1/market/radar/history?limit=1")
    assert history.status_code == 200
    assert history.json()["items"][-1]["executable_position"] is None

    current = client.get("/api/v1/market/radar")
    assert current.status_code == 200
    regime = current.json()["regime"]
    assert regime["executable_position"] is None
    assert "position_range" not in regime
    assert regime["position_unavailable_reason"]


def test_limit_up_range_and_cached_chan_structure(client, session):
    instrument = Instrument(code="600519", ts_code="600519.SH", name="贵州茅台", status="active")
    session.add(instrument)
    session.flush()
    session.add_all(
        [
            LimitUpEvent(
                instrument_id=instrument.id,
                legacy_code=instrument.code,
                trade_date=date(2026, 7, 10),
                pct_change=10.0,
                consecutive_boards=1,
                raw_payload={"name": instrument.name},
            ),
            AnalysisRun(
                kind="stock",
                instrument_id=instrument.id,
                subject_date=date(2026, 7, 10),
                status="completed",
                algorithm_version="chan-core-test",
                input_snapshot={
                    "bi_list": [{"idx": 1, "start_date": "2026-07-01", "end_date": "2026-07-10"}],
                    "seg_list": [],
                    "zs_list": [],
                    "buy_signals": [],
                    "sell_signals": [],
                },
                finished_at=datetime(2026, 7, 10),
            ),
        ]
    )
    session.commit()
    response = client.get("/api/v1/limit-ups?start_date=20260701&end_date=20260715")
    assert response.status_code == 200
    assert response.json()["items"][0]["trade_date"] == "20260710"
    structure = client.get(f"/api/v1/instruments/{instrument.id}/chan-structure")
    assert structure.status_code == 200
    assert structure.json()["analysis"]["bi_list"][0]["idx"] == 1


def test_smart_screen_preserves_scan_side(client, session):
    response = client.post(
        "/api/v1/scans",
        json={
            "mode": "smart",
            "scan_side": "sell",
            "types": ["2s"],
            "industries": ["白酒"],
            "areas": ["贵州"],
            "min_net_mf_amount": 1000,
            "min_main_net_amount": 500,
        },
    )
    assert response.status_code == 202
    job = session.scalar(select(Job).where(Job.id == response.json()["job_id"]))
    assert job is not None
    assert job.kind == "screen.smart"
    assert job.payload["scan_side"] == "sell"
    assert job.payload["min_net_mf_amount"] == 1000
    assert job.payload["min_main_net_amount"] == 500


def test_instrument_facets_return_selectable_counts(client, session):
    industry = Industry(name="白酒", source="test")
    session.add(industry)
    session.flush()
    session.add_all(
        [
            Instrument(
                code="600519",
                ts_code="600519.SH",
                exchange="SH",
                name="贵州茅台",
                area="贵州",
                industry_id=industry.id,
                status="active",
            ),
            Instrument(
                code="000858",
                ts_code="000858.SZ",
                exchange="SZ",
                name="五粮液",
                area="四川",
                industry_id=industry.id,
                status="active",
            ),
        ]
    )
    session.commit()
    response = client.get("/api/v1/instruments/facets")
    assert response.status_code == 200
    assert {"value": "白酒", "count": 2} in response.json()["industries"]
    assert {"value": "贵州", "count": 1} in response.json()["areas"]


def test_bj_instrument_bars_fall_back_to_honestly_labelled_raw_series(client, session):
    instrument = Instrument(
        code="920001",
        ts_code="920001.BJ",
        exchange="BJ",
        name="北交所样本",
        status="active",
    )
    session.add(instrument)
    session.flush()
    session.add(
        Bar(
            instrument_id=instrument.id,
            timeframe="DAY",
            adjustment="NONE",
            bar_time=datetime(2026, 7, 29),
            open=10,
            high=11,
            low=9.5,
            close=10.5,
            volume=1000,
        )
    )
    session.commit()
    response = client.get(f"/api/v1/instruments/{instrument.id}/bars")
    assert response.status_code == 200
    assert response.json()["adjustment"] == "NONE"
    assert response.json()["items"][0]["close"] == 10.5


def test_radar_history_uses_one_algorithm_version_and_unique_trade_dates(client, session):
    rows = [
        RadarSnapshot(
            trade_date=date(2026, 8, 1),
            algorithm_version="history-1.1",
            score=40,
            status="weak",
            status_label="偏弱",
            snapshot={"components": []},
            calculated_at=datetime(2026, 8, 1, 16, 0),
        ),
        RadarSnapshot(
            trade_date=date(2026, 8, 1),
            algorithm_version="history-2.0",
            score=55,
            status="neutral",
            status_label="震荡",
            snapshot={"components": []},
            calculated_at=datetime(2026, 8, 1, 17, 0),
        ),
        RadarSnapshot(
            trade_date=date(2026, 8, 2),
            algorithm_version="history-2.0",
            score=65,
            status="warm",
            status_label="偏强",
            snapshot={"components": []},
            calculated_at=datetime(2026, 8, 2, 17, 0),
        ),
    ]
    session.add_all(rows)
    session.commit()

    current = client.get("/api/v1/market/radar/history?limit=10")
    assert current.status_code == 200
    payload = current.json()
    assert payload["algorithm_version"] == "history-2.0"
    assert [item["trade_date"] for item in payload["items"]] == ["2026-08-01", "2026-08-02"]
    assert {item["algorithm_version"] for item in payload["items"]} == {"history-2.0"}

    legacy = client.get("/api/v1/market/radar/history?algorithm_version=history-1.1")
    assert legacy.status_code == 200
    assert [item["trade_date"] for item in legacy.json()["items"]] == ["2026-08-01"]
