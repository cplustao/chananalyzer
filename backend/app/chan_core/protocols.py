from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ProgressCallback = Callable[[int, int, int], None]
ScanOutcomeStatus = Literal["matched", "no_signal", "filtered", "failed"]


@dataclass(slots=True)
class ScanSubjectOutcome:
    code: str
    status: ScanOutcomeStatus
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass(slots=True)
class ScanBatchOutcome:
    matches: list[dict[str, Any]]
    subjects: list[ScanSubjectOutcome]

    @property
    def failures(self) -> list[ScanSubjectOutcome]:
        return [item for item in self.subjects if item.status == "failed"]


class ChanEngine(Protocol):
    def analyze(self, code: str) -> dict[str, Any]: ...

    def scan(
        self,
        *,
        stock_codes: list[str],
        buy_types: list[str],
        sell_types: list[str],
        progress_callback: ProgressCallback | None = None,
        industries: list[str] | None = None,
        areas: list[str] | None = None,
        exclude_st: bool = True,
    ) -> ScanBatchOutcome: ...