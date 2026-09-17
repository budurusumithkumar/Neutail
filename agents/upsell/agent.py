"""Governed MCP-only Service Upsell & Monetisation Agent."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from fastmcp import Client, FastMCP
from langsmith import get_current_run_tree, trace, traceable
from pydantic import ValidationError

from agents.upsell.constants import SERVICE_OFFER_CATALOG
from agents.upsell.offer_selector import OfferSelector
from agents.upsell.opportunity_scorer import OpportunityScorer
from llm_gateway import LLMGateway
from models.dto import BehaviorSummary, LoyaltyProfile, Product
from models.upsell import (
    UpsellDecision,
    UpsellEligibilityResult,
    UpsellEvaluationRequest,
    UpsellEventInput,
    UpsellRequest,
    UpsellResult,
    UpsellTrigger,
)
from tools.permissions import (
    AgentName,
    UPSELL_AGENT_OPTIONAL_TOOLS,
    UPSELL_AGENT_REQUIRED_TOOLS,
)
from tools.registry import create_agent_server


class UpsellAgentError(RuntimeError):
    """Base error raised at the governed agent boundary."""


class UpsellToolDiscoveryError(UpsellAgentError):
    """Raised when the runtime registry lacks a mandatory capability."""


class UpsellToolInvocationError(UpsellAgentError):
    """Raised when the agent attempts to call an undeclared capability."""


class UpsellToolClient(Protocol):
    async def discover_tools(self) -> list[str]: ...

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


def _tool_payload(result: Any) -> Any:
    payload = result.structured_content
    if payload is None:
        payload = result.data
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


class FastMCPUpsellToolClient:
    """Use only the Upsell Agent's runtime-scoped FastMCP server."""

    CALLABLE_TOOLS = UPSELL_AGENT_REQUIRED_TOOLS | UPSELL_AGENT_OPTIONAL_TOOLS

    def __init__(
        self,
        server_factory: Callable[[], FastMCP] = lambda: create_agent_server(
            AgentName.UPSELL
        ),
    ) -> None:
        self._server_factory = server_factory

    async def discover_tools(self) -> list[str]:
        with trace(
            name="upsell_tool_discovery",
            run_type="tool",
            inputs={"agent": AgentName.UPSELL.value},
            tags=["fastmcp", "tool-discovery", "upsell-agent"],
        ) as run:
            async with Client(self._server_factory()) as client:
                names = sorted(tool.name for tool in await client.list_tools())
            run.end(outputs={"tools": names})
            return names

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self.CALLABLE_TOOLS:
            raise UpsellToolInvocationError(
                f"Upsell Agent is not permitted to call '{tool_name}'"
            )
        with trace(
            name=tool_name,
            run_type="tool",
            inputs={"argument_keys": sorted(arguments)},
            tags=["fastmcp", "upsell-agent"],
            metadata={"agent": AgentName.UPSELL.value},
        ) as run:
            async with Client(self._server_factory()) as client:
                result = await client.call_tool(tool_name, arguments)
            payload = _tool_payload(result)
            run.end(outputs={"result_type": type(payload).__name__})
            return payload


def _trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    if isinstance(request, UpsellRequest):
        return {
            "customer_id": request.customer_context.customer_id,
            "session_id": request.session_context.session_id,
            "segment": request.customer_context.segment,
            "trigger_type": request.trigger.trigger_type.value,
            "trigger_strength": request.trigger.strength,
            "selected_sku": request.selected_sku,
        }
    return {"request_type": type(request).__name__}


def _trace_outputs(output: Any) -> dict[str, Any]:
    if isinstance(output, UpsellResult):
        return {
            "status": output.status,
            "should_offer": output.should_offer,
            "selected_offer": (
                output.offer.offer_type.value if output.offer is not None else None
            ),
            "opportunity_score": output.opportunity_score,
            "suppression_reasons": output.suppression_reasons,
        }
    return {"output_type": type(output).__name__}


