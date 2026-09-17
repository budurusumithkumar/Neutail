"""Deterministic service-offer eligibility and suppression policy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Any, Optional

from langsmith import trace
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
from models.upsell import (
    ServiceOfferType,
    UpsellEligibilityResult,
    UpsellEvaluationRequest,
    UpsellTriggerType,
)
from services._date_utils import sqlite_datetime, utc_now


class UpsellCustomerNotFoundError(LookupError):
    """Raised when policy evaluation is requested for an unknown customer."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Customer '{customer_id}' was not found")


@dataclass(frozen=True)
class UpsellPolicyConfig:
    """Central thresholds for governed service-offer eligibility."""

    max_service_offers_30d: int = 3
    service_offer_window_days: int = 30
    explicit_suppression_days: int = 30
    style_plus_decline_cooldown_days: int = 30
    high_engagement_threshold: float = 0.70
    chronic_fit_risk_threshold: float = 0.50
    cart_abandonment_threshold: float = 0.70
    loyalty_threshold_tiers: frozenset[str] = frozenset({"gold", "platinum"})


DEFAULT_UPSELL_POLICY = UpsellPolicyConfig()


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
        config: UpsellPolicyConfig = DEFAULT_UPSELL_POLICY,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now
        self._state = state or UpsellPolicyState()
        self._config = config

    def evaluate_eligibility(
        self,
        request: UpsellEvaluationRequest,
    ) -> UpsellEligibilityResult:
        """Apply ordered, deterministic policy before scoring or wording."""

        if not isinstance(request, UpsellEvaluationRequest):
            request = UpsellEvaluationRequest.model_validate(request)
        customer_id = self._normalize_identifier(request.customer_id, "customer_id")
        customer = self._session.get(Customer, customer_id)
        if customer is None:
            raise UpsellCustomerNotFoundError(customer_id)

        normalized_segment = self._normalized_segment(request.segment)
        stored_segment = self._normalized_segment(customer.segment or "")
        supported_segments = {
            "prestige champion",
            "value defender",
            "aspiring loyalist",
            "price explorer",
        }
        if (
            normalized_segment not in supported_segments
            or normalized_segment != stored_segment
            or customer.premium_affinity is None
            or customer.price_sensitivity is None
            or customer.marketing_consent is None
        ):
            return self._no_offer("CUSTOMER_CONTEXT_INCOMPLETE")

        now = self._clock()
        subscription_suppressed = self._has_active_style_plus(customer_id)
        inherited_suppressions = (
            ["ALREADY_STYLE_PLUS_MEMBER"] if subscription_suppressed else []
        )

        if not bool(customer.marketing_consent):
            return self._no_offer("CUSTOMER_CONSENT_REQUIRED")

        if self._has_explicit_suppression(customer_id, now):
            return self._no_offer("SERVICE_NOT_AVAILABLE")

        last_decline_at = self._last_style_plus_decline(customer_id, now)
        if last_decline_at is not None:
            return self._no_offer(
                "RECENT_STYLE_PLUS_DECLINE",
                cooldown_until=last_decline_at
                + timedelta(days=self._config.style_plus_decline_cooldown_days),
            )

        frequency_reset = self._frequency_reset_at(customer_id, now)
        if frequency_reset is not None:
            return self._no_offer(
                "SERVICE_FREQUENCY_LIMIT", cooldown_until=frequency_reset
            )

        try:
            trigger_type = UpsellTriggerType(request.trigger_type)
        except ValueError:
            return self._no_offer("INVALID_TRIGGER")

        loyalty = self._session.get(Loyalty, customer_id)
        trigger_reason = self._validate_trigger(
            trigger_type=trigger_type,
            strength=request.trigger_strength,
            loyalty_tier=loyalty.tier if loyalty else None,
        )
        if trigger_reason is not None:
            return self._no_offer(trigger_reason)

        eligible_offers = self._eligible_offers(
            trigger_type=trigger_type,
            segment=normalized_segment,
        )
        if subscription_suppressed:
            eligible_offers = [
                offer
                for offer in eligible_offers
                if offer
                not in {
                    ServiceOfferType.STYLE_PLUS_TRIAL,
                    ServiceOfferType.STYLE_PLUS,
                }
            ]
        if not eligible_offers:
            return UpsellEligibilityResult(
                eligible=False,
                eligible_offers=[],
                suppression_reasons=[
                    *inherited_suppressions,
                    "SERVICE_NOT_AVAILABLE",
                ],
                eligibility_reasons=[],
            )

        reasons = [trigger_type.value]
        if normalized_segment == "prestige champion":
            reasons.append("PRESTIGE_CHAMPION")
        elif normalized_segment == "aspiring loyalist":
            reasons.append("ASPIRING_LOYALIST")
        if ServiceOfferType.STYLE_PLUS_TRIAL in eligible_offers:
            reasons.append("STYLE_PLUS_ELIGIBLE")
        return UpsellEligibilityResult(
            eligible=True,
            eligible_offers=eligible_offers,
            suppression_reasons=inherited_suppressions,
            eligibility_reasons=reasons,
        )

    def evaluate_upsell(
        self, request: UpsellEvaluationRequest
    ) -> UpsellEligibilityResult:
        """Named policy entry point matching the public FastMCP capability."""

        return self.evaluate_eligibility(request)

    def _has_active_style_plus(self, customer_id: str) -> bool:
        with trace(
            name="check_subscription",
            run_type="chain",
            inputs={"customer_id": customer_id},
            tags=["upsell-policy", "deterministic"],
        ) as run:
            statement = select(ServiceEngagementEntity.engagement_id).where(
                ServiceEngagementEntity.customer_id == customer_id,
                ServiceEngagementEntity.service_type
                == ServiceOfferType.STYLE_PLUS.value,
                ServiceEngagementEntity.outcome.in_(
                    {"ACCEPTED", "COMPLETED", "OFFER_ACCEPTED"}
                ),
            ).limit(1)
            active = self._session.scalar(statement) is not None
            run.end(outputs={"active": active})
            return active

    def _has_explicit_suppression(
        self, customer_id: str, now: datetime
    ) -> bool:
        cutoff = now - timedelta(days=self._config.explicit_suppression_days)
        statement = select(ServiceEngagementEntity.engagement_id).where(
            ServiceEngagementEntity.customer_id == customer_id,
            ServiceEngagementEntity.offer_suppressed.is_(True),
            func.datetime(ServiceEngagementEntity.event_datetime)
            >= sqlite_datetime(cutoff),
            func.datetime(ServiceEngagementEntity.event_datetime)
            <= sqlite_datetime(now),
        ).limit(1)
        return self._session.scalar(statement) is not None

    def _last_style_plus_decline(
        self, customer_id: str, now: datetime
    ) -> Optional[datetime]:
        with trace(
            name="check_recent_decline",
            run_type="chain",
            inputs={"customer_id": customer_id},
            tags=["upsell-policy", "deterministic"],
        ) as run:
            cutoff = now - timedelta(
                days=self._config.style_plus_decline_cooldown_days
            )
            statement = (
                select(ServiceEngagementEntity.event_datetime)
                .where(
                    ServiceEngagementEntity.customer_id == customer_id,
                    ServiceEngagementEntity.service_type.in_(
                        {
                            ServiceOfferType.STYLE_PLUS.value,
                            ServiceOfferType.STYLE_PLUS_TRIAL.value,
                        }
                    ),
                    ServiceEngagementEntity.outcome.in_(
                        {"DECLINED", "OFFER_DECLINED"}
                    ),
                    func.datetime(ServiceEngagementEntity.event_datetime)
                    >= sqlite_datetime(cutoff),
                    func.datetime(ServiceEngagementEntity.event_datetime)
                    <= sqlite_datetime(now),
                )
                .order_by(ServiceEngagementEntity.event_datetime.desc())
                .limit(1)
            )
            declined_at = self._session.scalar(statement)
            run.end(outputs={"recent_decline": declined_at is not None})
            return declined_at

    def _frequency_reset_at(
        self, customer_id: str, now: datetime
    ) -> Optional[datetime]:
        with trace(
            name="check_frequency_limit",
            run_type="chain",
            inputs={"customer_id": customer_id},
            tags=["upsell-policy", "deterministic"],
        ) as run:
            cutoff = now - timedelta(days=self._config.service_offer_window_days)
            statement = select(
                func.count(ServiceEngagementEntity.engagement_id),
                func.min(ServiceEngagementEntity.event_datetime),
            ).where(
                ServiceEngagementEntity.customer_id == customer_id,
                ServiceEngagementEntity.outcome.in_(
                    {
                        "VIEWED",
                        "ACCEPTED",
                        "COMPLETED",
                        "DECLINED",
                        "DISMISSED",
                        "OFFER_SHOWN",
                        "OFFER_ACCEPTED",
                        "OFFER_DECLINED",
                        "OFFER_DISMISSED",
                    }
                ),
                func.datetime(ServiceEngagementEntity.event_datetime)
                >= sqlite_datetime(cutoff),
                func.datetime(ServiceEngagementEntity.event_datetime)
                <= sqlite_datetime(now),
            )
            count, first_at = self._session.execute(statement).one()
            limited = int(count or 0) >= self._config.max_service_offers_30d
            run.end(outputs={"offer_count": int(count or 0), "limited": limited})
            if limited and first_at is not None:
                return first_at + timedelta(
                    days=self._config.service_offer_window_days
                )
            return None

    def _validate_trigger(
        self,
        *,
        trigger_type: UpsellTriggerType,
        strength: Optional[float],
        loyalty_tier: Optional[str],
    ) -> Optional[str]:
        threshold = {
            UpsellTriggerType.HIGH_PRODUCT_ENGAGEMENT: (
                self._config.high_engagement_threshold
            ),
            UpsellTriggerType.PREMIUM_PRODUCT_INTEREST: (
                self._config.high_engagement_threshold
            ),
            UpsellTriggerType.CART_ABANDONMENT: self._config.cart_abandonment_threshold,
            UpsellTriggerType.CHRONIC_FIT_RISK: self._config.chronic_fit_risk_threshold,
            UpsellTriggerType.STYLING_ENGAGEMENT: (
                self._config.high_engagement_threshold
            ),
        }.get(trigger_type)
        if threshold is not None and (strength is None or strength < threshold):
            return "INSUFFICIENT_ENGAGEMENT"
        if trigger_type is UpsellTriggerType.LOYALTY_THRESHOLD_REACHED:
            if (
                loyalty_tier or ""
            ).casefold() not in self._config.loyalty_threshold_tiers:
                return "SERVICE_NOT_AVAILABLE"
        return None

    @staticmethod
    def _eligible_offers(
        *, trigger_type: UpsellTriggerType, segment: str
    ) -> list[ServiceOfferType]:
        value_segments = {"value defender", "price explorer"}
        premium_segments = {"prestige champion", "aspiring loyalist"}
        if trigger_type is UpsellTriggerType.CHRONIC_FIT_RISK:
            return [ServiceOfferType.STYLING_ADVISORY]
        if trigger_type is UpsellTriggerType.CART_ABANDONMENT:
            return [ServiceOfferType.STYLING_ADVISORY]
        if segment in value_segments:
            return [ServiceOfferType.STYLING_ADVISORY]
        if trigger_type is UpsellTriggerType.STYLING_ENGAGEMENT:
            return [
                ServiceOfferType.STYLING_ADVISORY,
                ServiceOfferType.STYLE_PLUS,
            ]
        if trigger_type is UpsellTriggerType.LOYALTY_THRESHOLD_REACHED:
            return [
                ServiceOfferType.STYLING_ADVISORY,
                ServiceOfferType.STYLE_PLUS_TRIAL,
            ]
        if segment in premium_segments:
            return [
                ServiceOfferType.STYLING_ADVISORY,
                ServiceOfferType.STYLE_PLUS_TRIAL,
            ]
        return []

    @staticmethod
    def _no_offer(
        reason: str, *, cooldown_until: Optional[datetime] = None
    ) -> UpsellEligibilityResult:
        return UpsellEligibilityResult(
            eligible=False,
            eligible_offers=[],
            suppression_reasons=[reason],
            eligibility_reasons=[],
            cooldown_until=cooldown_until,
        )

    @staticmethod
    def _normalized_segment(value: str) -> str:
        return " ".join(value.replace("_", " ").casefold().split())

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
    "DEFAULT_UPSELL_POLICY",
    "UpsellCustomerNotFoundError",
    "UpsellPolicyConfig",
    "UpsellPolicyService",
    "UpsellPolicyState",
]
