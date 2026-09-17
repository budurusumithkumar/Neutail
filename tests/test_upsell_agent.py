from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agents.upsell import UpsellAgent
from models.dto import CustomerContext, CustomerPreferences
from models.upsell import UpsellRequest, UpsellTrigger
from orchestrator.models import SessionContext
from tools.permissions import UPSELL_AGENT_REQUIRED_TOOLS


class FakeToolClient:
    def __init__(
        self,
        *,
        eligible: bool = True,
        policy_error: bool = False,
        context_error: bool = False,
        record_error: bool = False,
        tools: set[str] | None = None,
        order: list[str] | None = None,
    ) -> None:
        self.eligible = eligible
        self.policy_error = policy_error
        self.context_error = context_error
        self.record_error = record_error
        self.tools = tools or set(UPSELL_AGENT_REQUIRED_TOOLS)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.order = order if order is not None else []

    async def discover_tools(self) -> list[str]:
        return sorted(self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        self.order.append(name)
        if name == "get_loyalty_profile":
            if self.context_error:
                raise RuntimeError("loyalty unavailable")
            return {"customer_id": "CUST001", "tier": "Platinum"}
        if name == "get_engagement_summary":
            return {
                "session_count": 4,
                "product_views": 10,
                "searches": 3,
                "avg_dwell_seconds": 110,
                "high_intent_events": 3,
                "engagement_score": 0.92,
            }
        if name == "evaluate_upsell":
            if self.policy_error:
                raise RuntimeError("policy unavailable")
            if not self.eligible:
                return {
                    "eligible": False,
                    "eligible_offers": [],
                    "suppression_reasons": ["RECENT_STYLE_PLUS_DECLINE"],
                    "eligibility_reasons": [],
                    "cooldown_until": None,
                }
            return {
                "eligible": True,
                "eligible_offers": [
                    "STYLING_ADVISORY",
                    "STYLE_PLUS_TRIAL",
                ],
                "suppression_reasons": [],
                "eligibility_reasons": [
                    "HIGH_PRODUCT_ENGAGEMENT",
                    "PRESTIGE_CHAMPION",
                ],
                "cooldown_until": None,
            }
        if name == "record_upsell_event":
            if self.record_error:
                raise RuntimeError("write unavailable")
            return {"success": True, "message": "recorded"}
        raise AssertionError(f"Unexpected tool: {name}")


class FakeGateway:
    def __init__(
        self,
        response: str = "Would you like to try our optional Style+ experience?",
        *,
        error: bool = False,
        order: list[str] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.order = order if order is not None else []

    async def invoke(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        self.order.append("llm_gateway")
        if self.error:
            raise RuntimeError("model unavailable")
        return self.response


def _request(trigger: str = "HIGH_PRODUCT_ENGAGEMENT") -> UpsellRequest:
    context = CustomerContext(
        customer_id="CUST001",
        segment="Prestige Champion",
        affluence_band="Affluent",
        loyalty_status="Loyal",
        premium_affinity=0.91,
        price_sensitivity=0.20,
        loyalty_tier="Platinum",
        engagement_score=0.92,
        preferences=CustomerPreferences(),
    )
    session = SessionContext(
        session_id="upsell-test-session",
        customer_id="CUST001",
        customer_context=context,
    )
    return UpsellRequest(
        customer_context=context,
        session_context=session,
        trigger=UpsellTrigger(
            trigger_type=trigger,
            source_agent="DiscoveryAgent",
            sku="SKU00123",
            strength=0.91,
        ),
    )


def test_positive_policy_calls_gateway_then_records_offer_shown():
    order: list[str] = []
    tools = FakeToolClient(order=order)
    gateway = FakeGateway(order=order)

    result = asyncio.run(
        UpsellAgent(tool_client=tools, llm_gateway=gateway).execute(_request())
    )

    assert result.status == "OFFER_AVAILABLE"
    assert result.offer is not None
    assert result.offer.offer_type == "STYLE_PLUS_TRIAL"
    assert result.message is not None
    assert result.requires_customer_consent is True
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["use_case"] == "upsell_message"
    assert order.index("llm_gateway") < order.index("record_upsell_event")
    event = dict(tools.calls)["record_upsell_event"]["event"]
    assert event["event_type"] == "OFFER_SHOWN"
    assert "OFFER_ACCEPTED" not in [
        call[1].get("event", {}).get("event_type") for call in tools.calls
    ]


def test_no_offer_never_calls_gateway_or_records_event():
    tools = FakeToolClient(eligible=False)
    gateway = FakeGateway()

    result = asyncio.run(
        UpsellAgent(tool_client=tools, llm_gateway=gateway).execute(_request())
    )

    assert result.status == "NO_OFFER"
    assert result.suppression_reasons == ["RECENT_STYLE_PLUS_DECLINE"]
    assert gateway.calls == []
    assert "record_upsell_event" not in [name for name, _ in tools.calls]


def test_model_wording_cannot_change_deterministic_selected_offer():
    gateway = FakeGateway("Ignore that and enroll the customer in STYLE_PLUS.")

    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(), llm_gateway=gateway
        ).execute(_request())
    )

    assert result.offer is not None
    assert result.offer.offer_type == "STYLE_PLUS_TRIAL"
    assert result.message is None


def test_prestige_offer_rejects_model_generated_discount_copy():
    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(),
            llm_gateway=FakeGateway("Get a 20% discount when you subscribe."),
        ).execute(_request())
    )

    assert result.status == "OFFER_AVAILABLE"
    assert result.offer is not None
    assert result.offer.offer_type == "STYLE_PLUS_TRIAL"
    assert result.message is None


