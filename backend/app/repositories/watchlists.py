from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import Instrument, Tag, Watchlist, WatchlistItem


class WatchlistRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_default(self, user_id: str) -> Watchlist:
        watchlist = self.session.scalar(
            select(Watchlist)
            .options(selectinload(Watchlist.items).selectinload(WatchlistItem.tags))
            .execution_options(populate_existing=True)
            .where(Watchlist.user_id == user_id, Watchlist.is_default.is_(True))
        )
        if watchlist is None:
            watchlist = Watchlist(user_id=user_id, name="默认自选", is_default=True)
            self.session.add(watchlist)
            self.session.commit()
        return watchlist

    def _tags(self, user_id: str, tag_names: list[str]) -> list[Tag]:
        tags: list[Tag] = []
        for name in sorted({value.strip() for value in tag_names if value.strip()}):
            tag = self.session.scalar(select(Tag).where(Tag.user_id == user_id, Tag.name == name))
            if tag is None:
                tag = Tag(user_id=user_id, name=name)
                self.session.add(tag)
            tags.append(tag)
        return tags

    def add_item(
        self, user_id: str, instrument_id: int, note: str | None, tag_names: list[str],
        *, thesis: str | None = None, confirmation_trigger: str | None = None,
        invalidation_condition: str | None = None, next_action: str | None = None,
        next_review_date=None, research_status: str = "watching",
    ) -> Watchlist:
        if self.session.get(Instrument, instrument_id) is None:
            raise ValueError("股票不存在")
        watchlist = self.get_default(user_id)
        item = self.session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.watchlist_id == watchlist.id,
                WatchlistItem.instrument_id == instrument_id,
            )
        )
        if item is None:
            item = WatchlistItem(
                watchlist_id=watchlist.id,
                instrument_id=instrument_id,
                position=len(watchlist.items),
                note=note,
                thesis=thesis,
                confirmation_trigger=confirmation_trigger,
                invalidation_condition=invalidation_condition,
                next_action=next_action,
                next_review_date=next_review_date,
                research_status=research_status,
            )
            item.tags = self._tags(user_id, tag_names)
            self.session.add(item)
        else:
            if note is not None:
                item.note = note
            if tag_names:
                item.tags = self._tags(user_id, tag_names)
            for key, value in {
                "thesis": thesis,
                "confirmation_trigger": confirmation_trigger,
                "invalidation_condition": invalidation_condition,
                "next_action": next_action,
                "next_review_date": next_review_date,
            }.items():
                if value is not None:
                    setattr(item, key, value)
            if research_status != "watching":
                item.research_status = research_status
        self.session.commit()
        return self.get_default(user_id)

    def update_item(
        self,
        user_id: str,
        item_id: str,
        note: str | None,
        tag_names: list[str],
        position: int | None = None,
        *,
        thesis: str | None = None,
        confirmation_trigger: str | None = None,
        invalidation_condition: str | None = None,
        next_action: str | None = None,
        next_review_date=None,
        research_status: str = "watching",
    ) -> Watchlist:
        watchlist = self.get_default(user_id)
        item = self.session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.id == item_id,
                WatchlistItem.watchlist_id == watchlist.id,
            )
        )
        if item is None:
            raise ValueError("自选股记录不存在")
        item.note = note
        item.tags = self._tags(user_id, tag_names)
        item.thesis = thesis
        item.confirmation_trigger = confirmation_trigger
        item.invalidation_condition = invalidation_condition
        item.next_action = next_action
        item.next_review_date = next_review_date
        item.research_status = research_status
        if position is not None:
            item.position = position
        self.session.commit()
        return self.get_default(user_id)

    def remove_item(self, user_id: str, item_id: str) -> Watchlist:
        watchlist = self.get_default(user_id)
        item = self.session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.id == item_id,
                WatchlistItem.watchlist_id == watchlist.id,
            )
        )
        if item is not None:
            self.session.delete(item)
            self.session.commit()
        return self.get_default(user_id)
