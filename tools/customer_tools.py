"""FastMCP adapters for customer profile and history capabilities."""

from __future__ import annotations

from typing import Optional

from models.dto import (
    BehaviorSummary,
    CustomerMaster,
    CustomerPreferences,
    CustomerProfileFacts,
    CustomerProfileSnapshot,
    LoyaltyProfile,
    LoyaltySummary,
    LoyaltyTransaction,
    Order,
    PurchasedItem,
    PurchaseSummary,
    ReturnRecord,
    ReturnSummary,
    SizeHistoryItem,
)
from services.customer_profile_service import CustomerProfileService
from services.engagement_service import EngagementService
from services.loyalty_service import LoyaltyService
from services.order_history_service import OrderHistoryService
from services.return_history_service import ReturnHistoryService
from tools.contracts import tool_contract
from tools.runtime import get_runtime


@tool_contract(
    name="get_customer_profile",
    title="Get Customer Profile",
    description="Return the customer master, normalized preferences, and factual profile inputs.",
    capability="profile.customer.snapshot",
)
def get_customer_profile(customer_id: str) -> CustomerProfileSnapshot:
    """ProfileAgent-only aggregate over authoritative profile capabilities."""

    with get_runtime().session() as session:
        service = CustomerProfileService(session)
        return CustomerProfileSnapshot(
            customer=service.get_customer(customer_id),
            preferences=service.get_preferences(customer_id),
            facts=service.get_profile_facts(customer_id),
        )


@tool_contract(
    name="get_purchase_history",
    title="Get Purchase History",
    description="Return deterministic twelve-month purchase behavior for profile enrichment.",
    capability="profile.purchase.summary",
)
def get_purchase_history(customer_id: str, months: int = 12) -> PurchaseSummary:
    with get_runtime().session() as session:
        return OrderHistoryService(session).get_purchase_summary(customer_id, months)


@tool_contract(
    name="get_return_history",
    title="Get Return History",
    description="Return deterministic twelve-month return behavior for profile enrichment.",
    capability="profile.returns.summary",
)
def get_return_history(customer_id: str, months: int = 12) -> ReturnSummary:
    with get_runtime().session() as session:
        return ReturnHistoryService(session).get_return_summary(customer_id, months)


@tool_contract(
    name="get_loyalty_profile",
    title="Get Loyalty Profile",
    description="Return the current loyalty tier and points used by the shared profile.",
    capability="profile.loyalty.current",
)
def get_loyalty_profile(customer_id: str) -> LoyaltyProfile:
    with get_runtime().session() as session:
        return LoyaltyService(session).get_loyalty_profile(customer_id)


@tool_contract(
    name="get_engagement_summary",
    title="Get Engagement Summary",
    description="Return recent deterministic behavior signals for profile enrichment.",
    capability="profile.engagement.summary",
)
def get_engagement_summary(customer_id: str) -> BehaviorSummary:
    with get_runtime().session() as session:
        return EngagementService(session).get_behavior_summary(customer_id)


@tool_contract(
    name="customer_exists",
    title="Check Customer",
    description="Confirm that a customer ID exists before creating context or calling customer tools.",
    capability="customer.identity.validate",
)
def customer_exists(customer_id: str) -> bool:
    with get_runtime().session() as session:
        return CustomerProfileService(session).exists(customer_id)


@tool_contract(
    name="customer_get_master",
    title="Get Customer Master",
    description="Fetch authoritative customer identity, location, and join-date facts.",
    capability="customer.profile.master",
)
def customer_get_master(customer_id: str) -> CustomerMaster:
    with get_runtime().session() as session:
        return CustomerProfileService(session).get_customer(customer_id)


@tool_contract(
    name="customer_get_preferences",
    title="Get Customer Preferences",
    description="Fetch normalized category, color, style, occasion, size, and fit preferences.",
    capability="customer.profile.preferences",
)
def customer_get_preferences(customer_id: str) -> CustomerPreferences:
    with get_runtime().session() as session:
        return CustomerProfileService(session).get_preferences(customer_id)


@tool_contract(
    name="customer_get_profile_facts",
    title="Get Customer Profile Facts",
    description="Fetch factual affluence, loyalty, CLV, price, premium-affinity, and consent inputs.",
    capability="customer.profile.facts",
)
def customer_get_profile_facts(customer_id: str) -> CustomerProfileFacts:
    with get_runtime().session() as session:
        return CustomerProfileService(session).get_profile_facts(customer_id)


@tool_contract(
    name="customer_get_orders",
    title="Get Customer Orders",
    description="Return customer orders within a calendar-month history window, newest first.",
    capability="customer.orders.list",
)
def customer_get_orders(customer_id: str, months: int = 12) -> list[Order]:
    with get_runtime().session() as session:
        return OrderHistoryService(session).get_orders(customer_id, months)


