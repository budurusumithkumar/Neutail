"""Pydantic models used at service, tool, and API boundaries.

Persistence-specific encodings are normalized here: pipe-delimited SQLite
fields become lists and the JSON-encoded brand adjustments become a mapping.
All DTOs support direct validation from the SQLAlchemy entities in
``models.entities``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Annotated, Any, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)


def _split_pipe_delimited(value: Any) -> Any:
    """Convert a pipe-delimited database value into a clean string list."""

    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split("|") if item.strip()]
    return value


def _parse_json_mapping(value: Any) -> Any:
    """Convert a JSON object stored in SQLite into a string mapping."""

    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value.strip():
            return {}
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("must be a valid JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("must contain a JSON object")
        return parsed
    return value


PipeDelimitedList = Annotated[list[str], BeforeValidator(_split_pipe_delimited)]
JsonStringMapping = Annotated[dict[str, str], BeforeValidator(_parse_json_mapping)]


class DTOModel(BaseModel):
    """Shared behavior for every Neu.Tail data-transfer object."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class CustomerDTO(DTOModel):
    """Customer identity, segmentation, and personalization preferences."""

    customer_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    join_date: Optional[date] = None
    affluence_band: Optional[str] = None
    loyalty_status: Optional[str] = None
    segment: Optional[str] = None
    annual_income_gbp: Optional[int] = None
    estimated_clv_gbp: Optional[float] = None
    price_sensitivity: Optional[float] = None
    premium_affinity: Optional[float] = None
    preferred_gender: Optional[str] = None
    preferred_categories: Optional[PipeDelimitedList] = None
    preferred_colors: Optional[PipeDelimitedList] = None
    preferred_styles: Optional[PipeDelimitedList] = None
    preferred_occasions: Optional[PipeDelimitedList] = None
    usual_size: Optional[str] = None
    fit_preference: Optional[str] = None
    marketing_consent: Optional[bool] = None
    golden_demo_customer: Optional[bool] = None


class CustomerMaster(DTOModel):
    """Authoritative customer identity and location details."""

    customer_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    join_date: Optional[date] = None


class CustomerPreferences(DTOModel):
    """Normalized customer shopping, sizing, and fit preferences."""

    preferred_gender: Optional[str] = None
    preferred_categories: PipeDelimitedList = Field(default_factory=list)
    preferred_colors: PipeDelimitedList = Field(default_factory=list)
    preferred_styles: PipeDelimitedList = Field(default_factory=list)
    preferred_occasions: PipeDelimitedList = Field(default_factory=list)
    usual_size: Optional[str] = None
    fit_preference: Optional[str] = None


class CustomerProfileFacts(DTOModel):
    """Stable facts used by the profiling agent to derive a segment."""

    affluence_band: Optional[str] = None
    loyalty_status: Optional[str] = None
    estimated_clv_gbp: Optional[float] = None
    price_sensitivity: Optional[float] = None
    premium_affinity: Optional[float] = None
    marketing_consent: Optional[bool] = None


class CustomerProfileSnapshot(DTOModel):
    """Profile facts returned by the ProfileAgent's aggregate MCP tool."""

    customer: CustomerMaster
    preferences: CustomerPreferences
    facts: CustomerProfileFacts


class CustomerContext(DTOModel):
    """Shared normalized context published by the profiling agent."""

    customer_id: str
    segment: str
    segment_code: Optional[str] = None
    affluence_band: Optional[str] = None
    loyalty_status: Optional[str] = None
    clv: Optional[float] = None
    clv_gbp: Optional[float] = None
    price_sensitivity: Optional[float] = None
    premium_affinity: Optional[float] = None
    preferences: CustomerPreferences
    loyalty_tier: Optional[str] = None
    return_rate: Optional[float] = Field(default=None, ge=0, le=1)
    order_count_12m: int = Field(default=0, ge=0)
    spend_12m_gbp: float = Field(default=0.0, ge=0)
    avg_order_value_gbp: float = Field(default=0.0, ge=0)
    category_affinity: list[str] = Field(default_factory=list)
    brand_affinity: list[str] = Field(default_factory=list)
    fit_risk_score: Optional[float] = Field(default=None, ge=0, le=1)
    loyalty_points: int = Field(default=0, ge=0)
    engagement_score: float = Field(default=0.0, ge=0, le=1)
    profile_version: str = "v1"
    data_quality: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def populate_compatible_context_names(self) -> "CustomerContext":
        """Publish explicit code/currency names while retaining old consumers."""

        if self.segment_code is None:
            self.segment_code = "_".join(self.segment.upper().split())
        if self.clv_gbp is None:
            self.clv_gbp = self.clv
        elif self.clv is None:
            self.clv = self.clv_gbp
        elif self.clv != self.clv_gbp:
            raise ValueError("clv and clv_gbp must match")
        return self


