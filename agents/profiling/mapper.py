"""Mapping and transparent derived attributes for customer profiling."""

from __future__ import annotations

from typing import Optional

from agents.profiling.models import EnrichedCustomerProfileFacts
from agents.profiling.segment_classifier import NeuTailSegment, SegmentClassifier
from models.dto import (
    BehaviorSummary,
    CustomerContext,
    CustomerProfileSnapshot,
    LoyaltyProfile,
    PurchaseSummary,
    ReturnSummary,
)


def build_profile_facts(
    snapshot: CustomerProfileSnapshot,
    purchase: Optional[PurchaseSummary],
    returns: Optional[ReturnSummary],
    loyalty: Optional[LoyaltyProfile],
    engagement: Optional[BehaviorSummary],
) -> EnrichedCustomerProfileFacts:
    """Normalize five tool results into one explainable fact model."""

    profile = snapshot.facts
    preferences = snapshot.preferences
    classifier = SegmentClassifier()
    affluence_band = classifier.normalize_affluence(profile.affluence_band)
    loyalty_status = classifier.normalize_loyalty(profile.loyalty_status)

    return_rate = returns.return_rate if returns is not None else 0.0
    fit_risk_score = _fit_risk(returns) if returns is not None else None
    return EnrichedCustomerProfileFacts(
        customer_id=snapshot.customer.customer_id,
        affluence_band=affluence_band,
        loyalty_status=loyalty_status,
        estimated_clv_gbp=profile.estimated_clv_gbp,
        price_sensitivity=profile.price_sensitivity,
        premium_affinity=profile.premium_affinity,
        marketing_consent=profile.marketing_consent,
        order_count_12m=purchase.order_count if purchase else 0,
        spend_12m_gbp=purchase.total_spend_gbp if purchase else 0.0,
        avg_order_value_gbp=purchase.avg_order_value_gbp if purchase else 0.0,
        return_rate=return_rate,
        size_related_return_count=(
            returns.size_related_return_count if returns else 0
        ),
        fit_risk_score=fit_risk_score,
        loyalty_tier=loyalty.tier if loyalty else None,
        loyalty_points=max(loyalty.points_balance or 0, 0) if loyalty else 0,
        engagement_score=engagement.engagement_score if engagement else 0.0,
        preferred_categories=preferences.preferred_categories,
        preferred_colors=preferences.preferred_colors,
        preferred_styles=preferences.preferred_styles,
        preferred_occasions=preferences.preferred_occasions,
        usual_size=preferences.usual_size,
        fit_preference=preferences.fit_preference,
        category_affinity=(
            purchase.top_categories if purchase else preferences.preferred_categories
        ),
        brand_affinity=purchase.top_brands if purchase else [],
    )


def publish_customer_context(
    snapshot: CustomerProfileSnapshot,
    facts: EnrichedCustomerProfileFacts,
    segment: NeuTailSegment,
    data_quality: dict[str, str],
) -> CustomerContext:
    """Publish the sole shared profile representation used downstream."""

    return CustomerContext(
        customer_id=facts.customer_id,
        segment=segment.value,
        segment_code=segment.name,
        affluence_band=facts.affluence_band,
        loyalty_status=facts.loyalty_status,
        clv=facts.estimated_clv_gbp,
        clv_gbp=facts.estimated_clv_gbp,
        price_sensitivity=facts.price_sensitivity,
        premium_affinity=facts.premium_affinity,
        preferences=snapshot.preferences,
        loyalty_tier=facts.loyalty_tier,
        return_rate=facts.return_rate,
        order_count_12m=facts.order_count_12m,
        spend_12m_gbp=facts.spend_12m_gbp,
        avg_order_value_gbp=facts.avg_order_value_gbp,
        category_affinity=facts.category_affinity,
        brand_affinity=facts.brand_affinity,
        fit_risk_score=facts.fit_risk_score,
        loyalty_points=facts.loyalty_points,
        engagement_score=facts.engagement_score,
        profile_version="v1",
        data_quality=data_quality,
    )


def _fit_risk(summary: ReturnSummary) -> float:
    """Combine overall and size-related return evidence without an ML model."""

    size_ratio = (
        summary.size_related_return_count / summary.returned_items
        if summary.returned_items
        else 0.0
    )
    return round(min(summary.return_rate * 0.65 + size_ratio * 0.35, 1.0), 3)


__all__ = ["build_profile_facts", "publish_customer_context"]
