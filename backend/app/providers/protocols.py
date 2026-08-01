from __future__ import annotations

from typing import Any, Protocol


class MarketDataProvider(Protocol):
    def hot_stocks(self, rank_type: str, top_n: int) -> list[dict[str, Any]]: ...
class AIProvider(Protocol):
    async def complete(self, *, system: str, user: str, temperature: float = 0.3, max_tokens: int = 2000) -> str: ...