class CustomerSummary(DTOModel):
    """Compact authenticated customer record displayed by the UI."""

    customer_id: str
    display_name: str
    city: Optional[str] = None
    segment: Optional[str] = None
    loyalty_tier: Optional[str] = None
    points_balance: Optional[int] = None
    preferred_categories: list[str] = Field(default_factory=list)
    preferred_colors: list[str] = Field(default_factory=list)
    preferred_styles: list[str] = Field(default_factory=list)
    usual_size: Optional[str] = None
    fit_preference: Optional[str] = None


class LoyaltyDTO(DTOModel):
    """Current loyalty tier and points state."""

    customer_id: str
    tier: Optional[str] = None
    points_balance: Optional[int] = None
    lifetime_points: Optional[int] = None
    member_since: Optional[date] = None
    referral_count: Optional[int] = None
    streak_days: Optional[int] = None
    next_tier_points: Optional[int] = None
    last_activity_date: Optional[date] = None


class FitProfile(DTOModel):
    """Persistent sizing preferences and fit-risk signals."""

    customer_id: str
    usual_size: Optional[str] = None
    preferred_fit: Optional[str] = None
    size_confidence: Optional[float] = None
    known_brand_adjustments: Optional[JsonStringMapping] = None
    fit_issue_flags: Optional[PipeDelimitedList] = None
    return_risk_score: Optional[float] = None
    last_updated: Optional[date] = None


class FitProfileDTO(FitProfile):
    """Backward-compatible name for the persistent fit profile DTO."""


class Product(DTOModel):
    """Factual product catalogue data exposed to discovery and fit tools."""

    sku: str
    product_name: Optional[str] = None
    gender: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    brand: Optional[str] = None
    brand_tier: Optional[str] = None
    color: Optional[str] = None
    style: Optional[str] = None
    occasion: Optional[str] = None
    material: Optional[str] = None
    fit_type: Optional[str] = None
    base_price_gbp: Optional[float] = None
    current_price_gbp: Optional[float] = None
    discount_pct: Optional[int] = None
    sizes: PipeDelimitedList = Field(default_factory=list)
    rating: Optional[float] = None
    new_arrival: Optional[bool] = None
    exclusive_flag: Optional[bool] = None
    private_label_flag: Optional[bool] = None
    active: Optional[bool] = None


class ProductCandidate(Product):
    """Complete factual catalogue candidate returned to Discovery."""

    product_name: str
    category: str
    brand: str
    brand_tier: str
    color: str
    style: str
    occasion: str
    base_price_gbp: float = Field(ge=0)
    current_price_gbp: float = Field(ge=0)
    discount_pct: int = Field(ge=0, le=100)
    new_arrival: bool
    exclusive_flag: bool
    private_label_flag: bool


class ProductDTO(Product):
    """Complete persistence DTO, including the catalogue review count."""

    review_count: Optional[int] = None


class InventoryRecord(DTOModel):
    """Authoritative product stock balance at one location."""

    sku: str
    location_id: str
    location_type: Optional[str] = None
    on_hand_qty: Optional[int] = None
    reserved_qty: Optional[int] = None
    available_qty: Optional[int] = None
    last_updated: Optional[datetime] = None


class InventoryDTO(InventoryRecord):
    """Backward-compatible name for a location inventory record."""


class InventorySummary(DTOModel):
    """Aggregate availability for a SKU across stores and the DC."""

    sku: str
    total_available_qty: int
    available: bool
    locations: list[InventoryRecord] = Field(default_factory=list)


