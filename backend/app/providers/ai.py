from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass(frozen=True, slots=True)
class AIRequest:
    system_prompt: str
    user_prompt: str
    model: str | None = None
    temperature: float = 0.3
    response_format: Literal["text", "json_object"] = "text"
    max_tokens: int = 2000


class AIProvider(Protocol):
    key: str
    model: str
    last_provider: str | None
    last_model: str | None
    last_attempts: list[dict[str, Any]]
    fallback_reason: str | None

    async def generate(self, request: AIRequest) -> str: ...


def is_recoverable_ai_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    error_name = type(exc).__name__.lower()
    return any(
        marker in error_name
        for marker in ("timeout", "connection", "ratelimit", "temporarilyunavailable")
    )


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_key: str,
        *,
        key: str = "deepseek",
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
    ):
        if not api_key:
            raise ValueError("AI API key must not be empty")
        if not model:
            raise ValueError("AI model must not be empty")
        self.api_key = api_key
        self.key = key
        self.model = model
        self.base_url = base_url
        self.last_provider: str | None = None
        self.last_model: str | None = None
        self.last_attempts: list[dict[str, Any]] = []
        self.fallback_reason: str | None = None

    async def generate(self, request: AIRequest) -> str:
        from openai import AsyncOpenAI

        model = request.model or self.model
        self.last_provider = self.key
        self.last_model = model
        self.last_attempts = []
        self.fallback_reason = None
        client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=120.0)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                request_kwargs: dict[str, Any] = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                }
                if request.response_format == "json_object":
                    request_kwargs["response_format"] = {"type": "json_object"}
                response = await client.chat.completions.create(**request_kwargs)
                self.last_attempts.append(
                    {"provider": self.key, "model": model, "attempt": attempt, "outcome": "success"}
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                recoverable = is_recoverable_ai_error(exc)
                self.last_attempts.append(
                    {
                        "provider": self.key,
                        "model": model,
                        "attempt": attempt,
                        "outcome": "recoverable_error" if recoverable else "terminal_error",
                        "error_type": type(exc).__name__,
                        "status_code": getattr(exc, "status_code", None),
                    }
                )
                if not recoverable or attempt == 3:
                    raise
                await asyncio.sleep(2 ** (attempt - 1))
        raise RuntimeError(str(last_error) if last_error else "AI request failed")


class AIProviderChain:
    key = "provider-chain"

    def __init__(self, providers: list[OpenAICompatibleProvider]):
        if not providers:
            raise ValueError("at least one AI provider is required")
        self.providers = providers
        self.model = providers[0].model
        self.last_provider: str | None = None
        self.last_model: str | None = None
        self.last_attempts: list[dict[str, Any]] = []
        self.fallback_reason: str | None = None

    async def generate(self, request: AIRequest) -> str:
        self.last_provider = None
        self.last_model = None
        self.last_attempts = []
        self.fallback_reason = None
        last_error: Exception | None = None
        for index, provider in enumerate(self.providers):
            try:
                result = await provider.generate(request)
                self.last_provider = provider.last_provider
                self.last_model = provider.last_model
                self.last_attempts.extend(provider.last_attempts)
                return result
            except Exception as exc:
                self.last_attempts.extend(provider.last_attempts)
                last_error = exc
                if not is_recoverable_ai_error(exc) or index == len(self.providers) - 1:
                    raise
                if self.fallback_reason is None:
                    self.fallback_reason = f"{provider.key}:{type(exc).__name__}"
        raise RuntimeError(str(last_error) if last_error else "AI provider chain failed")