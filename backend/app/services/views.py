from __future__ import annotations

from backend.app.api.schemas import InstrumentView, WatchlistItemView, WatchlistView
from backend.app.db.models import Instrument, Watchlist


def instrument_view(item: Instrument) -> InstrumentView:
    return InstrumentView(
        id=item.id,
        code=item.code,
        ts_code=item.ts_code,
        exchange=item.exchange,
        name=item.name,
        industry=item.industry.name if item.industry else None,
        area=item.area,
        status=item.status,
        asset_type=item.asset_type,
    )


def watchlist_view(item: Watchlist) -> WatchlistView:
    return WatchlistView(
        id=item.id,
        name=item.name,
        is_default=item.is_default,
        items=[
            WatchlistItemView(
                id=entry.id,
                instrument=instrument_view(entry.instrument),
                position=entry.position,
                note=entry.note,
                tags=[tag.name for tag in entry.tags],
                thesis=entry.thesis,
                confirmation_trigger=entry.confirmation_trigger,
                invalidation_condition=entry.invalidation_condition,
                next_action=entry.next_action,
                next_review_date=entry.next_review_date,
                research_status=entry.research_status,
            )
            for entry in item.items
        ],
    )