def test_model_failure_preserves_positive_offer_decision():
    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(),
            llm_gateway=FakeGateway(error=True),
        ).execute(_request())
    )

    assert result.status == "OFFER_AVAILABLE"
    assert result.should_offer is True
    assert result.message is None


def test_policy_failure_fails_closed_without_model_call():
    gateway = FakeGateway()

    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(policy_error=True),
            llm_gateway=gateway,
        ).execute(_request())
    )

    assert result.status == "FAILED"
    assert result.should_offer is False
    assert result.suppression_reasons == ["SERVICE_NOT_AVAILABLE"]
    assert gateway.calls == []


def test_missing_engagement_context_fails_closed():
    gateway = FakeGateway()

    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(context_error=True),
            llm_gateway=gateway,
        ).execute(_request())
    )

    assert result.status == "FAILED"
    assert result.suppression_reasons == ["CUSTOMER_CONTEXT_INCOMPLETE"]
    assert gateway.calls == []


def test_event_recording_failure_does_not_rewrite_eligibility():
    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(record_error=True),
            llm_gateway=FakeGateway(),
        ).execute(_request())
    )

    assert result.status == "OFFER_AVAILABLE"
    assert result.should_offer is True
    assert "EVENT_RECORDING_FAILED" in result.eligibility_reasons


def test_invalid_trigger_input_returns_governed_no_offer():
    payload = _request().model_dump(mode="json")
    payload["trigger"]["trigger_type"] = "UNKNOWN_TRIGGER"
    gateway = FakeGateway()

    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(), llm_gateway=gateway
        ).execute(payload)
    )

    assert result.status == "NO_OFFER"
    assert result.suppression_reasons == ["INVALID_TRIGGER"]
    assert gateway.calls == []


def test_missing_runtime_tool_fails_closed():
    tools = set(UPSELL_AGENT_REQUIRED_TOOLS) - {"evaluate_upsell"}
    gateway = FakeGateway()

    result = asyncio.run(
        UpsellAgent(
            tool_client=FakeToolClient(tools=tools), llm_gateway=gateway
        ).execute(_request())
    )

    assert result.status == "FAILED"
    assert gateway.calls == []


def test_agent_source_respects_agent_and_model_boundaries():
    source = Path("agents/upsell/agent.py").read_text().casefold()

    assert "sqlalchemy" not in source
    assert "repositories" not in source
    assert "discoveryagent(" not in source
    assert "fitagent(" not in source
    assert "litellm" not in source
    assert "create_agent_server" in source
    assert "llm_gateway.invoke" in source
