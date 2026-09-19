"""UI-facing contracts for deterministic homepage product recommendations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import Field

from models.dto import DTOModel


class HomeRecommendationProduct(DTOModel):
    """One in-stock product card returned to NeutailUI."""

    sku: str
    name: str
    brand: Optional[str] = None
    price_gbp: float = Field(ge=0)
    image_url: Optional[str] = None
    recommended_score: float = Field(ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    available: Literal[True] = True
    available_sizes: list[str] = Field(default_factory=list)
    category: Optional[str] = None
    color: Optional[str] = None
    style: Optional[str] = None


class HomeRecommendationSection(DTOModel):
    """A named group of products rendered together on the homepage."""

    section_id: str
    title: str
    category: Optional[str] = None
    products: list[HomeRecommendationProduct] = Field(default_factory=list)


class HomeRecommendationsResponse(DTOModel):
    """Authenticated, traceable homepage recommendation response."""

    recommendation_id: str
    trace_id: str
    generated_at: datetime
    status: Literal["SUCCESS", "NO_RESULTS"]
    strategy: Literal[
        "CATEGORY_AFFINITY",
        "PROFILE_PREFERENCE",
        "PROFILE_PERSONALIZATION",
    ]
    categories_used: list[str] = Field(default_factory=list)
    sections: list[HomeRecommendationSection] = Field(default_factory=list)


__all__ = [
    "HomeRecommendationProduct",
    "HomeRecommendationSection",
    "HomeRecommendationsResponse",
]