class PurchasedItem(DTOModel):
    """A purchased product line."""

    order_item_id: str
    order_id: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[int] = None
    unit_price_gbp: Optional[float] = None
    size: Optional[str] = None
    color: Optional[str] = None
    line_total_gbp: Optional[float] = None


class OrderItemDTO(PurchasedItem):
    """Backward-compatible name for a purchased order item."""


class Order(DTOModel):
    """Customer order summary."""

    order_id: str
    customer_id: Optional[str] = None
    order_datetime: Optional[datetime] = None
    channel: Optional[str] = None
    store_id: Optional[str] = None
    subtotal_gbp: Optional[float] = None
    shipping_gbp: Optional[float] = None
    total_gbp: Optional[float] = None
    payment_type: Optional[str] = None
    status: Optional[str] = None


class OrderDTO(Order):
    """Backward-compatible name for an order DTO."""


class PurchaseSummary(DTOModel):
    """Aggregated purchase behavior over a requested time window."""

    order_count: int = Field(ge=0)
    item_count: int = Field(ge=0)
    total_spend_gbp: float = Field(ge=0)
    avg_order_value_gbp: float = Field(ge=0)
    top_categories: list[str] = Field(default_factory=list)
    top_brands: list[str] = Field(default_factory=list)
    common_sizes: list[str] = Field(default_factory=list)
    channel_mix: dict[str, float] = Field(default_factory=dict)


class SizeHistoryItem(DTOModel):
    """A recent purchased size with product context for fit evidence."""

    order_item_id: str
    order_id: str
    order_datetime: datetime
    sku: str
    product_name: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    size: str
    quantity: int = Field(ge=1)


class ReturnRecord(DTOModel):
    """Returned item, reason, and resolution."""

    return_id: str
    order_item_id: Optional[str] = None
    order_id: Optional[str] = None
    customer_id: Optional[str] = None
    sku: Optional[str] = None
    return_date: Optional[date] = None
    reason_code: Optional[str] = None
    resolution: Optional[str] = None
    refund_amount_gbp: Optional[float] = None
    exchange_size: Optional[str] = None


class ReturnDTO(ReturnRecord):
    """Backward-compatible name for a returned-item DTO."""


class ReturnSummary(DTOModel):
    """Aggregated customer return behavior over a requested time window."""

    returned_items: int = Field(ge=0)
    purchased_items: int = Field(ge=0)
    return_rate: float = Field(ge=0, le=1)
    top_reason_codes: list[str] = Field(default_factory=list)
    size_related_return_count: int = Field(ge=0)
    exchange_count: int = Field(ge=0)


class BrandSizeAdjustment(DTOModel):
    """Known deterministic size adjustment for one brand."""

    brand: str
    adjustment: str
    evidence_count: int = Field(ge=0)


class FitEvidence(DTOModel):
    """Reusable factual and rule-based inputs for a fit recommendation."""

    usual_size: Optional[str] = None
    requested_size: Optional[str] = None
    product_fit_type: Optional[str] = None
    preferred_fit: Optional[str] = None
    brand_adjustment: BrandSizeAdjustment
    prior_sizes: list[str] = Field(default_factory=list)
    relevant_return_reasons: list[str] = Field(default_factory=list)
    suggested_size_candidates: list[str] = Field(default_factory=list)
    confidence_inputs: dict[str, Any] = Field(default_factory=dict)


class FitRisk(DTOModel):
    """Transparent deterministic fit-risk result."""

    risk_score: float = Field(ge=0, le=1)
    risk_band: str
    reason_codes: list[str] = Field(default_factory=list)


class BehaviorEvent(DTOModel):
    """Customer behavior event from a browsing session."""

    event_id: str
    customer_id: Optional[str] = None
    event_datetime: Optional[datetime] = None
    session_id: Optional[str] = None
    event_type: Optional[str] = None
    sku: Optional[str] = None
    search_query: Optional[str] = None
    device: Optional[str] = None
    source: Optional[str] = None
    dwell_seconds: Optional[int] = None


