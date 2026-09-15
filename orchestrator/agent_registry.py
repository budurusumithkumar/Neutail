"""Explicit specialised-agent registry and common invocation contract."""

from __future__ import annotations

from typing import Protocol

from agents.discovery import DiscoveryAgent, DiscoveryRequest
from agents.profiling import ProfileAgent, ProfileAgentRequest
from llm_gateway import LLMGateway
from orchestrator.models import (
    AgentDescriptor,
    AgentResult,
    AgentRunStatus,
    NeuTailState,
)
from tools.contracts import ToolDescriptor
from tools.permissions import (
    AgentName,
    DISCOVERY_AGENT_TOOLS,
    PROFILE_AGENT_TOOLS,
)


class AgentUnavailableError(RuntimeError):
    """Raised when a planned specialised agent is not implemented."""

    def __init__(self, agent_name: AgentName) -> None:
        self.agent_name = agent_name
        super().__init__(f"{agent_name.value} is not available in this demo slice")


class AgentDependencyError(RuntimeError):
    """Raised when runtime discovery does not satisfy an agent contract."""


class OrchestratedAgent(Protocol):
    async def execute(
        self, state: NeuTailState, tools: list[ToolDescriptor]
    ) -> AgentResult: ...


class ProfilingAgentAdapter:
    """Adapt the existing ProfileAgent to the common orchestrator interface."""

    def __init__(self, agent: ProfileAgent) -> None:
        self.agent = agent

    async def execute(
        self, state: NeuTailState, tools: list[ToolDescriptor]
    ) -> AgentResult:
        discovered_names = {tool.name for tool in tools}
        if discovered_names != PROFILE_AGENT_TOOLS:
            raise AgentDependencyError(
                "Profiling Agent capability mismatch: "
                f"expected={sorted(PROFILE_AGENT_TOOLS)}, "
                f"actual={sorted(discovered_names)}"
            )
        request = state["request"]
        context = await self.agent.execute(
            ProfileAgentRequest(
                customer_id=request.customer_id,
                session_id=request.session_id,
                trace_id=request.trace_id,
            )
        )
        return AgentResult(
            agent_name=AgentName.PROFILING,
            status=AgentRunStatus.SUCCESS,
            state_updates={"customer_context": context},
        )


class DiscoveryAgentAdapter:
    """Adapt DiscoveryAgent without leaking orchestrator concerns into it."""

    def __init__(self, agent: DiscoveryAgent) -> None:
        self.agent = agent

    async def execute(
        self, state: NeuTailState, tools: list[ToolDescriptor]
    ) -> AgentResult:
        discovered_names = {tool.name for tool in tools}
        if discovered_names != DISCOVERY_AGENT_TOOLS:
            raise AgentDependencyError(
                "Discovery Agent capability mismatch: "
                f"expected={sorted(DISCOVERY_AGENT_TOOLS)}, "
                f"actual={sorted(discovered_names)}"
            )

        request = state["request"]
        result = await self.agent.execute(
            DiscoveryRequest(
                query=request.message,
                customer_context=state["customer_context"],
                session_context=state["session"],
                trace_id=request.trace_id,
            )
        )
        successful = result.status in {"SUCCESS", "NO_RESULTS"}
        return AgentResult(
            agent_name=AgentName.DISCOVERY,
            status=(
                AgentRunStatus.SUCCESS if successful else AgentRunStatus.FAILED
            ),
            state_updates={
                "discovery_result": result.model_dump(mode="json")
            },
            error=None if successful else "DISCOVERY_FAILED",
        )


_DESCRIPTORS = (
    AgentDescriptor(
        name=AgentName.PROFILING,
        display_name="ProfilingAgent",
        capabilities=["customer-context", "deterministic-segmentation"],
        supported_intents=[
            "CUSTOMER_CONTEXT",
            "PRODUCT_DISCOVERY",
            "FIT_QUERY",
            "SERVICE_QUERY",
        ],
        implemented=True,
    ),
    AgentDescriptor(
        name=AgentName.DISCOVERY,
        display_name="DiscoveryAgent",
        capabilities=[
            "structured-product-search",
            "semantic-product-search",
            "inventory-filtering",
            "deterministic-personalized-ranking",
        ],
        supported_intents=["PRODUCT_DISCOVERY"],
        implemented=True,
    ),
    AgentDescriptor(
        name=AgentName.FIT,
        display_name="FitAgent",
        capabilities=["fit-evidence", "size-guidance"],
        supported_intents=["FIT_QUERY"],
        implemented=False,
    ),
    AgentDescriptor(
        name=AgentName.UPSELL,
        display_name="UpsellAgent",
        capabilities=["service-eligibility", "offer-explanation"],
        supported_intents=["SERVICE_QUERY"],
        implemented=False,
    ),
)


class AgentRegistry:
    """Own agent metadata while keeping domain execution in specialised agents."""

    def __init__(
        self,
        profile_agent: ProfileAgent | None = None,
        discovery_agent: DiscoveryAgent | None = None,
        llm_gateway: LLMGateway | None = None,
    ) -> None:
        self._adapters: dict[AgentName, OrchestratedAgent] = {
            AgentName.PROFILING: ProfilingAgentAdapter(
                profile_agent or ProfileAgent()
            ),
            AgentName.DISCOVERY: DiscoveryAgentAdapter(
                discovery_agent
                or DiscoveryAgent(llm_gateway=llm_gateway or LLMGateway())
            ),
        }
        self._descriptors = {item.name: item for item in _DESCRIPTORS}

    def list_agents(self) -> list[AgentDescriptor]:
        return [
            descriptor.model_copy(
                deep=True,
                update={"implemented": name in self._adapters},
            )
            for name, descriptor in self._descriptors.items()
        ]

    def describe(self, agent_name: AgentName) -> AgentDescriptor:
        try:
            descriptor = self._descriptors[agent_name]
        except KeyError as exc:
            raise KeyError(f"Unknown agent: {agent_name!r}") from exc
        return descriptor.model_copy(
            deep=True,
            update={"implemented": agent_name in self._adapters},
        )

    async def invoke(
        self,
        agent_name: AgentName,
        state: NeuTailState,
        tools: list[ToolDescriptor],
    ) -> AgentResult:
        adapter = self._adapters.get(agent_name)
        if adapter is None:
            raise AgentUnavailableError(agent_name)
        return await adapter.execute(state, tools)


__all__ = [
    "AgentDependencyError",
    "AgentRegistry",
    "AgentUnavailableError",
    "DiscoveryAgentAdapter",
    "OrchestratedAgent",
    "ProfilingAgentAdapter",
]
