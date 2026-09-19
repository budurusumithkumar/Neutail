"""Explicit specialised-agent registry and common invocation contract."""

from __future__ import annotations

from typing import Protocol

from agents.discovery import DiscoveryAgent, DiscoveryCriteria, DiscoveryRequest
from agents.fit import FitAgent, FitRequest
from agents.profiling import ProfileAgent, ProfileAgentRequest
from agents.upsell import (
    UpsellAgent,
    UpsellRequest,
    UpsellTrigger,
    UpsellTriggerType,
)
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
    FIT_AGENT_TOOLS,
    PROFILE_AGENT_TOOLS,
    UPSELL_AGENT_REQUIRED_TOOLS,
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
        criteria_payload = state.get("discovery_criteria")
        criteria = (
            DiscoveryCriteria.model_validate(criteria_payload)
            if criteria_payload is not None
            else None
        )
        max_results = state.get("discovery_max_results", 5)
        result = await self.agent.execute(
            DiscoveryRequest(
                query=request.message,
                customer_context=state["customer_context"],
                session_context=state["session"],
                criteria=criteria,
                trace_id=request.trace_id,
                max_results=max_results,
                allow_llm_explanations=state.get(
                    "discovery_allow_llm_explanations", True
                ),
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


class FitAgentAdapter:
    """Adapt FitAgent while enforcing selected-product and tool boundaries."""

    def __init__(self, agent: FitAgent) -> None:
        self.agent = agent

    async def execute(
        self, state: NeuTailState, tools: list[ToolDescriptor]
    ) -> AgentResult:
        discovered_names = {tool.name for tool in tools}
        if discovered_names != FIT_AGENT_TOOLS:
            raise AgentDependencyError(
                "Fit Agent capability mismatch: "
                f"expected={sorted(FIT_AGENT_TOOLS)}, "
                f"actual={sorted(discovered_names)}"
            )

        session = state["session"]
        if not session.selected_sku:
            return AgentResult(
                agent_name=AgentName.FIT,
                status=AgentRunStatus.FAILED,
                state_updates={
                    "fit_result": {
                        "status": "INVALID_REQUEST",
                        "sku": None,
                        "requested_size": session.requested_size,
                        "reason_codes": ["SELECTED_SKU_REQUIRED"],
                        "errors": ["NO_SELECTED_SKU"],
                    }
                },
                error="SELECTED_SKU_REQUIRED",
            )

        request = state["request"]
        result = await self.agent.execute(
            FitRequest(
                customer_context=state["customer_context"],
                session_context=session,
                sku=session.selected_sku,
                requested_size=session.requested_size,
                trace_id=request.trace_id,
            )
        )
        successful = result.status in {"SUCCESS", "INSUFFICIENT_EVIDENCE"}
        return AgentResult(
            agent_name=AgentName.FIT,
            status=(
                AgentRunStatus.SUCCESS if successful else AgentRunStatus.FAILED
            ),
            state_updates={"fit_result": result.model_dump(mode="json")},
            error=None if successful else result.status,
        )


class UpsellAgentAdapter:
    """Adapt the governed UpsellAgent and preserve orchestrator-owned routing."""

    def __init__(self, agent: UpsellAgent) -> None:
        self.agent = agent

    async def execute(
        self, state: NeuTailState, tools: list[ToolDescriptor]
    ) -> AgentResult:
        discovered_names = {tool.name for tool in tools}
        missing = UPSELL_AGENT_REQUIRED_TOOLS.difference(discovered_names)
        if missing:
            raise AgentDependencyError(
                "Upsell Agent capability mismatch: "
                f"missing={sorted(missing)}"
            )

        session = state["session"]
        trigger_payload = state.get("upsell_trigger")
        if trigger_payload is not None:
            trigger = UpsellTrigger.model_validate(trigger_payload)
        else:
            raw_strength = session.attributes.get("styling_engagement_score", 0.0)
            strength = (
                float(raw_strength)
                if isinstance(raw_strength, (int, float))
                and not isinstance(raw_strength, bool)
                and 0 <= raw_strength <= 1
                else 0.0
            )
            trigger = UpsellTrigger(
                trigger_type=UpsellTriggerType.STYLING_ENGAGEMENT,
                source_agent="Orchestrator",
                sku=session.selected_sku,
                strength=strength,
            )

        raw_cart_value = session.attributes.get("cart_value_gbp")
        cart_value = (
            float(raw_cart_value)
            if isinstance(raw_cart_value, (int, float))
            and not isinstance(raw_cart_value, bool)
            and raw_cart_value >= 0
            else None
        )
        result = await self.agent.execute(
            UpsellRequest(
                customer_context=state["customer_context"],
                session_context=session,
                trigger=trigger,
                selected_sku=trigger.sku or session.selected_sku,
                cart_value_gbp=cart_value,
            )
        )
        successful = result.status in {"OFFER_AVAILABLE", "NO_OFFER"}
        return AgentResult(
            agent_name=AgentName.UPSELL,
            status=(
                AgentRunStatus.SUCCESS if successful else AgentRunStatus.FAILED
            ),
            state_updates={"upsell_result": result.model_dump(mode="json")},
            error=None if successful else "UPSELL_FAILED_CLOSED",
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
        capabilities=[
            "fit-evidence",
            "vector-fit-retrieval",
            "deterministic-size-guidance",
            "return-prevention",
        ],
        supported_intents=["FIT_QUERY"],
        implemented=True,
    ),
    AgentDescriptor(
        name=AgentName.UPSELL,
        display_name="UpsellAgent",
        capabilities=["service-eligibility", "offer-explanation"],
        supported_intents=["SERVICE_QUERY"],
        implemented=True,
    ),
)


class AgentRegistry:
    """Own agent metadata while keeping domain execution in specialised agents."""

    def __init__(
        self,
        profile_agent: ProfileAgent | None = None,
        discovery_agent: DiscoveryAgent | None = None,
        fit_agent: FitAgent | None = None,
        upsell_agent: UpsellAgent | None = None,
        llm_gateway: LLMGateway | None = None,
    ) -> None:
        shared_gateway = llm_gateway or LLMGateway()
        self._adapters: dict[AgentName, OrchestratedAgent] = {
            AgentName.PROFILING: ProfilingAgentAdapter(
                profile_agent or ProfileAgent()
            ),
            AgentName.DISCOVERY: DiscoveryAgentAdapter(
                discovery_agent
                or DiscoveryAgent(llm_gateway=shared_gateway)
            ),
            AgentName.FIT: FitAgentAdapter(
                fit_agent or FitAgent(llm_gateway=shared_gateway)
            ),
            AgentName.UPSELL: UpsellAgentAdapter(
                upsell_agent or UpsellAgent(llm_gateway=shared_gateway)
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
    "FitAgentAdapter",
    "OrchestratedAgent",
    "ProfilingAgentAdapter",
    "UpsellAgentAdapter",
]