@tool_contract(
    name="customer_get_order_items",
    title="Get Purchased Items",
    description="Return item-level purchase history within a calendar-month window.",
    capability="customer.orders.items",
)
def customer_get_order_items(
    customer_id: str, months: int = 12
) -> list[PurchasedItem]:
    with get_runtime().session() as session:
        return OrderHistoryService(session).get_order_items(customer_id, months)


@tool_contract(
    name="customer_get_purchase_summary",
    title="Get Purchase Summary",
    description="Aggregate customer spend, categories, brands, sizes, and channel mix.",
    capability="customer.orders.summary",
)
def customer_get_purchase_summary(
    customer_id: str, months: int = 12
) -> PurchaseSummary:
    with get_runtime().session() as session:
        return OrderHistoryService(session).get_purchase_summary(customer_id, months)


@tool_contract(
    name="customer_get_recent_sizes",
    title="Get Recent Purchased Sizes",
    description="Return recent purchased sizes with product evidence and an optional category filter.",
    capability="customer.orders.sizes",
)
def customer_get_recent_sizes(
    customer_id: str, category: Optional[str] = None
) -> list[SizeHistoryItem]:
    with get_runtime().session() as session:
        return OrderHistoryService(session).get_recent_sizes(customer_id, category)


@tool_contract(
    name="customer_get_returns",
    title="Get Customer Returns",
    description="Return detailed customer return history within a calendar-month window.",
    capability="customer.returns.list",
)
def customer_get_returns(
    customer_id: str, months: int = 12
) -> list[ReturnRecord]:
    with get_runtime().session() as session:
        return ReturnHistoryService(session).get_returns(customer_id, months)


@tool_contract(
    name="customer_get_return_summary",
    title="Get Return Summary",
    description="Calculate factual return rate, top reasons, size-related returns, and exchanges.",
    capability="customer.returns.summary",
)
def customer_get_return_summary(
    customer_id: str, months: int = 12
) -> ReturnSummary:
    with get_runtime().session() as session:
        return ReturnHistoryService(session).get_return_summary(customer_id, months)


@tool_contract(
    name="customer_get_product_returns",
    title="Get Product Return Evidence",
    description="Fetch a customer's return evidence filtered by optional SKU and brand.",
    capability="customer.returns.product",
)
def customer_get_product_returns(
    customer_id: str,
    sku: Optional[str] = None,
    brand: Optional[str] = None,
) -> list[ReturnRecord]:
    with get_runtime().session() as session:
        return ReturnHistoryService(session).get_product_returns(
            customer_id, sku, brand
        )


@tool_contract(
    name="customer_get_size_related_returns",
    title="Get Size-Related Returns",
    description="Fetch returns caused by explicit size or fit issues for explainable fit evidence.",
    capability="customer.returns.fit",
)
def customer_get_size_related_returns(customer_id: str) -> list[ReturnRecord]:
    with get_runtime().session() as session:
        return ReturnHistoryService(session).get_size_related_returns(customer_id)


@tool_contract(
    name="customer_get_loyalty_profile",
    title="Get Loyalty Profile",
    description="Fetch the customer's current loyalty tier and points state.",
    capability="customer.loyalty.profile",
)
def customer_get_loyalty_profile(customer_id: str) -> LoyaltyProfile:
    with get_runtime().session() as session:
        return LoyaltyService(session).get_loyalty_profile(customer_id)


@tool_contract(
    name="customer_get_loyalty_transactions",
    title="Get Loyalty Transactions",
    description="Return recent customer loyalty points activity in reverse chronological order.",
    capability="customer.loyalty.transactions",
)
def customer_get_loyalty_transactions(
    customer_id: str, limit: int = 20
) -> list[LoyaltyTransaction]:
    with get_runtime().session() as session:
        return LoyaltyService(session).get_loyalty_transactions(customer_id, limit)


@tool_contract(
    name="customer_get_loyalty_summary",
    title="Get Loyalty Summary",
    description="Calculate tier progress and recent loyalty engagement indicators.",
    capability="customer.loyalty.summary",
)
def customer_get_loyalty_summary(customer_id: str) -> LoyaltySummary:
    with get_runtime().session() as session:
        return LoyaltyService(session).get_loyalty_summary(customer_id)


CUSTOMER_TOOLS = (
    get_customer_profile,
    get_purchase_history,
    get_return_history,
    get_loyalty_profile,
    get_engagement_summary,
    customer_exists,
    customer_get_master,
    customer_get_preferences,
    customer_get_profile_facts,
    customer_get_orders,
    customer_get_order_items,
    customer_get_purchase_summary,
    customer_get_recent_sizes,
    customer_get_returns,
    customer_get_return_summary,
    customer_get_product_returns,
    customer_get_size_related_returns,
    customer_get_loyalty_profile,
    customer_get_loyalty_transactions,
    customer_get_loyalty_summary,
)


__all__ = ["CUSTOMER_TOOLS"]
