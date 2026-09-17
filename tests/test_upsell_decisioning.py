from __future__ import annotations

import pytest

from agents.upsell.offer_selector import OfferSelector
from agents.upsell.opportunity_scorer import OpportunityScorer
from models.dto import (
    BehaviorSummary,
    CustomerContext,
    CustomerPreferences,
    LoyaltyProfile,
)
from models.upsell import (
    OpportunityScore,
    ServiceOfferType,
    UpsellEligibilityResult,
    UpsellTrigger,
)


def _context(segment: str = "Prestige Champion") -> CustomerContext:
    return CustomerContext(
        customer_id="CUST001",
        segment=segment,
        premium_affinity=0.91,
        price_sensitivity=0.20,
        loyalty_status="Loyal",
        loyalty_tier="Platinum",
        preferences=CustomerPreferences(),
    )


def _trigger(trigger_type: str, strength: float = 0.91) -> UpsellTrigger:
    return UpsellTrigger(
        trigger_type=trigger_type,
        source_agent="TestAgent",
        strength=strength,
    )


def _opportunity(score: float = 0.90, band: str = "HIGH") -> OpportunityScore:
    return OpportunityScore(score=score, band=band, reason_codes=[])


def test_opportunity_score_is_weighted_and_explainable():
    result = OpportunityScorer().calculate(
        customer_context=_context(),
        loyalty=LoyaltyProfile(customer_id="CUST001", tier="Platinum"),
        engagement=BehaviorSummary(
            session_count=3,
            product_views=8,
            searches=2,
            avg_dwell_seconds=100,
            high_intent_events=2,
            engagement_score=0.90,
        ),
        trigger=_trigger("HIGH_PRODUCT_ENGAGEMENT"),
        eligibility=UpsellEligibilityResult(
            eligible=True,
            eligible_offers=[ServiceOfferType.STYLING_ADVISORY],
            eligibility_reasons=["HIGH_PRODUCT_ENGAGEMENT"],
        ),
    )

    assert result.band == "HIGH"
    assert result.score > 0.85
    assert "HIGH_PREMIUM_AFFINITY" in result.reason_codes
    assert "LOYAL_CUSTOMER" in result.reason_codes


def test_opportunity_scoring_cannot_run_before_eligibility():
    with pytest.raises(ValueError, match="requires an eligible"):
        OpportunityScorer().calculate(
            customer_context=_context(),
            loyalty=LoyaltyProfile(customer_id="CUST001", tier="Platinum"),
            engagement=BehaviorSummary(
                session_count=0,
                product_views=0,
                searches=0,
                avg_dwell_seconds=0,
                high_intent_events=0,
                engagement_score=0,
            ),
            trigger=_trigger("HIGH_PRODUCT_ENGAGEMENT"),
            eligibility=UpsellEligibilityResult(eligible=False),
        )


def test_chronic_fit_risk_prioritizes_styling_advisory():
    selected = OfferSelector().select(
        eligible_offers=[
            ServiceOfferType.STYLE_PLUS_TRIAL,
            ServiceOfferType.STYLING_ADVISORY,
        ],
        opportunity=_opportunity(),
        trigger=_trigger("CHRONIC_FIT_RISK", 0.82),
        customer_context=_context(),
    )

    assert selected is ServiceOfferType.STYLING_ADVISORY


def test_value_defender_prefers_value_first_styling():
    selected = OfferSelector().select(
        eligible_offers=[
            ServiceOfferType.STYLING_ADVISORY,
            ServiceOfferType.STYLE_PLUS_TRIAL,
        ],
        opportunity=_opportunity(),
        trigger=_trigger("HIGH_PRODUCT_ENGAGEMENT"),
        customer_context=_context("Value Defender"),
    )

    assert selected is ServiceOfferType.STYLING_ADVISORY


def test_high_premium_engagement_can_select_trial():
    selected = OfferSelector().select(
        eligible_offers=[
            ServiceOfferType.STYLING_ADVISORY,
            ServiceOfferType.STYLE_PLUS_TRIAL,
        ],
        opportunity=_opportunity(),
        trigger=_trigger("HIGH_PRODUCT_ENGAGEMENT"),
        customer_context=_context(),
    )

    assert selected is ServiceOfferType.STYLE_PLUS_TRIAL


def test_selector_never_invents_an_offer():
    selected = OfferSelector().select(
        eligible_offers=[],
        opportunity=_opportunity(),
        trigger=_trigger("HIGH_PRODUCT_ENGAGEMENT"),
        customer_context=_context(),
    )

    assert selected is None