class ClickstreamEventDTO(BehaviorEvent):
    """Backward-compatible name for a customer behavior event."""


class BehaviorSummary(DTOModel):
    """Aggregated engagement signals from clickstream behavior."""

    session_count: int = Field(ge=0)
    product_views: int = Field(ge=0)
    searches: int = Field(ge=0)
    avg_dwell_seconds: float = Field(ge=0)
    high_intent_events: int = Field(ge=0)
    engagement_score: float = Field(ge=0, le=1)


class ServiceEngagement(DTOModel):
    """Styling or upsell-service engagement signal."""

    engagement_id: str
    customer_id: Optional[str] = None
    event_datetime: Optional[datetime] = None
    service_type: Optional[str] = None
    outcome: Optional[str] = None
    channel: Optional[str] = None
    propensity_score: Optional[float] = None
    offer_suppressed: Optional[bool] = None


class ServiceEngagementDTO(ServiceEngagement):
    """Backward-compatible name for a service engagement DTO."""


class LoyaltyTransaction(DTOModel):
    """Loyalty points accrual or adjustment event."""

    loyalty_txn_id: str
    customer_id: Optional[str] = None
    event_datetime: Optional[datetime] = None
    event_type: Optional[str] = None
    points_delta: Optional[int] = None
    reference_id: Optional[str] = None
    description: Optional[str] = None


class LoyaltyTransactionDTO(LoyaltyTransaction):
    """Backward-compatible name for a loyalty transaction DTO."""


class LoyaltyProfile(DTOModel):
    """Current loyalty tier, balance, and engagement state."""

    customer_id: str
    tier: Optional[str] = None
    points_balance: Optional[int] = None
    lifetime_points: Optional[int] = None
    member_since: Optional[date] = None
    referral_count: Optional[int] = None
    streak_days: Optional[int] = None
    next_tier_points: Optional[int] = None
    last_activity_date: Optional[date] = None


class LoyaltySummary(DTOModel):
    """Loyalty tier progress and recent activity indicators."""

    tier: Optional[str] = None
    points_balance: int = Field(ge=0)
    points_to_next_tier: int = Field(ge=0)
    lifetime_points: int = Field(ge=0)
    streak_days: int = Field(ge=0)
    recent_activity_count: int = Field(ge=0)


class DemoScenarioDTO(DTOModel):
    """Curated prompt and expected behavior used in demonstrations."""

    scenario_id: str
    customer_id: Optional[str] = None
    title: Optional[str] = None
    prompt_1: Optional[str] = None
    expected: Optional[str] = None


class CustomerProfileDTO(CustomerDTO):
    """Customer 360 aggregate returned by the profile service."""

    loyalty: Optional[LoyaltyDTO] = None
    fit_profile: Optional[FitProfileDTO] = None


class OrderDetailDTO(OrderDTO):
    """Order header with purchased items and any return records."""

    items: list[OrderItemDTO] = Field(default_factory=list)
    returns: list[ReturnDTO] = Field(default_factory=list)


class ProductAvailabilityDTO(ProductDTO):
    """Product catalogue record with stock by location."""

    inventory: list[InventoryDTO] = Field(default_factory=list)


class ProductSearchCriteria(DTOModel):
    """Structured, non-personalized catalogue search filters."""

    gender: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    occasion: Optional[str] = None
    colors: PipeDelimitedList = Field(default_factory=list)
    styles: PipeDelimitedList = Field(default_factory=list)
    brand_tier: Optional[str] = None
    min_price: Optional[float] = Field(default=None, ge=0)
    max_price: Optional[float] = Field(default=None, ge=0)
    sizes: PipeDelimitedList = Field(default_factory=list)
    active: bool = True
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_price_range(self) -> "ProductSearchCriteria":
        """Reject an inverted price range before it reaches a repository."""

        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price")
        return self


ProductSearchRequest = ProductSearchCriteria