class UpsellAgent:
    """Evaluate governed service offers and use the model only for wording."""

    def __init__(
        self,
        *,
        tool_client: UpsellToolClient | None = None,
        opportunity_scorer: OpportunityScorer | None = None,
        offer_selector: OfferSelector | None = None,
        llm_gateway: LLMGateway | None = None,
    ) -> None:
        self.tool_client = tool_client or FastMCPUpsellToolClient()
        self.opportunity_scorer = opportunity_scorer or OpportunityScorer()
        self.offer_selector = offer_selector or OfferSelector()
        self.llm_gateway = llm_gateway or LLMGateway()

    @traceable(
        name="upsell_agent",
        run_type="chain",
        tags=["upsell-agent", "governed-monetisation"],
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    async def execute(
        self, request: UpsellRequest | dict[str, Any]
    ) -> UpsellResult:
        if not isinstance(request, UpsellRequest):
            try:
                request = UpsellRequest.model_validate(request)
            except ValidationError as exc:
                invalid_trigger = any(
                    tuple(error.get("loc", ())) == ("trigger", "trigger_type")
                    for error in exc.errors()
                )
                return self._no_offer(
                    "INVALID_TRIGGER"
                    if invalid_trigger
                    else "CUSTOMER_CONTEXT_INCOMPLETE"
                )
        self._attach_trace_metadata(request)

        try:
            discovered_tools = await self._verify_tool_scope()
            loyalty_payload = await self.tool_client.call_tool(
                "get_loyalty_profile",
                {"customer_id": request.customer_context.customer_id},
            )
            loyalty = LoyaltyProfile.model_validate(loyalty_payload)
            engagement_payload = await self.tool_client.call_tool(
                "get_engagement_summary",
                {"customer_id": request.customer_context.customer_id},
            )
            engagement = BehaviorSummary.model_validate(engagement_payload)
        except Exception as exc:
            self._record_failure("CONTEXT_TOOLS_UNAVAILABLE", exc)
            return UpsellResult(
                status="FAILED",
                should_offer=False,
                suppression_reasons=["CUSTOMER_CONTEXT_INCOMPLETE"],
                trigger=request.trigger,
            )

        evaluation_request = UpsellEvaluationRequest(
            customer_id=request.customer_context.customer_id,
            segment=request.customer_context.segment,
            trigger_type=request.trigger.trigger_type.value,
            trigger_strength=request.trigger.strength,
            selected_sku=request.selected_sku,
        )
        try:
            eligibility_payload = await self.tool_client.call_tool(
                "evaluate_upsell",
                {"request": evaluation_request.model_dump(mode="json")},
            )
            eligibility = UpsellEligibilityResult.model_validate(
                eligibility_payload
            )
        except Exception as exc:
            self._record_failure("UPSELL_POLICY_UNAVAILABLE", exc)
            return UpsellResult(
                status="FAILED",
                should_offer=False,
                suppression_reasons=["SERVICE_NOT_AVAILABLE"],
                trigger=request.trigger,
            )

        if not eligibility.eligible:
            return UpsellResult(
                status="NO_OFFER",
                should_offer=False,
                opportunity_score=0.0,
                eligibility_reasons=eligibility.eligibility_reasons,
                suppression_reasons=eligibility.suppression_reasons,
                trigger=request.trigger,
            )

        opportunity = self.opportunity_scorer.calculate(
            customer_context=request.customer_context,
            loyalty=loyalty,
            engagement=engagement,
            trigger=request.trigger,
            eligibility=eligibility,
        )
        selected_offer = self.offer_selector.select(
            eligible_offers=eligibility.eligible_offers,
            opportunity=opportunity,
            trigger=request.trigger,
            customer_context=request.customer_context,
        )
        if selected_offer is None:
            return self._no_offer(
                "NO_APPROPRIATE_OFFER", trigger=request.trigger
            )

        decision = UpsellDecision(
            should_offer=True,
            selected_offer=selected_offer,
            opportunity_score=opportunity.score,
            opportunity_band=opportunity.band,
            eligibility_reasons=opportunity.reason_codes,
            suppression_reasons=eligibility.suppression_reasons,
            requires_customer_consent=True,
        )
        product_context = await self._get_safe_product_context(
            request, discovered_tools
        )
        message = await self._generate_message(
            decision=decision,
            request=request,
            product_context=product_context,
        )

        event_error = False
        try:
            event = UpsellEventInput(
                customer_id=request.customer_context.customer_id,
                session_id=request.session_context.session_id,
                offer_type=selected_offer,
                event_type="OFFER_SHOWN",
                trigger_type=request.trigger.trigger_type.value,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            await self.tool_client.call_tool(
                "record_upsell_event",
                {"event": event.model_dump(mode="json")},
            )
        except Exception as exc:
            event_error = True
            self._record_failure("UPSELL_EVENT_RECORDING_FAILED", exc)

        reasons = list(decision.eligibility_reasons)
        if event_error:
            reasons.append("EVENT_RECORDING_FAILED")
        return UpsellResult(
            status="OFFER_AVAILABLE",
            should_offer=True,
            offer=SERVICE_OFFER_CATALOG[selected_offer].model_copy(deep=True),
            opportunity_score=decision.opportunity_score,
            opportunity_band=decision.opportunity_band,
            eligibility_reasons=reasons,
            suppression_reasons=decision.suppression_reasons,
            message=message,
            requires_customer_consent=True,
            trigger=request.trigger,
            llm_invoked=True,
        )

    async def _verify_tool_scope(self) -> set[str]:
        names = set(await self.tool_client.discover_tools())
        missing = UPSELL_AGENT_REQUIRED_TOOLS.difference(names)
        if missing:
            raise UpsellToolDiscoveryError(
                f"Upsell Agent tool scope is missing {sorted(missing)}"
            )
        return names

    async def _get_safe_product_context(
        self, request: UpsellRequest, discovered_tools: set[str]
    ) -> Optional[dict[str, Any]]:
        if not request.selected_sku or "get_product_details" not in discovered_tools:
            return None
        try:
            payload = await self.tool_client.call_tool(
                "get_product_details", {"sku": request.selected_sku}
            )
            product = Product.model_validate(payload)
        except Exception:
            return None
        return {
            "sku": product.sku,
            "product_name": product.product_name,
            "brand": product.brand,
            "category": product.category,
            "brand_tier": product.brand_tier,
        }

    async def _generate_message(
        self,
        *,
        decision: UpsellDecision,
        request: UpsellRequest,
        product_context: Optional[dict[str, Any]],
    ) -> Optional[str]:
        if not decision.should_offer or decision.selected_offer is None:
            return None
        offer = SERVICE_OFFER_CATALOG[decision.selected_offer]
        with trace(
            name="generate_upsell_message",
            run_type="chain",
            inputs={
                "selected_offer": decision.selected_offer.value,
                "reason_codes": decision.eligibility_reasons,
            },
            tags=["upsell-agent", "llm-wording-only"],
        ) as run:
            try:
                response = await self.llm_gateway.invoke(
                    use_case="upsell_message",
                    prompt_version="v1",
                    agent_name="UpsellAgent",
                    trace_id=(
                        request.session_context.attributes.get("trace_id")
                        or request.session_context.session_id
                    ),
                    session_id=request.session_context.session_id,
                    context={
                        "customer_context": {
                            "segment": request.customer_context.segment,
                            "loyalty_tier": request.customer_context.loyalty_tier,
                            "premium_affinity": (
                                request.customer_context.premium_affinity
                            ),
                        },
                        "shopping_intent": {
                            "trigger_type": request.trigger.trigger_type.value,
                            "product_context": product_context,
                        },
                        "eligible_offer": offer.model_dump(mode="json"),
                        "reason_codes": decision.eligibility_reasons,
                    },
                )
            except Exception as exc:
                run.end(outputs={"status": "UNAVAILABLE"}, error=type(exc).__name__)
                return None
            message = self._safe_message(response)
            run.end(outputs={"message_available": bool(message)})
            return message or None

    @staticmethod
    def _safe_message(response: Any) -> Optional[str]:
        if not isinstance(response, str) or not response.strip():
            return None
        message = response.strip()
        normalized = message.casefold()
        prohibited = (
            "enroll",
            "subscribe",
            "subscription",
            "discount",
            "% off",
        )
        return None if any(term in normalized for term in prohibited) else message

    @staticmethod
    def _no_offer(
        reason: str, *, trigger: Optional[UpsellTrigger] = None
    ) -> UpsellResult:
        return UpsellResult(
            status="NO_OFFER",
            should_offer=False,
            opportunity_score=0.0,
            suppression_reasons=[reason],
            trigger=trigger,
            llm_invoked=False,
        )

    @staticmethod
    def _record_failure(code: str, exc: Exception) -> None:
        run = get_current_run_tree()
        if run is not None:
            run.metadata.setdefault("upsell_errors", []).append(
                {"code": code, "error_type": type(exc).__name__}
            )

    @staticmethod
    def _attach_trace_metadata(request: UpsellRequest) -> None:
        run = get_current_run_tree()
        if run is not None:
            run.metadata.update(
                {
                    "customer_id": request.customer_context.customer_id,
                    "session_id": request.session_context.session_id,
                    "segment": request.customer_context.segment,
                    "trigger_type": request.trigger.trigger_type.value,
                    "trigger_strength": request.trigger.strength,
                }
            )


__all__ = [
    "FastMCPUpsellToolClient",
    "UpsellAgent",
    "UpsellAgentError",
    "UpsellToolDiscoveryError",
    "UpsellToolInvocationError",
]
