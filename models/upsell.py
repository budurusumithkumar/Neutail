"""Governed service-offer contracts shared by policy, tools, and agents."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import Field, model_validator

from models.dto import CustomerContext, DTOModel
from orchestrator.models import SessionContext


class UpsellTriggerType(str, Enum):
    HIGH_PRODUCT_ENGAGEMENT = "HIGH_PRODUCT_ENGAGEMENT"
    PREMIUM_PRODUCT_INTEREST = "PREMIUM_PRODUCT_INTEREST"
    CART_ABANDONMENT = "CART_ABANDONMENT"
    CHRONIC_FIT_RISK = "CHRONIC_FIT_RISK"
    STYLING_ENGAGEMENT = "STYLING_ENGAGEMENT"
    LOYALTY_THRESHOLD_REACHED = "LOYALTY_THRESHOLD_REACHED"


class ServiceOfferType(str, Enum):
    STYLING_ADVISORY = "STYLING_ADVISORY"
    STYLE_PLUS_TRIAL = "STYLE_PLUS_TRIAL"
    STYLE_PLUS = "STYLE_PLUS"


class ServiceOffer(DTOModel):
    offer_type: ServiceOfferType
    title: str
    description: Optional[str] = None
    requires_explicit_consent: bool = True
    priority: int = 0


class UpsellTrigger(DTOModel):
    trigger_type: UpsellTriggerType
    source_agent: str = Field(min_length=1, max_length=128)
    sku: Optional[str] = Field(default=None, min_length=1, max_length=128)
    strength: Optional[float] = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpsellRequest(DTOModel):
    customer_context: CustomerContext
    session_context: SessionContext
    trigger: UpsellTrigger
    selected_sku: Optional[str] = Field(default=None, min_length=1, max_length=128)
    cart_value_gbp: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def identities_match(self) -> "UpsellRequest":
        if self.customer_context.customer_id != self.session_context.customer_id:
            raise ValueError("customer and session identities must match")
        if self.selected_sku is None and self.trigger.sku is not None:
            self.selected_sku = self.trigger.sku
        return self


class UpsellEvaluationRequest(DTOModel):
    customer_id: str = Field(min_length=1, max_length=128)
    segment: str = Field(min_length=1, max_length=128)
    trigger_type: str = Field(min_length=1, max_length=128)
    trigger_strength: Optional[float] = Field(default=None, ge=0, le=1)
    selected_sku: Optional[str] = Field(default=None, min_length=1, max_length=128)


class UpsellEligibilityResult(DTOModel):
    eligible: bool
    eligible_offers: list[ServiceOfferType] = Field(default_factory=list)
    suppression_reasons: list[str] = Field(default_factory=list)
    eligibility_reasons: list[str] = Field(default_factory=list)
    cooldown_until: Optional[datetime] = None

    @model_validator(mode="after")
    def eligible_result_has_offers(self) -> "UpsellEligibilityResult":
        if self.eligible != bool(self.eligible_offers):
            raise ValueError("eligible must match whether eligible_offers is non-empty")
        return self


class OpportunityScore(DTOModel):
    score: float = Field(ge=0, le=1)
    band: Literal["LOW", "MEDIUM", "HIGH"]
    reason_codes: list[str] = Field(default_factory=list)


class UpsellDecision(DTOModel):
    should_offer: bool
    selected_offer: Optional[ServiceOfferType] = None
    opportunity_score: float = Field(default=0.0, ge=0, le=1)
    opportunity_band: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    eligibility_reasons: list[str] = Field(default_factory=list)
    suppression_reasons: list[str] = Field(default_factory=list)
    requires_customer_consent: bool = True

    @model_validator(mode="after")
    def offered_decision_has_selection(self) -> "UpsellDecision":
        if self.should_offer != (self.selected_offer is not None):
            raise ValueError(
                "selected_offer must be set exactly when should_offer is true"
            )
        return self


class UpsellResult(DTOModel):
    status: Literal["OFFER_AVAILABLE", "NO_OFFER", "FAILED"]
    should_offer: bool
    offer: Optional[ServiceOffer] = None
    opportunity_score: Optional[float] = Field(default=None, ge=0, le=1)
    opportunity_band: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    eligibility_reasons: list[str] = Field(default_factory=list)
    suppression_reasons: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    requires_customer_consent: bool = True
    decision_id: str = Field(
        default_factory=lambda: f"UPSELL-{uuid4().hex}",
        min_length=1,
    )
    trigger: Optional[UpsellTrigger] = None
    llm_invoked: bool = False

    @model_validator(mode="after")
    def offer_matches_status(self) -> "UpsellResult":
        available = self.status == "OFFER_AVAILABLE"
        if available != self.should_offer or available != (self.offer is not None):
            raise ValueError("OFFER_AVAILABLE, should_offer, and offer must agree")
        return self


class UpsellEventInput(DTOModel):
    customer_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    offer_type: ServiceOfferType
    event_type: Literal[
        "OFFER_SHOWN",
        "OFFER_ACCEPTED",
        "OFFER_DECLINED",
        "OFFER_DISMISSED",
    ]
    trigger_type: str = Field(min_length=1, max_length=128)
    timestamp: datetime


__all__ = [
    "OpportunityScore",
    "ServiceOffer",
    "ServiceOfferType",
    "UpsellDecision",
    "UpsellEligibilityResult",
    "UpsellEvaluationRequest",
    "UpsellEventInput",
    "UpsellRequest",
    "UpsellResult",
    "UpsellTrigger",
    "UpsellTriggerType",
]
