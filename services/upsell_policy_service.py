"""Deterministic service-offer eligibility and suppression policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models.dto import (
    CustomerContext,
    CustomerPreferences,
    ServiceCandidate,
    SuppressionResult,
    UpsellDecision,
    UpsellEvaluationInput,
)
from models.entities import Customer
from models.entities import Loyalty
from models.entities import Order as OrderEntity
from models.entities import OrderItem as OrderItemEntity
from models.entities import Return as ReturnEntity
from models.entities import ServiceEngagement as ServiceEngagementEntity
from services._date_utils import sqlite_datetime, utc_now


class UpsellCustomerNotFoundError(LookupError):
    """Raised when policy evaluation is requested for an unknown customer."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Customer '{customer_id}' was not found")


@dataclass
class UpsellPolicyState:
    """Process-local state shared across short-lived service instances."""

    offered_sessions: set[tuple[str, str]] = field(default_factory=set)
    decision_audit: list[UpsellDecision] = field(default_factory=list)
    lock: RLock = field(default_factory=RLock, repr=False)


class UpsellPolicyService:
    """Apply deterministic rules before an agent may present a service offer."""

    MIN_ENGAGEMENT_SCORE = 0.35
    MIN_INTERACTION_COUNT = 3
    MIN_PROPENSITY_SCORE = 0.45
    DECLINE_COOLDOWN_DAYS = 30
    FREQUENCY_WINDOW_DAYS = 7
    MAX_OFFERS_PER_WINDOW = 2
    SUBSCRIPTION_SERVICE_CODES = frozenset({"STYLE_PLUS_TRIAL"})

    def __init__(
        self,
        session: Session,
        *,
        clock: Optional[Callable[[], datetime]] = None,
        state: Optional[UpsellPolicyState] = None,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now
        self._state = state or UpsellPolicyState()

    def evaluate(
        self,
        customer_id: str,
        session_context: UpsellEvaluationInput,
    ) -> UpsellDecision:
        """Evaluate consent, suppression, engagement, and service eligibility."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        if not isinstance(session_context, UpsellEvaluationInput):
            session_context = UpsellEvaluationInput.model_validate(session_context)
        if session_context.customer_id != normalized_customer_id:
            raise ValueError("customer_id must match session_context.customer_id")

        customer = self._session.get(Customer, normalized_customer_id)
        if customer is None:
            raise UpsellCustomerNotFoundError(normalized_customer_id)

        if not bool(customer.marketing_consent):
            return UpsellDecision(
                eligible=False,
                action="NO_OFFER",
                reason_codes=["MARKETING_CONSENT_REQUIRED"],
                suppression_reason="CUSTOMER_HAS_NOT_CONSENTED",
            )

        suppression = self.is_suppressed(
            normalized_customer_id, session_context.session_id
        )
        if suppression.suppressed:
            return UpsellDecision(
                eligible=False,
                action="NO_OFFER",
                reason_codes=["OFFER_SUPPRESSED"],
                suppression_reason=suppression.reason,
            )

        engagement_reasons: list[str] = []
        if session_context.engagement_score < self.MIN_ENGAGEMENT_SCORE:
            engagement_reasons.append("INSUFFICIENT_ENGAGEMENT_SCORE")
        if session_context.interaction_count < self.MIN_INTERACTION_COUNT:
            engagement_reasons.append("INSUFFICIENT_INTERACTIONS")
        if engagement_reasons:
            return UpsellDecision(
                eligible=False,
                action="NO_OFFER",
                reason_codes=engagement_reasons,
            )

        customer_context = self._build_customer_context(customer)
        candidates = self.get_candidate_services(customer_context)
        previously_used_services = self._previously_used_subscription_services(
            normalized_customer_id
        )
        candidates = [
            candidate.model_copy(
                update={
                    "eligible": False,
                    "reason_codes": [
                        *candidate.reason_codes,
                        "SERVICE_ALREADY_ACCEPTED_OR_COMPLETED",
                    ],
                }
            )
            if candidate.service_code in previously_used_services
            else candidate
            for candidate in candidates
        ]

        candidate = self._select_candidate(candidates, session_context)
        if candidate is None:
            return UpsellDecision(
                eligible=False,
                action="NO_OFFER",
                reason_codes=["NO_ELIGIBLE_SERVICE_CANDIDATE"],
            )

        propensity_score = self._calculate_propensity(
            session_context, customer_context
        )
        if propensity_score < self.MIN_PROPENSITY_SCORE:
            return UpsellDecision(
                eligible=False,
                action="NO_OFFER",
                recommended_service=candidate,
                propensity_score=propensity_score,
                reason_codes=["PROPENSITY_BELOW_THRESHOLD"],
            )

        decision = UpsellDecision(
            eligible=True,
            action="OFFER",
            recommended_service=candidate,
            propensity_score=propensity_score,
            reason_codes=["POLICY_RULES_PASSED", *candidate.reason_codes],
        )
        session_key = (normalized_customer_id, session_context.session_id)
        with self._state.lock:
            if session_key in self._state.offered_sessions:
                return UpsellDecision(
                    eligible=False,
                    action="NO_OFFER",
                    reason_codes=["OFFER_SUPPRESSED"],
                    suppression_reason="SESSION_OFFER_LIMIT_REACHED",
                )
            self._state.offered_sessions.add(session_key)
        return decision

    def is_suppressed(
        self, customer_id: str, session_id: str
    ) -> SuppressionResult:
        """Check session, decline-cooldown, and frequency suppression rules."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        normalized_session_id = self._normalize_identifier(session_id, "session_id")
        with self._state.lock:
            if (
                normalized_customer_id,
                normalized_session_id,
            ) in self._state.offered_sessions:
                return SuppressionResult(
                    suppressed=True,
                    reason="SESSION_OFFER_LIMIT_REACHED",
                )

        now = self._clock()
        decline_cutoff = now - timedelta(days=self.DECLINE_COOLDOWN_DAYS)
        recent_suppressed_statement = (
            select(ServiceEngagementEntity.event_datetime)
            .where(
                ServiceEngagementEntity.customer_id == normalized_customer_id,
                func.datetime(ServiceEngagementEntity.event_datetime)
                >= sqlite_datetime(decline_cutoff),
                func.datetime(ServiceEngagementEntity.event_datetime)
                <= sqlite_datetime(now),
                or_(
                    ServiceEngagementEntity.offer_suppressed.is_(True),
                    ServiceEngagementEntity.outcome == "DECLINED",
                ),
            )
            .order_by(ServiceEngagementEntity.event_datetime.desc())
            .limit(1)
        )
        last_suppressed_at = self._session.scalar(recent_suppressed_statement)
        if last_suppressed_at is not None:
            return SuppressionResult(
                suppressed=True,
                reason="RECENT_DECLINED_OR_SUPPRESSED_OFFER",
                expires_at=last_suppressed_at
                + timedelta(days=self.DECLINE_COOLDOWN_DAYS),
            )

        frequency_cutoff = now - timedelta(days=self.FREQUENCY_WINDOW_DAYS)
        frequency_statement = select(
            func.count(ServiceEngagementEntity.engagement_id),
            func.min(ServiceEngagementEntity.event_datetime),
        ).where(
            ServiceEngagementEntity.customer_id == normalized_customer_id,
            func.datetime(ServiceEngagementEntity.event_datetime)
            >= sqlite_datetime(frequency_cutoff),
            func.datetime(ServiceEngagementEntity.event_datetime)
            <= sqlite_datetime(now),
        )
        offer_count, first_offer_at = self._session.execute(
            frequency_statement
        ).one()
        if int(offer_count or 0) >= self.MAX_OFFERS_PER_WINDOW:
            return SuppressionResult(
                suppressed=True,
                reason="OFFER_FREQUENCY_LIMIT_REACHED",
                expires_at=first_offer_at
                + timedelta(days=self.FREQUENCY_WINDOW_DAYS),
            )

        return SuppressionResult(suppressed=False)

    def get_candidate_services(
        self, customer_context: CustomerContext
    ) -> list[ServiceCandidate]:
        """Return deterministic candidate eligibility from customer context."""

        if not isinstance(customer_context, CustomerContext):
            customer_context = CustomerContext.model_validate(customer_context)

        tier = (customer_context.loyalty_tier or "").casefold()
        segment = customer_context.segment.casefold()
        affluence = (customer_context.affluence_band or "").casefold()
        loyalty_status = (customer_context.loyalty_status or "").casefold()
        premium_affinity = customer_context.premium_affinity or 0.0
        return_rate = customer_context.return_rate or 0.0

        return [
            self._candidate(
                "FIT_ASSIST",
                "Fit Assist",
                return_rate >= 0.20,
                "ELEVATED_RETURN_RATE",
                "RETURN_RATE_BELOW_FIT_THRESHOLD",
            ),
            self._candidate(
                "CONCIERGE",
                "Concierge",
                affluence == "affluent" and tier in {"gold", "platinum"},
                "AFFLUENT_HIGH_TIER_CUSTOMER",
                "CONCIERGE_TIER_REQUIREMENTS_NOT_MET",
            ),
            self._candidate(
                "PERSONAL_STYLING",
                "Personal Styling",
                premium_affinity >= 0.65
                or segment in {"prestige champion", "aspiring loyalist"},
                "PREMIUM_STYLE_AFFINITY",
                "PREMIUM_AFFINITY_BELOW_THRESHOLD",
            ),
            self._candidate(
                "STYLE_PLUS_TRIAL",
                "Style+ Trial",
                loyalty_status == "new" or tier in {"bronze", "silver"},
                "TRIAL_ELIGIBLE_LOYALTY_STATE",
                "TRIAL_NOT_RELEVANT_TO_LOYALTY_STATE",
            ),
            self._candidate(
                "OUTFIT_BUILDER",
                "Outfit Builder",
                bool(customer_context.preferences.preferred_occasions),
                "KNOWN_OCCASION_PREFERENCES",
                "NO_OCCASION_PREFERENCES",
            ),
            ServiceCandidate(
                service_code="AI_STYLING_ADVISORY",
                service_name="AI Styling Advisory",
                eligible=True,
                reason_codes=["BASELINE_DIGITAL_SERVICE"],
            ),
        ]

    def record_decision(self, decision: UpsellDecision) -> None:
        """Record an in-process audit copy of a deterministic policy decision."""

        if not isinstance(decision, UpsellDecision):
            decision = UpsellDecision.model_validate(decision)
        with self._state.lock:
            self._state.decision_audit.append(decision.model_copy(deep=True))

    def _build_customer_context(self, customer: Customer) -> CustomerContext:
        loyalty = self._session.get(Loyalty, customer.customer_id)
        purchased_item_ids = (
            select(OrderItemEntity.order_item_id)
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .where(OrderEntity.customer_id == customer.customer_id)
        )
        purchased_items = int(
            self._session.scalar(
                select(func.count()).select_from(purchased_item_ids.subquery())
            )
            or 0
        )
        returned_items = int(
            self._session.scalar(
                select(func.count(func.distinct(ReturnEntity.order_item_id))).where(
                    ReturnEntity.customer_id == customer.customer_id,
                    ReturnEntity.order_item_id.in_(purchased_item_ids),
                )
            )
            or 0
        )
        return_rate = (
            round(returned_items / purchased_items, 3) if purchased_items else 0.0
        )

        return CustomerContext(
            customer_id=customer.customer_id,
            segment=customer.segment or "UNKNOWN",
            affluence_band=customer.affluence_band,
            loyalty_status=customer.loyalty_status,
            clv=customer.estimated_clv_gbp,
            price_sensitivity=customer.price_sensitivity,
            premium_affinity=customer.premium_affinity,
            preferences=CustomerPreferences.model_validate(customer),
            loyalty_tier=loyalty.tier if loyalty else None,
            return_rate=return_rate,
        )

    def _previously_used_subscription_services(self, customer_id: str) -> set[str]:
        statement = select(ServiceEngagementEntity.service_type).where(
            ServiceEngagementEntity.customer_id == customer_id,
            ServiceEngagementEntity.service_type.in_(self.SUBSCRIPTION_SERVICE_CODES),
            ServiceEngagementEntity.outcome.in_({"ACCEPTED", "COMPLETED"}),
        )
        return set(self._session.scalars(statement).all())

    @staticmethod
    def _select_candidate(
        candidates: list[ServiceCandidate],
        context: UpsellEvaluationInput,
    ) -> Optional[ServiceCandidate]:
        eligible = {
            candidate.service_code: candidate
            for candidate in candidates
            if candidate.eligible
        }
        intent = context.current_intent.casefold()
        if context.fit_risk is not None and context.fit_risk >= 0.60:
            if "FIT_ASSIST" in eligible:
                return eligible["FIT_ASSIST"]
        if any(
            keyword in intent
            for keyword in ("outfit", "wedding", "occasion", "party", "work")
        ):
            if "OUTFIT_BUILDER" in eligible:
                return eligible["OUTFIT_BUILDER"]

        priority = (
            "CONCIERGE",
            "PERSONAL_STYLING",
            "STYLE_PLUS_TRIAL",
            "OUTFIT_BUILDER",
            "FIT_ASSIST",
            "AI_STYLING_ADVISORY",
        )
        return next((eligible[code] for code in priority if code in eligible), None)

    @staticmethod
    def _calculate_propensity(
        context: UpsellEvaluationInput,
        customer_context: CustomerContext,
    ) -> float:
        interaction_component = min(context.interaction_count / 10, 1.0)
        premium_component = customer_context.premium_affinity or 0.0
        fit_component = context.fit_risk or 0.0
        score = (
            context.engagement_score * 0.55
            + interaction_component * 0.20
            + premium_component * 0.15
            + fit_component * 0.10
        )
        return round(min(max(score, 0.0), 1.0), 3)

    @staticmethod
    def _candidate(
        service_code: str,
        service_name: str,
        eligible: bool,
        eligible_reason: str,
        ineligible_reason: str,
    ) -> ServiceCandidate:
        return ServiceCandidate(
            service_code=service_code,
            service_name=service_name,
            eligible=eligible,
            reason_codes=[eligible_reason if eligible else ineligible_reason],
        )

    @staticmethod
    def _normalize_identifier(value: Any, field_name: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError(f"{field_name} must be a non-empty string")


__all__ = [
    "UpsellCustomerNotFoundError",
    "UpsellPolicyService",
    "UpsellPolicyState",
]
