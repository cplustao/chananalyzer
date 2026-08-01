from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.models import Instrument, IpoEvent
from backend.app.services.research_lists import enrich_ipo_items


def build_ipo_items(session: Session, start: date, end: date) -> list[dict]:
    rows = session.execute(
        select(IpoEvent, Instrument)
        .outerjoin(Instrument, Instrument.id == IpoEvent.instrument_id)
        .where(IpoEvent.listing_date.between(start, end))
        .order_by(IpoEvent.listing_date.desc(), IpoEvent.legacy_code)
    ).all()
    raw_items = []
    for event, instrument in rows:
        payload = dict(event.raw_payload or {})
        payload.update(
            {
                "instrument_id": event.instrument_id,
                "code": event.legacy_code,
                "ts_code": instrument.ts_code if instrument else payload.get("ts_code"),
                "name": payload.get("name") or (instrument.name if instrument else event.legacy_code),
                "industry": payload.get("industry")
                or (instrument.industry.name if instrument and instrument.industry else None),
                "list_date": event.listing_date.strftime("%Y%m%d"),
                "listing_date": event.listing_date.strftime("%Y%m%d"),
                "ipo_price": payload.get("ipo_price") or event.issue_price,
                "issue_price": event.issue_price,
                "ipo_pe": payload.get("ipo_pe") or event.issue_pe,
                "issue_pe": event.issue_pe,
            }
        )
        raw_items.append(payload)
    return enrich_ipo_items(session, raw_items, start, end)
