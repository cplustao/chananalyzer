from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from backend.app.api.deps import current_user
from backend.app.api.schemas import WatchlistItemInput, WatchlistItemUpdate, WatchlistView
from backend.app.db.models import User
from backend.app.db.session import get_session
from backend.app.repositories.watchlists import WatchlistRepository
from backend.app.services.views import watchlist_view

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


@router.get("/default", response_model=WatchlistView)
def get_default_watchlist(
    session: Session = Depends(get_session), user: User = Depends(current_user)
) -> WatchlistView:
    return watchlist_view(WatchlistRepository(session).get_default(user.id))


@router.post("/default/items", response_model=WatchlistView)
def add_watchlist_item(
    payload: WatchlistItemInput,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> WatchlistView:
    try:
        item = WatchlistRepository(session).add_item(
            user.id, payload.instrument_id, payload.note, payload.tag_names
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return watchlist_view(item)


@router.put("/default/items/{item_id}", response_model=WatchlistView)
def update_watchlist_item(
    item_id: str,
    payload: WatchlistItemUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> WatchlistView:
    try:
        watchlist = WatchlistRepository(session).update_item(
            user.id, item_id, payload.note, payload.tag_names, payload.position
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return watchlist_view(watchlist)


@router.delete("/default/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_watchlist_item(
    item_id: str,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> Response:
    WatchlistRepository(session).remove_item(user.id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)