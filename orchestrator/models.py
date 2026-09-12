"""Typed contracts and shared graph state for the Neu.Tail orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional, TypedDict

from pydantic import Field

from llm_gateway.models import IntentResult
from models.dto import CustomerContext, DTOModel
from tools.contracts import ToolDescriptor
from tools.permissions import AgentName


IntentName = Literal[
    "PRODUCT_DISCOVERY",
    "FIT_QUERY",
    "SERVICE_QUERY",
    "CUSTOMER_CONTEXT",
    "GENERAL_QUERY",
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChatRequest(DTOModel):
    """UI-facing chat body; customer identity comes from the API boundary."""

    message: str = Field(min_length=1, max_length=2_000)
    session_id: str = Field(min_length=1, max_length=128)


class OrchestratorRequest(DTOModel):
    """Trusted request passed from FastAPI to the control plane."""

    customer_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=2_000)
    trace_id: str = Field(min_length=1, max_length=128)


class ConversationTurn(DTOModel):
    role: Literal["user", "assistant"]
    content: str
    trace_id: str
    intent: Optional[IntentName] = None
    created_at: datetime = Field(default_factory=_utc_now)


class SessionContext(DTOModel):
    """Process-local, multi-turn state owned by the session service."""

    session_id: str
    customer_id: str
    turn_count: int = Field(default=0, ge=0)
    last_intent: Optional[IntentName] = None
    category: Optional[str] = None
    occasion: Optional[str] = None
    selected_sku: Optional[str] = None
    requested_size: Optional[str] = None
    customer_context: Optional[CustomerContext] = None
    conversation: list[ConversationTurn] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def entity_context(self) -> dict[str, Optional[str]]:
        """Return only conversational entities suitable for prompt context."""

        return {
            "category": self.category,
            "occasion": self.occasion,
            "selected_sku": self.selected_sku,
            "requested_size": self.requested_size,
        }


class AgentDescriptor(DTOModel):
    """Discoverable routing metadata for one specialised agent."""

    name: AgentName
    display_name: str
    capabilities: list[str]
    supported_intents: list[IntentName]
    implemented: bool


class ExecutionPlan(DTOModel):
    """Ordered deterministic agent plan for the current turn."""

    steps: list[AgentName] = Field(default_factory=list)
    reason: str


class AgentRunStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class AgentResult(DTOModel):
    """Common structured result returned by agent adapters."""

    agent_name: AgentName
    status: AgentRunStatus
    state_updates: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None


class OrchestratorResponse(DTOModel):
    """Stable API result returned after state persistence."""

    trace_id: str
    session_id: str
    response: str
    intent: IntentName
    intent_confidence: float = Field(ge=0, le=1)
    execution_plan: list[AgentName] = Field(default_factory=list)
    completed_agents: list[AgentName] = Field(default_factory=list)
    extracted_entities: dict[str, Optional[str]] = Field(default_factory=dict)
    customer_context: Optional[CustomerContext] = None
    agent_outputs: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    turn_count: int = Field(ge=1)


class NeuTailState(TypedDict, total=False):
    """Internal LangGraph state shared across orchestrator nodes."""

    request: OrchestratorRequest
    session: SessionContext
    intent_result: IntentResult
    extracted_entities: dict[str, Optional[str]]
    tool_catalog: dict[AgentName, list[ToolDescriptor]]
    execution_plan: ExecutionPlan
    customer_context: CustomerContext
    discovery_result: dict[str, Any]
    fit_result: dict[str, Any]
    upsell_result: dict[str, Any]
    agent_outputs: dict[str, Any]
    completed_agents: list[AgentName]
    final_response: str
    errors: list[str]


__all__ = [
    "AgentDescriptor",
    "AgentResult",
    "AgentRunStatus",
    "ChatRequest",
    "ConversationTurn",
    "ExecutionPlan",
    "IntentName",
    "NeuTailState",
    "OrchestratorRequest",
    "OrchestratorResponse",
    "SessionContext",
]

