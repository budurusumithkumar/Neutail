"""Typed inputs and normalized facts owned by the Profiling Agent."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from models.dto import DTOModel


AffluenceBand = Literal["Affluent", "Less Affluent"]
LoyaltyStatus = Literal["Loyal", "New"]


class ProfileAgentRequest(DTOModel):
    """Small orchestrator/API request accepted by the Profiling Agent."""

    customer_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    refresh: bool = False


class EnrichedCustomerProfileFacts(DTOModel):
    """Normalized, explainable inputs used to publish customer context."""

    customer_id: str
    affluence_band: AffluenceBand
    loyalty_status: LoyaltyStatus
    estimated_clv_gbp: Optional[float] = Field(default=None, ge=0)
    price_sensitivity: Optional[float] = Field(default=None, ge=0, le=1)
    premium_affinity: Optional[float] = Field(default=None, ge=0, le=1)
    marketing_consent: Optional[bool] = None
    order_count_12m: int = Field(default=0, ge=0)
    spend_12m_gbp: float = Field(default=0.0, ge=0)
    avg_order_value_gbp: float = Field(default=0.0, ge=0)
    return_rate: float = Field(default=0.0, ge=0, le=1)
    size_related_return_count: int = Field(default=0, ge=0)
    fit_risk_score: Optional[float] = Field(default=None, ge=0, le=1)
    loyalty_tier: Optional[str] = None
    loyalty_points: int = Field(default=0, ge=0)
    engagement_score: float = Field(default=0.0, ge=0, le=1)
    preferred_categories: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    preferred_styles: list[str] = Field(default_factory=list)
    preferred_occasions: list[str] = Field(default_factory=list)
    usual_size: Optional[str] = None
    fit_preference: Optional[str] = None
    category_affinity: list[str] = Field(default_factory=list)
    brand_affinity: list[str] = Field(default_factory=list)


__all__ = [
    "AffluenceBand",
    "EnrichedCustomerProfileFacts",
    "LoyaltyStatus",
    "ProfileAgentRequest",
]

