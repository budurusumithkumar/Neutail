"""Typed contracts owned by the Discovery Agent."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from models.dto import CustomerContext, DTOModel, Product
from orchestrator.models import SessionContext


class RetrievalStrategy(str, Enum):
    STRUCTURED = "STRUCTURED"
    SEMANTIC = "SEMANTIC"
    SIMILAR_ITEM = "SIMILAR_ITEM"
    HYBRID = "HYBRID"


class DiscoveryCriteria(DTOModel):
    category: Optional[str] = None
    subcategory: Optional[str] = None
    occasion: Optional[str] = None
    colors: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    min_price: Optional[float] = Field(default=None, ge=0)
    max_price: Optional[float] = Field(default=None, ge=0)
    requested_size: Optional[str] = None
    semantic_query: Optional[str] = None

    @model_validator(mode="after")
    def validate_price_range(self) -> "DiscoveryCriteria":
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price")
        return self


class DiscoveryRequest(DTOModel):
    query: str = Field(min_length=1, max_length=2_000)
    customer_context: CustomerContext
    session_context: SessionContext
    criteria: Optional[DiscoveryCriteria] = None
    trace_id: Optional[str] = None
    max_results: int = Field(default=5, ge=1, le=20)


class RankingScore(DTOModel):
    sku: str
    total_score: float = Field(ge=0, le=1)
    occasion_score: float = Field(ge=0, le=1)
    style_score: float = Field(ge=0, le=1)
    color_score: float = Field(ge=0, le=1)
    segment_score: float = Field(ge=0, le=1)
    price_score: float = Field(ge=0, le=1)
    semantic_score: float = Field(ge=0, le=1)
    availability_score: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)


class RankedProduct(DTOModel):
    product: Product
    ranking: RankingScore


class ProductRecommendation(DTOModel):
    sku: str
    product_name: str
    price_gbp: float = Field(ge=0)
    brand: str
    score: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    brand_tier: Optional[str] = None
    color: Optional[str] = None
    style: Optional[str] = None
    occasion: Optional[str] = None
    available_sizes: list[str] = Field(default_factory=list)


class DiscoverySignal(DTOModel):
    signal_type: str
    sku: Optional[str] = None
    strength: Optional[float] = Field(default=None, ge=0, le=1)


class DiscoveryResult(DTOModel):
    status: Literal[
        "SUCCESS",
        "NO_RESULTS",
        "CLARIFICATION_REQUIRED",
        "FAILED",
    ]
    retrieval_strategy: RetrievalStrategy
    recommendations: list[ProductRecommendation] = Field(default_factory=list)
    candidates_retrieved: int = Field(ge=0)
    candidates_after_filtering: int = Field(ge=0)
    downstream_signals: list[DiscoverySignal] = Field(default_factory=list)
    ranking_scores: list[RankingScore] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProductExplanation(DTOModel):
    sku: str
    explanation: str = Field(min_length=1, max_length=800)


class DiscoveryExplanations(DTOModel):
    explanations: list[ProductExplanation] = Field(default_factory=list)


__all__ = [
    "DiscoveryCriteria",
    "DiscoveryExplanations",
    "DiscoveryRequest",
    "DiscoveryResult",
    "DiscoverySignal",
    "ProductExplanation",
    "ProductRecommendation",
    "RankedProduct",
    "RankingScore",
    "RetrievalStrategy",
]
