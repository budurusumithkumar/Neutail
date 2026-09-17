"""FastMCP adapters for engagement and deterministic upsell policy."""

from __future__ import annotations

from typing import Optional
from uuid import uuid4

from models.dto import (
    BehaviorEvent,
    BehaviorSummary,
    CustomerContext,
    ServiceCandidate,
    ServiceEngagement,
    SuppressionResult,
    UpsellDecision,
    UpsellEvaluationInput,
)
from models.upsell import (
    UpsellEligibilityResult,
    UpsellEvaluationRequest,
    UpsellEventInput,
)
from services.engagement_service import EngagementService
from services.upsell_policy_service import UpsellPolicyService
from tools.contracts import ToolAcknowledgement, tool_contract
from tools.runtime import get_runtime


@tool_contract(
    name="evaluate_upsell",
    title="Evaluate Governed Service Offer",
    description=(
        "Apply deterministic customer context, consent, subscription, decline, "
        "frequency, trigger, and service-eligibility policy."
    ),
    capability="upsell.policy.governed-evaluation",
)
def evaluate_upsell(
    request: UpsellEvaluationRequest,
) -> UpsellEligibilityResult:
    runtime = get_runtime()
    with runtime.session() as session:
        return UpsellPolicyService(
            session, state=runtime.upsell_policy_state
        ).evaluate_upsell(request)


@tool_contract(
    name="record_upsell_event",
    title="Record Upsell Event",
    description=(
        "Persist an explicit offer lifecycle event; generating an offer records "
        "OFFER_SHOWN only and never implies acceptance."
    ),
    capability="upsell.engagement.governed-record",
    read_only=False,
    idempotent=False,
)
def record_upsell_event(event: UpsellEventInput) -> ToolAcknowledgement:
    if not isinstance(event, UpsellEventInput):
        event = UpsellEventInput.model_validate(event)
    service_event = ServiceEngagement(
        engagement_id=f"UPSELL-{uuid4().hex}",
        customer_id=event.customer_id,
        event_datetime=event.timestamp,
        service_type=event.offer_type.value,
        outcome=event.event_type,
        channel="UPSELL_AGENT",
        offer_suppressed=False,
    )
    with get_runtime().session(write=True) as session:
        EngagementService(session).record_service_event(service_event)
    return ToolAcknowledgement(success=True, message=f"{event.event_type} recorded")


@tool_contract(
    name="upsell_get_recent_behavior",
    title="Get Recent Behavior",
    description="Return recent clickstream, search, and product-view evidence for a customer.",
    capability="upsell.engagement.behavior",
)
def upsell_get_recent_behavior(
    customer_id: str, days: int = 30
) -> list[BehaviorEvent]:
    with get_runtime().session() as session:
        return EngagementService(session).get_recent_behavior(customer_id, days)


@tool_contract(
    name="upsell_get_behavior_summary",
    title="Get Behavior Summary",
    description="Calculate deterministic engagement signals for a session or recent customer behavior.",
    capability="upsell.engagement.summary",
)
def upsell_get_behavior_summary(
    customer_id: str, session_id: Optional[str] = None
) -> BehaviorSummary:
    with get_runtime().session() as session:
        return EngagementService(session).get_behavior_summary(customer_id, session_id)


@tool_contract(
    name="upsell_get_service_engagement",
    title="Get Service Engagement",
    description="Return previous styling and paid-service interactions for suppression evidence.",
    capability="upsell.engagement.services",
)
def upsell_get_service_engagement(
    customer_id: str, days: int = 90
) -> list[ServiceEngagement]:
    with get_runtime().session() as session:
        return EngagementService(session).get_service_engagement(customer_id, days)


@tool_contract(
    name="upsell_record_service_event",
    title="Record Service Event",
    description="Persist an explicit service offer or interaction in the caller's demo database.",
    capability="upsell.engagement.record",
    read_only=False,
    idempotent=False,
)
def upsell_record_service_event(event: ServiceEngagement) -> ServiceEngagement:
    with get_runtime().session(write=True) as session:
        return EngagementService(session).record_service_event(event)


@tool_contract(
    name="upsell_evaluate",
    title="Evaluate Upsell Policy",
    description="Apply deterministic consent, suppression, frequency, engagement, and eligibility rules.",
    capability="upsell.policy.evaluate",
)
def upsell_evaluate(
    customer_id: str, session_context: UpsellEvaluationInput
) -> UpsellDecision:
    runtime = get_runtime()
    with runtime.session() as session:
        return UpsellPolicyService(
            session, state=runtime.upsell_policy_state
        ).evaluate(customer_id, session_context)


@tool_contract(
    name="upsell_is_suppressed",
    title="Check Upsell Suppression",
    description="Check session, recent-decline, and offer-frequency suppression rules.",
    capability="upsell.policy.suppression",
)
def upsell_is_suppressed(customer_id: str, session_id: str) -> SuppressionResult:
    runtime = get_runtime()
    with runtime.session() as session:
        return UpsellPolicyService(
            session, state=runtime.upsell_policy_state
        ).is_suppressed(customer_id, session_id)


@tool_contract(
    name="upsell_get_candidate_services",
    title="Get Candidate Services",
    description="Return deterministic service eligibility from shared customer context.",
    capability="upsell.policy.candidates",
)
def upsell_get_candidate_services(
    customer_context: CustomerContext,
) -> list[ServiceCandidate]:
    runtime = get_runtime()
    with runtime.session() as session:
        return UpsellPolicyService(
            session, state=runtime.upsell_policy_state
        ).get_candidate_services(customer_context)


@tool_contract(
    name="upsell_record_decision",
    title="Record Upsell Decision",
    description="Record a process-local audit copy of an upsell policy decision.",
    capability="upsell.policy.audit",
    read_only=False,
    idempotent=False,
)
def upsell_record_decision(decision: UpsellDecision) -> ToolAcknowledgement:
    runtime = get_runtime()
    with runtime.session() as session:
        UpsellPolicyService(
            session, state=runtime.upsell_policy_state
        ).record_decision(decision)
    return ToolAcknowledgement(success=True, message="Upsell decision recorded")


UPSELL_TOOLS = (
    evaluate_upsell,
    record_upsell_event,
    upsell_get_recent_behavior,
    upsell_get_behavior_summary,
    upsell_get_service_engagement,
    upsell_record_service_event,
    upsell_evaluate,
    upsell_is_suppressed,
    upsell_get_candidate_services,
    upsell_record_decision,
)


__all__ = ["UPSELL_TOOLS"]
