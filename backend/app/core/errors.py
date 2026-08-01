from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization|api[-_]?key|token|password|secret)(\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
)


class AppError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def sanitize_text(value: object, *, limit: int = 2000) -> str:
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)bearer"):
            text = pattern.sub("Bearer [REDACTED]", text)
        else:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    return text[:limit]


def safe_validation_errors(errors: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": item.get("type"),
            "loc": list(item.get("loc") or []),
            "msg": item.get("msg"),
        }
        for item in errors
    ]