class ProductSearchInput(DTOModel):
    """Structured catalogue constraints exposed to the Discovery Agent."""

    category: Optional[str] = None
    subcategory: Optional[str] = None
    occasion: Optional[str] = None
    colors: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    brand_tier: Optional[str] = None
    min_price: Optional[float] = Field(default=None, ge=0)
    max_price: Optional[float] = Field(default=None, ge=0)
    sizes: list[str] = Field(default_factory=list)
    active: bool = True
    limit: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def validate_price_range(self) -> "ProductSearchInput":
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot exceed max_price")
        return self

    def to_catalog_criteria(self) -> ProductSearchCriteria:
        return ProductSearchCriteria.model_validate(self.model_dump())


class SemanticProductSearchInput(DTOModel):
    """Semantic catalogue query with optional stable metadata filters."""

    query: str = Field(min_length=1, max_length=2_000)
    category: Optional[str] = None
    occasion: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class SemanticProductMatch(DTOModel):
    """Vector retrieval result; facts are fetched separately by SKU."""

    sku: str
    similarity_score: float = Field(ge=0, le=1)


class ProductSearchResult(DTOModel):
    """Catalogue matches and the factual criteria used to retrieve them."""

    criteria: ProductSearchCriteria
    total_matches: int = Field(ge=0)
    products: list[Product] = Field(default_factory=list)


class ReturnStatsDTO(DTOModel):
    """Aggregated purchase and return metrics for one customer."""

    customer_id: str
    purchased_items: int = Field(ge=0)
    returned_items: int = Field(ge=0)
    return_rate: Optional[float] = Field(default=None, ge=0, le=1)


class UpsellEvaluationInput(DTOModel):
    """Session and customer signals used by deterministic upsell policy."""

    customer_id: str
    session_id: str
    current_intent: str
    engagement_score: float = Field(ge=0, le=1)
    selected_sku: Optional[str] = None
    fit_risk: Optional[float] = Field(default=None, ge=0, le=1)
    interaction_count: int = Field(ge=0)


class ServiceCandidate(DTOModel):
    """One deterministic service opportunity and its eligibility reasons."""

    service_code: str
    service_name: str
    eligible: bool
    reason_codes: list[str] = Field(default_factory=list)


class UpsellDecision(DTOModel):
    """Final deterministic permission and recommended offer action."""

    eligible: bool
    action: str
    recommended_service: Optional[ServiceCandidate] = None
    propensity_score: Optional[float] = Field(default=None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list)
    suppression_reason: Optional[str] = None


class SuppressionResult(DTOModel):
    """Whether another offer is blocked for the current customer/session."""

    suppressed: bool
    reason: Optional[str] = None
    expires_at: Optional[datetime] = None


__all__ = [
    "BehaviorEvent",
    "BehaviorSummary",
    "BrandSizeAdjustment",
    "ClickstreamEventDTO",
    "CustomerContext",
    "CustomerDTO",
    "CustomerMaster",
    "CustomerPreferences",
    "CustomerProfileDTO",
    "CustomerProfileFacts",
    "CustomerProfileSnapshot",
    "CustomerSummary",
    "DTOModel",
    "DemoScenarioDTO",
    "FitEvidence",
    "FitProfile",
    "FitProfileDTO",
    "FitRisk",
    "InventoryDTO",
    "InventoryRecord",
    "InventorySummary",
    "LoyaltyDTO",
    "LoyaltyProfile",
    "LoyaltySummary",
    "LoyaltyTransaction",
    "LoyaltyTransactionDTO",
    "Order",
    "OrderDTO",
    "OrderDetailDTO",
    "OrderItemDTO",
    "Product",
    "ProductCandidate",
    "ProductAvailabilityDTO",
    "ProductDTO",
    "ProductSearchCriteria",
    "ProductSearchInput",
    "ProductSearchRequest",
    "ProductSearchResult",
    "PurchaseSummary",
    "PurchasedItem",
    "ReturnDTO",
    "ReturnRecord",
    "ReturnSummary",
    "ReturnStatsDTO",
    "ServiceCandidate",
    "ServiceEngagement",
    "ServiceEngagementDTO",
    "SizeHistoryItem",
    "SemanticProductMatch",
    "SemanticProductSearchInput",
    "SuppressionResult",
    "UpsellDecision",
    "UpsellEvaluationInput",
]
