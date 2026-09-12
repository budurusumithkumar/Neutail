"""Neu.Tail LangGraph orchestration control plane.

Heavy control-plane exports are loaded lazily so the session service can import
the shared context model without creating a package import cycle.
"""

from __future__ import annotations

from typing import Any

from orchestrator.models import (
    AgentDescriptor,
    AgentResult,
    ChatRequest,
    ExecutionPlan,
    NeuTailState,
    OrchestratorRequest,
    OrchestratorResponse,
    SessionContext,
)


_AGENT_EXPORTS = {"AgentRegistry", "AgentUnavailableError"}
_CONTROL_EXPORTS = {
    "FastMCPIdentityValidator",
    "NeuTailOrchestrator",
    "OrchestratorCustomerNotFoundError",
    "OrchestratorDependencyError",
    "OrchestratorError",
    "ResponseSynthesizer",
}


def __getattr__(name: str) -> Any:
    if name in _AGENT_EXPORTS:
        from orchestrator import agent_registry

        return getattr(agent_registry, name)
    if name in _CONTROL_EXPORTS:
        from orchestrator import orchestrator

        return getattr(orchestrator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AgentDescriptor",
    "AgentRegistry",
    "AgentResult",
    "AgentUnavailableError",
    "ChatRequest",
    "ExecutionPlan",
    "FastMCPIdentityValidator",
    "NeuTailOrchestrator",
    "NeuTailState",
    "OrchestratorCustomerNotFoundError",
    "OrchestratorDependencyError",
    "OrchestratorError",
    "OrchestratorRequest",
    "OrchestratorResponse",
    "ResponseSynthesizer",
    "SessionContext",
]
