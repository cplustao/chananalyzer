from __future__ import annotations

from datetime import date, timedelta

from backend.app.db.models import Instrument, TradingCalendar
from backend.app.services import research_plan as research_plan_module
from backend.app.services.research_plan import ResearchPlanDraftService, review_date_after

ZS = {"start_date": "2026/09/01", "end_date": "2026/10/15", "high": 12.0, "low": 10.0}


class FakeEngine:
    algorithm_version = "fake-1"

    def __init__(self, structure):
        self.structure = structure

    def analyze(self, code):
        return self.structure


def structure(*, current, buys=(), sells=(), zs=ZS, bi=(9.0, 11.0)):
    return {
        "end_date": "2026/11/09",
        "current_price": current,
        "zs_list": [zs] if zs else [],
        "bi_list": [{"start_price": bi[0], "end_price": bi[1], "is_sure": True}] if bi else [],
        "buy_signals": [{"type": kind, "date": day, "price": price} for kind, day, price in buys],
        "sell_signals": [{"type": "1", "date": day, "price": 1.0} for day in sells],
        "history_sufficient": True,
        "analysis_bar_count": 500,
    }


def stock(session):
    instrument = Instrument(code="300750", ts_code="300750.SZ", exchange="SZSE", name="宁德时代", status="active")
    session.add(instrument)
    session.commit()
    return instrument


def draft(session, data):
    return ResearchPlanDraftService(session, FakeEngine(data)).build(stock(session).id)


def test_third_buy_above_the_pivot_is_invalidated_by_falling_back_into_it(session):
    result = draft(session, structure(current=12.8, buys=[("3a", "2026/11/06", 12.4)]))

    assert result["thesis"].startswith("2026-11-06 出现日线三类买点 A（12.40）；当前价 12.80 在最近中枢 10.00–12.00")
    assert "上方" in result["thesis"]
    assert result["confirmation_trigger"] == "回抽不跌回中枢上沿 12.00 之下，并出现三类买点"
    assert result["invalidation_condition"] == "收盘跌回中枢上沿 12.00 之下，三类买点不成立"
    assert result["basis"] == {
        "structure_date": "2026-11-09", "algorithm_version": "fake-1",
        "history_sufficient": True, "analysis_bar_count": 500,
    }


def test_first_buy_is_invalidated_below_its_price(session):
    result = draft(session, structure(current=10.5, buys=[("1", "2026/11/04", 9.8)]))

    assert result["confirmation_trigger"] == "收盘向上突破中枢上沿 12.00，且回抽不再回到中枢内"
    assert result["invalidation_condition"] == "收盘跌破买点价格 9.80"


def test_buy_followed_by_a_sell_is_reported_but_not_used_as_the_stop(session):
    result = draft(session, structure(current=9.5, buys=[("1", "2026/10/20", 9.0)], sells=["2026/11/05"]))

    assert "最近的日线买点是 2026-10-20 的一类买点，之后 2026-11-05 出现了卖点" in result["thesis"]
    # Already below the pivot: the stop is the prior low, not the pivot floor it has already lost.
    assert result["invalidation_condition"] == "收盘跌破前低 9.00"
    assert result["confirmation_trigger"] == "回调不破前低 9.00 形成二类买点，或重新站上中枢下沿 10.00"


def test_price_below_the_buy_point_does_not_keep_it_as_the_stop(session):
    result = draft(session, structure(current=8.5, buys=[("2", "2026/11/06", 9.2)], bi=(11.0, 8.4)))

    assert "当前价已低于买点价格" in result["thesis"]
    assert result["invalidation_condition"] == "收盘跌破前低 8.40"


def test_no_pivot_yet(session):
    result = draft(session, structure(current=20.0, zs=None, bi=(30.0, 19.0)))

    assert result["thesis"].startswith("近期没有日线买点；最后一笔向下（30.00 → 19.00，已确认）")
    assert result["confirmation_trigger"] == "形成第一个日线中枢后再判断方向"
    assert result["invalidation_condition"] == "收盘跌破前低 19.00"


def test_review_is_five_sessions_later_and_falls_back_to_weekdays(session):
    day = date(2026, 11, 2)
    while day <= date(2026, 11, 11):
        session.add(TradingCalendar(exchange="CN", trade_date=day, is_open=day.weekday() < 5))
        day += timedelta(days=1)
    session.commit()

    # 11-10 and 11-11 are known; the rest are weekdays after the loaded calendar.
    assert review_date_after(session, date(2026, 11, 9)) == date(2026, 11, 16)
    assert review_date_after(session, date(2026, 11, 2)) == date(2026, 11, 9)


def test_draft_endpoint_and_indices_are_not_added(client, session, monkeypatch):
    instrument = stock(session)
    index = Instrument(code="sh.000300", ts_code="sh.000300", name="沪深300", status="active", asset_type="index")
    session.add(index)
    session.commit()
    monkeypatch.setattr(
        research_plan_module, "DatabaseChanEngine",
        lambda session: FakeEngine(structure(current=10.5, buys=[("1", "2026/11/04", 9.8)])),
    )

    rejected = client.post("/api/v1/watchlists/default/items", json={"instrument_id": index.id})
    assert rejected.status_code == 422
    assert client.post("/api/v1/watchlists/default/items", json={"instrument_id": 999999}).status_code == 404

    item = client.post("/api/v1/watchlists/default/items", json={"instrument_id": instrument.id}).json()["items"][0]
    response = client.get(f"/api/v1/watchlists/default/items/{item['id']}/plan-draft")

    assert response.status_code == 200
    body = response.json()
    assert body["invalidation_condition"] == "收盘跌破买点价格 9.80"
    assert body["next_review_date"] == "2026-11-16"
    # The draft is not saved by itself.
    saved = client.get("/api/v1/watchlists/default").json()["items"][0]
    assert saved["thesis"] is None and saved["next_review_date"] is None
    assert client.get("/api/v1/watchlists/default/items/missing/plan-draft").status_code == 404
