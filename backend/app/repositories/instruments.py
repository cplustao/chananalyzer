from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from backend.app.db.models import Bar, Industry, Instrument


class InstrumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(self, query: str | None, page: int, page_size: int) -> tuple[list[Instrument], int]:
        valid_stock = func.length(Instrument.code) == 6
        statement = (
            select(Instrument)
            .options(selectinload(Instrument.industry))
            .where(valid_stock)
        )
        count_statement = select(func.count()).select_from(Instrument).where(valid_stock)
        if query:
            pattern = f"%{query.strip()}%"
            condition = or_(Instrument.code.like(pattern), Instrument.name.like(pattern), Instrument.ts_code.like(pattern))
            statement = statement.where(condition)
            count_statement = count_statement.where(condition)
        total = int(self.session.scalar(count_statement) or 0)
        items = self.session.scalars(
            statement.order_by(Instrument.code).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return list(items), total
    def facets(self) -> dict[str, object]:
        eligible = (
            func.length(Instrument.code) == 6,
            Instrument.status == "active",
        )
        industries = self.session.execute(
            select(Industry.name, func.count(Instrument.id))
            .join(Instrument, Instrument.industry_id == Industry.id)
            .where(*eligible)
            .group_by(Industry.name)
            .order_by(func.count(Instrument.id).desc(), Industry.name)
        ).all()
        areas = self.session.execute(
            select(Instrument.area, func.count(Instrument.id))
            .where(*eligible, Instrument.area.is_not(None), Instrument.area != "")
            .group_by(Instrument.area)
            .order_by(func.count(Instrument.id).desc(), Instrument.area)
        ).all()
        total = int(
            self.session.scalar(
                select(func.count()).select_from(Instrument).where(*eligible)
            )
            or 0
        )
        return {
            "industries": [{"value": str(name), "count": int(count)} for name, count in industries],
            "areas": [{"value": str(name), "count": int(count)} for name, count in areas],
            "eligible_count": total,
        }


    def get(self, instrument_id: int) -> Instrument | None:
        return self.session.scalar(
            select(Instrument).options(selectinload(Instrument.industry)).where(Instrument.id == instrument_id)
        )

    def get_by_code(self, code: str) -> Instrument | None:
        return self.session.scalar(
            select(Instrument).options(selectinload(Instrument.industry)).where(Instrument.code == code)
        )

    def bars(self, instrument_id: int, timeframe: str, adjustment: str, limit: int) -> list[Bar]:
        descending = self.session.scalars(
            select(Bar)
            .where(Bar.instrument_id == instrument_id, Bar.timeframe == timeframe, Bar.adjustment == adjustment)
            .order_by(Bar.bar_time.desc())
            .limit(limit)
        ).all()
        return list(reversed(descending))
