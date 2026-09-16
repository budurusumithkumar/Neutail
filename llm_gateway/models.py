"""Pydantic contracts for routing, prompts, outputs, and accounting."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from llm_gateway.policies import GatewayPolicy


class GatewayModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CompletionOptions(GatewayModel):
    """Governed model-specific options forwarded through LiteLLM."""

    reasoning_effort: Optional[Literal["low", "medium", "high", "max"]] = None
    clear_thinking: Optional[bool] = None


class ModelRoute(GatewayModel):
    """Resolved logical route for one business use case."""

    use_case: str
    logical_model: str
    primary_model: str
    fallback_model: Optional[str] = None
    prompt_group: str
    default_prompt_version: str = "v1"
    policy: GatewayPolicy
    primary_options: CompletionOptions = Field(default_factory=CompletionOptions)
    fallback_options: CompletionOptions = Field(default_factory=CompletionOptions)

    @model_validator(mode="after")
    def distinct_fallback(self) -> "ModelRoute":
        if self.fallback_model == self.primary_model:
            raise ValueError("fallback_model must differ from primary_model")
        return self


class PromptDefinition(GatewayModel):
    """Trusted, versioned prompt loaded from the central registry."""

    name: str
    version: str
    system: str
    user_template: str
    required_context: list[str] = Field(default_factory=list)

    def render(self, context: dict[str, Any]) -> list[dict[str, str]]:
        missing = sorted(set(self.required_context).difference(context))
        if missing:
            raise ValueError(f"Missing prompt context fields: {missing}")
        normalized = {
            key: _render_value(value) for key, value in context.items()
        }
        try:
            user = self.user_template.format_map(normalized)
        except KeyError as exc:
            raise ValueError(
                f"Missing prompt context field: {exc.args[0]}"
            ) from exc
        return [
            {"role": "system", "content": self.system.strip()},
            {"role": "user", "content": user.strip()},
        ]


class TokenUsage(GatewayModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def calculate_total(self) -> "TokenUsage":
        if self.total_tokens == 0:
            self.total_tokens = self.input_tokens + self.output_tokens
        return self


class LLMCallStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FALLBACK_SUCCESS = "FALLBACK_SUCCESS"
    FAILED = "FAILED"
    INVALID_OUTPUT = "INVALID_OUTPUT"


class LLMCallRecord(GatewayModel):
    """Sanitized audit record for one physical provider invocation."""

    trace_id: str
    session_id: Optional[str] = None
    agent_name: str
    use_case: str
    prompt_name: str
    prompt_version: str
    logical_model: str
    provider: str
    provider_model: str
    attempt: int = Field(ge=1)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(ge=0)
    status: LLMCallStatus
    estimated_cost_usd: Optional[float] = Field(default=None, ge=0)
    error_type: Optional[str] = None


class IntentResult(GatewayModel):
    """Structured intent and entities consumed by the orchestrator."""

    intent: Literal[
        "PRODUCT_DISCOVERY",
        "FIT_QUERY",
        "SERVICE_QUERY",
        "CUSTOMER_CONTEXT",
        "GENERAL_QUERY",
    ]
    secondary_intents: list[
        Literal["PRODUCT_DISCOVERY", "FIT_QUERY", "SERVICE_QUERY"]
    ] = Field(default_factory=list)
    category: Optional[str] = None
    occasion: Optional[str] = None
    requested_size: Optional[str] = None
    selected_sku: Optional[str] = None
    confidence: float = Field(ge=0, le=1)

    @field_validator("intent", mode="before")
    @classmethod
    def normalize_legacy_upsell_intent(cls, value: Any) -> Any:
        """Accept the gateway's former name while publishing one canonical intent."""

        return "SERVICE_QUERY" if value == "UPSELL_QUERY" else value

    @field_validator("secondary_intents", mode="before")
    @classmethod
    def normalize_secondary_intents(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        return ["SERVICE_QUERY" if item == "UPSELL_QUERY" else item for item in value]


def _render_value(value: Any) -> str:
    import json

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if value is None:
        return "null"
    return str(value)


__all__ = [
    "CompletionOptions",
    "GatewayModel",
    "IntentResult",
    "LLMCallRecord",
    "LLMCallStatus",
    "ModelRoute",
    "PromptDefinition",
    "TokenUsage",
]
