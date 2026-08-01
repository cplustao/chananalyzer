from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.providers.ai import AIProvider, AIRequest


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    source: str = Field(min_length=1)
    data_time: str = Field(min_length=1)


class StructuredResearchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_status: Literal["complete", "insufficient_data"]
    summary: str = Field(min_length=1)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=12)
    counter_evidence: list[EvidenceItem] = Field(min_length=1, max_length=12)
    risks: list[str] = Field(min_length=1, max_length=12)
    missing_data: list[str] = Field(max_length=20)
    upgrade_conditions: list[str] = Field(min_length=1, max_length=12)
    downgrade_conditions: list[str] = Field(min_length=1, max_length=12)
    confidence: float = Field(ge=0, le=1)
    requires_human_review: bool
    authoritative_rule_fields: dict[str, Any]


@dataclass(slots=True)
class GeneratedReport:
    payload: dict[str, Any] | None
    markdown: str
    validation_status: str
    validation_error: str | None
    input_digest: str
    independent_review: bool = False
    provider: str | None = None
    model: str | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    fallback_reason: str | None = None

    @property
    def successful(self) -> bool:
        return self.validation_status == "validated"

    def generation_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "fallback_reason": self.fallback_reason,
            "input_digest": self.input_digest,
            "validation_status": self.validation_status,
        }


def input_digest(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def failed_report(message: str, snapshot: dict[str, Any]) -> GeneratedReport:
    return GeneratedReport(None, message, "failed", message, input_digest(snapshot))


async def generate_validated_report(
    provider: AIProvider,
    *,
    system_prompt: str,
    user_prompt: str,
    authoritative_rule_fields: dict[str, Any],
    snapshot: dict[str, Any],
    independent_review: bool = False,
    max_attempts: int = 2,
) -> GeneratedReport:
    digest = input_digest(snapshot)
    if authoritative_rule_fields.get("input_freshness") in {"stale", "missing"}:
        return GeneratedReport(
            None,
            "输入数据已过期，未生成成功报告。",
            "failed",
            "stale_input",
            digest,
            independent_review,
        )
    schema = StructuredResearchOutput.model_json_schema()
    contract = (
        "Return one JSON object only. Do not use Markdown fences. "
        "authoritative_rule_fields is read-only and must be echoed byte-for-byte equivalent. "
        f"JSON Schema: {json.dumps(schema, ensure_ascii=False)}"
    )
    last_error = "empty_response"
    attempt_history: list[dict[str, Any]] = []
    for _attempt in range(max_attempts):
        try:
            raw = await provider.generate(
                AIRequest(
                    system_prompt=f"{system_prompt}\n\n{contract}",
                    user_prompt=(
                        "Authoritative rule fields (read-only):\n"
                        f"{json.dumps(authoritative_rule_fields, ensure_ascii=False, default=str)}\n\n"
                        f"{user_prompt}"
                    ),
                    temperature=0.15,
                    max_tokens=2600,
                    response_format="json_object",
                )
            )
            attempt_history.extend(getattr(provider, "last_attempts", []))
            if not raw.strip():
                raise ValueError("empty_response")
            parsed = StructuredResearchOutput.model_validate_json(raw)
            if parsed.authoritative_rule_fields != authoritative_rule_fields:
                raise ValueError("authoritative_rule_fields_changed")
            payload = parsed.model_dump(mode="json")
            return GeneratedReport(
                payload,
                render_markdown(parsed),
                "validated",
                None,
                digest,
                independent_review,
                getattr(provider, "last_provider", None),
                getattr(provider, "last_model", None),
                attempt_history,
                getattr(provider, "fallback_reason", None),
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            last_error = str(exc)[:2000]
    return GeneratedReport(
        None,
        "AI 结构校验失败，未生成成功报告。",
        "failed",
        last_error,
        digest,
        independent_review,
        getattr(provider, "last_provider", None),
        getattr(provider, "last_model", None),
        attempt_history,
        getattr(provider, "fallback_reason", None),
    )


def render_markdown(report: StructuredResearchOutput) -> str:
    evidence = "\n".join(
        f"- {item.claim}（{item.source}，{item.data_time}）" for item in report.evidence
    )
    counter = "\n".join(
        f"- {item.claim}（{item.source}，{item.data_time}）" for item in report.counter_evidence
    )
    risks = "\n".join(f"- {item}" for item in report.risks)
    missing = "\n".join(f"- {item}" for item in report.missing_data) or "- 无"
    upgrades = "\n".join(f"- {item}" for item in report.upgrade_conditions)
    downgrades = "\n".join(f"- {item}" for item in report.downgrade_conditions)
    review = "是" if report.requires_human_review else "否"
    return (
        f"## 摘要\n\n{report.summary}\n\n## 证据\n\n{evidence}\n\n"
        f"## 反证\n\n{counter}\n\n## 风险\n\n{risks}\n\n## 缺失数据\n\n{missing}\n\n"
        f"## 升级条件\n\n{upgrades}\n\n## 降级条件\n\n{downgrades}\n\n"
        f"置信度：{report.confidence:.0%}；需要人工复核：{review}。"
    )