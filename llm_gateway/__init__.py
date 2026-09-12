"""Single controlled model-invocation boundary for Neu.Tail agents."""

from llm_gateway.gateway import (
    LLMGateway,
    LLMGatewayError,
    LLMInvocationError,
    StructuredOutputError,
)
from llm_gateway.models import IntentResult, LLMCallRecord, LLMCallStatus
from llm_gateway.prompt_registry import PromptRegistry
from llm_gateway.router import ModelRouter
from llm_gateway.telemetry import GatewayTelemetry

__all__ = [
    "GatewayTelemetry",
    "IntentResult",
    "LLMCallRecord",
    "LLMCallStatus",
    "LLMGateway",
    "LLMGatewayError",
    "LLMInvocationError",
    "ModelRouter",
    "PromptRegistry",
    "StructuredOutputError",
]

