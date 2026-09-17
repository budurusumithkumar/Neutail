from __future__ import annotations

import asyncio

from agents.discovery.models import (
    DiscoveryResult,
    DiscoverySignal,
    RetrievalStrategy,
)
from models.dto import CustomerContext, CustomerPreferences
from models.fit import FitResult, FitSignal
from models.upsell import UpsellResult
from orchestrator.agent_registry import AgentRegistry
from orchestrator.models import ExecutionPlan, OrchestratorRequest, SessionContext
from orchestrator.orchestrator import NeuTailOrchestrator
from tools.permissions import AgentName
from tools.registry import TOOL_REGISTRY


class SignalDiscoveryAgent:
    async def execute(self, _request):
        return DiscoveryResult(
            status="SUCCESS",
            retrieval_strategy=RetrievalStrategy.STRUCTURED,
            recommendations=[],
            candidates_retrieved=1,
            candidates_after_filtering=1,
            downstream_signals=[
                DiscoverySignal(
                    signal_type="HIGH_PRODUCT_ENGAGEMENT",
                    sku="SKU00123",
                    strength=0.91,
                )
            ],
        )


class SignalFitAgent:
    async def execute(self, request):
        return FitResult(
            status="SUCCESS",
            sku=request.sku,
            requested_size=request.requested_size,
            recommended_size=request.requested_size,
            confidence=0.8,
            risk_score=0.82,
            risk_band="HIGH",
            action="SHOW_CAUTION",
            downstream_signals=[
                FitSignal(
                    signal_type="CHRONIC_FIT_RISK",
                    severity="HIGH",
                    strength=0.82,
                )
            ],
        )


class CapturingUpsellAgent:
    def __init__(self):
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        return UpsellResult(status="NO_OFFER", should_offer=False)


def _context() -> CustomerContext:
    return CustomerContext(
        customer_id="CUST001",
        segment="Prestige Champion",
        premium_affinity=0.91,
        price_sensitivity=0.20,
        preferences=CustomerPreferences(),
    )


def _state(step: AgentName, *, selected_sku: str | None = None):
    context = _context()
    session = SessionContext(
        session_id="signal-session",
        customer_id="CUST001",
        selected_sku=selected_sku,
        requested_size="12" if selected_sku else None,
        customer_context=context,
    )
    return {
        "request": OrchestratorRequest(
            customer_id="CUST001",
            session_id=session.session_id,
            message="test signal routing",
            trace_id="signal-trace",
            selected_sku=selected_sku,
        ),
        "session": session,
        "customer_context": context,
        "execution_plan": ExecutionPlan(steps=[step], reason="test"),
        "tool_catalog": {
            step: TOOL_REGISTRY.list_tools(step),
            AgentName.UPSELL: TOOL_REGISTRY.list_tools(AgentName.UPSELL),
        },
        "agent_outputs": {},
        "completed_agents": [],
        "errors": [],
    }


def test_discovery_high_engagement_routes_to_upsell_through_orchestrator():
    upsell = CapturingUpsellAgent()
    registry = AgentRegistry(
        discovery_agent=SignalDiscoveryAgent(), upsell_agent=upsell
    )
    orchestrator = NeuTailOrchestrator(agent_registry=registry)

    update = asyncio.run(
        orchestrator._execute_plan(_state(AgentName.DISCOVERY))
    )

    assert update["execution_plan"].steps == [
        AgentName.DISCOVERY,
        AgentName.UPSELL,
    ]
    assert update["completed_agents"] == [
        AgentName.DISCOVERY,
        AgentName.UPSELL,
    ]
    assert upsell.requests[0].trigger.trigger_type == "HIGH_PRODUCT_ENGAGEMENT"
    assert upsell.requests[0].trigger.source_agent == "DiscoveryAgent"
    assert upsell.requests[0].selected_sku == "SKU00123"


def test_fit_chronic_risk_routes_to_upsell_through_orchestrator():
    upsell = CapturingUpsellAgent()
    registry = AgentRegistry(fit_agent=SignalFitAgent(), upsell_agent=upsell)
    orchestrator = NeuTailOrchestrator(agent_registry=registry)

    update = asyncio.run(
        orchestrator._execute_plan(
            _state(AgentName.FIT, selected_sku="SKU00001")
        )
    )

    assert update["execution_plan"].steps == [AgentName.FIT, AgentName.UPSELL]
    assert upsell.requests[0].trigger.trigger_type == "CHRONIC_FIT_RISK"
    assert upsell.requests[0].trigger.source_agent == "FitAgent"
    assert upsell.requests[0].trigger.strength == 0.82
