"""SQLAlchemy ORM entities for the Neu.Tail SQLite database.

The database stores dates and datetimes as ISO-8601 ``TEXT`` values and boolean
flags as ``INTEGER`` values. SQLAlchemy's SQLite dialect converts those values
to :class:`datetime.date`, :class:`datetime.datetime`, and :class:`bool` at the
ORM boundary.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all Neu.Tail persistence entities."""


class Customer(Base):
    """Customer master record and personalization attributes."""

    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(Text, primary_key=True)
    first_name: Mapped[Optional[str]] = mapped_column(Text)
    last_name: Mapped[Optional[str]] = mapped_column(Text)
    email: Mapped[Optional[str]] = mapped_column(Text)
    city: Mapped[Optional[str]] = mapped_column(Text)
    country: Mapped[Optional[str]] = mapped_column(Text)
    join_date: Mapped[Optional[date]] = mapped_column(Date)
    affluence_band: Mapped[Optional[str]] = mapped_column(Text)
    loyalty_status: Mapped[Optional[str]] = mapped_column(Text)
    segment: Mapped[Optional[str]] = mapped_column(Text)
    annual_income_gbp: Mapped[Optional[int]] = mapped_column(Integer)
    estimated_clv_gbp: Mapped[Optional[float]] = mapped_column(Float)
    price_sensitivity: Mapped[Optional[float]] = mapped_column(Float)
    premium_affinity: Mapped[Optional[float]] = mapped_column(Float)
    preferred_gender: Mapped[Optional[str]] = mapped_column(Text)
    preferred_categories: Mapped[Optional[str]] = mapped_column(Text)
    preferred_colors: Mapped[Optional[str]] = mapped_column(Text)
    preferred_styles: Mapped[Optional[str]] = mapped_column(Text)
    preferred_occasions: Mapped[Optional[str]] = mapped_column(Text)
    usual_size: Mapped[Optional[str]] = mapped_column(Text)
    fit_preference: Mapped[Optional[str]] = mapped_column(Text)
    marketing_consent: Mapped[Optional[bool]] = mapped_column(Boolean)
    golden_demo_customer: Mapped[Optional[bool]] = mapped_column(Boolean)
    profile_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    loyalty: Mapped[Optional["Loyalty"]] = relationship(
        back_populates="customer", uselist=False
    )
    fit_profile: Mapped[Optional["FitProfile"]] = relationship(
        back_populates="customer", uselist=False
    )
    orders: Mapped[list["Order"]] = relationship(back_populates="customer")
    returns: Mapped[list["Return"]] = relationship(back_populates="customer")
    clickstream_events: Mapped[list["ClickstreamEvent"]] = relationship(
        back_populates="customer"
    )
    service_engagements: Mapped[list["ServiceEngagement"]] = relationship(
        back_populates="customer"
    )
    loyalty_transactions: Mapped[list["LoyaltyTransaction"]] = relationship(
        back_populates="customer"
    )
    demo_scenarios: Mapped[list["DemoScenario"]] = relationship(
        back_populates="customer"
    )


class Loyalty(Base):
    """A customer's current loyalty balance and tier state."""

    __tablename__ = "loyalty"

    customer_id: Mapped[str] = mapped_column(
        Text, ForeignKey("customers.customer_id"), primary_key=True
    )
    tier: Mapped[Optional[str]] = mapped_column(Text)
    points_balance: Mapped[Optional[int]] = mapped_column(Integer)
    lifetime_points: Mapped[Optional[int]] = mapped_column(Integer)
    member_since: Mapped[Optional[date]] = mapped_column(Date)
    referral_count: Mapped[Optional[int]] = mapped_column(Integer)
    streak_days: Mapped[Optional[int]] = mapped_column(Integer)
    next_tier_points: Mapped[Optional[int]] = mapped_column(Integer)
    last_activity_date: Mapped[Optional[date]] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    customer: Mapped[Customer] = relationship(back_populates="loyalty")


class FitProfile(Base):
    """Persistent customer sizing and fit signals."""

    __tablename__ = "fit_profiles"

    customer_id: Mapped[str] = mapped_column(
        Text, ForeignKey("customers.customer_id"), primary_key=True
    )
    usual_size: Mapped[Optional[str]] = mapped_column(Text)
    preferred_fit: Mapped[Optional[str]] = mapped_column(Text)
    size_confidence: Mapped[Optional[float]] = mapped_column(Float)
    known_brand_adjustments: Mapped[Optional[str]] = mapped_column(Text)
    fit_issue_flags: Mapped[Optional[str]] = mapped_column(Text)
    return_risk_score: Mapped[Optional[float]] = mapped_column(Float)
    last_updated: Mapped[Optional[date]] = mapped_column(Date)

    customer: Mapped[Customer] = relationship(back_populates="fit_profile")


class Product(Base):
    """A product catalogue item."""

    __tablename__ = "products"
    __table_args__ = (
        Index(
            "idx_products_search", "gender", "category", "occasion", "brand_tier"
        ),
    )

    sku: Mapped[str] = mapped_column(Text, primary_key=True)
    product_name: Mapped[Optional[str]] = mapped_column(Text)
    gender: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(Text)
    subcategory: Mapped[Optional[str]] = mapped_column(Text)
    brand: Mapped[Optional[str]] = mapped_column(Text)
    brand_tier: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]] = mapped_column(Text)
    style: Mapped[Optional[str]] = mapped_column(Text)
    occasion: Mapped[Optional[str]] = mapped_column(Text)
    material: Mapped[Optional[str]] = mapped_column(Text)
    fit_type: Mapped[Optional[str]] = mapped_column(Text)
    base_price_gbp: Mapped[Optional[float]] = mapped_column(Float)
    current_price_gbp: Mapped[Optional[float]] = mapped_column(Float)
    discount_pct: Mapped[Optional[int]] = mapped_column(Integer)
    sizes: Mapped[Optional[str]] = mapped_column(Text)
    rating: Mapped[Optional[float]] = mapped_column(Float)
    review_count: Mapped[Optional[int]] = mapped_column(Integer)
    new_arrival: Mapped[Optional[bool]] = mapped_column(Boolean)
    exclusive_flag: Mapped[Optional[bool]] = mapped_column(Boolean)
    private_label_flag: Mapped[Optional[bool]] = mapped_column(Boolean)
    active: Mapped[Optional[bool]] = mapped_column(Boolean)

    inventory: Mapped[list["Inventory"]] = relationship(back_populates="product")
    order_items: Mapped[list["OrderItem"]] = relationship(back_populates="product")
    returns: Mapped[list["Return"]] = relationship(back_populates="product")


class Inventory(Base):
    """Inventory quantity for a product at a store or distribution centre."""

    __tablename__ = "inventory"

    sku: Mapped[str] = mapped_column(
        Text, ForeignKey("products.sku"), primary_key=True
    )
    location_id: Mapped[str] = mapped_column(Text, primary_key=True)
    location_type: Mapped[Optional[str]] = mapped_column(Text)
    on_hand_qty: Mapped[Optional[int]] = mapped_column(Integer)
    reserved_qty: Mapped[Optional[int]] = mapped_column(Integer)
    available_qty: Mapped[Optional[int]] = mapped_column(Integer)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    product: Mapped[Product] = relationship(back_populates="inventory")


class Order(Base):
    """Customer order header."""

    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_customer_date", "customer_id", "order_datetime"),
        Index(
            "idx_orders_customer_date_status",
            "customer_id",
            "order_datetime",
            "status",
        ),
    )

    order_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("customers.customer_id")
    )
    order_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    channel: Mapped[Optional[str]] = mapped_column(Text)
    store_id: Mapped[Optional[str]] = mapped_column(Text)
    subtotal_gbp: Mapped[Optional[float]] = mapped_column(Float)
    shipping_gbp: Mapped[Optional[float]] = mapped_column(Float)
    total_gbp: Mapped[Optional[float]] = mapped_column(Float)
    payment_type: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(Text)
    source_event_id: Mapped[Optional[str]] = mapped_column(Text, unique=True)

    customer: Mapped[Optional[Customer]] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")
    returns: Mapped[list["Return"]] = relationship(back_populates="order")


class OrderItem(Base):
    """Individual product line within an order."""

    __tablename__ = "order_items"
    __table_args__ = (Index("idx_items_order", "order_id"),)

    order_item_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("orders.order_id")
    )
    sku: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("products.sku"))
    quantity: Mapped[Optional[int]] = mapped_column(Integer)
    unit_price_gbp: Mapped[Optional[float]] = mapped_column(Float)
    size: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[str]] = mapped_column(Text)
    line_total_gbp: Mapped[Optional[float]] = mapped_column(Float)

    order: Mapped[Optional[Order]] = relationship(back_populates="items")
    product: Mapped[Optional[Product]] = relationship(back_populates="order_items")
    return_record: Mapped[Optional["Return"]] = relationship(
        back_populates="order_item", uselist=False
    )


class Return(Base):
    """A returned order item and its outcome."""

    __tablename__ = "returns"
    __table_args__ = (Index("idx_returns_customer", "customer_id"),)

    return_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_item_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("order_items.order_item_id")
    )
    order_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("orders.order_id")
    )
    customer_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("customers.customer_id")
    )
    sku: Mapped[Optional[str]] = mapped_column(Text, ForeignKey("products.sku"))
    return_date: Mapped[Optional[date]] = mapped_column(Date)
    reason_code: Mapped[Optional[str]] = mapped_column(Text)
    resolution: Mapped[Optional[str]] = mapped_column(Text)
    refund_amount_gbp: Mapped[Optional[float]] = mapped_column(Float)
    exchange_size: Mapped[Optional[str]] = mapped_column(Text)

    order_item: Mapped[Optional[OrderItem]] = relationship(
        back_populates="return_record"
    )
    order: Mapped[Optional[Order]] = relationship(back_populates="returns")
    customer: Mapped[Optional[Customer]] = relationship(back_populates="returns")
    product: Mapped[Optional[Product]] = relationship(back_populates="returns")


class ClickstreamEvent(Base):
    """A customer's browsing or search event."""

    __tablename__ = "clickstream"
    __table_args__ = (
        Index("idx_click_customer_date", "customer_id", "event_datetime"),
    )

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("customers.customer_id")
    )
    event_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    event_type: Mapped[Optional[str]] = mapped_column(Text)
    sku: Mapped[Optional[str]] = mapped_column(Text)
    search_query: Mapped[Optional[str]] = mapped_column(Text)
    device: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(Text)
    dwell_seconds: Mapped[Optional[int]] = mapped_column(Integer)

    customer: Mapped[Optional[Customer]] = relationship(
        back_populates="clickstream_events"
    )


class ServiceEngagement(Base):
    """A customer's engagement with a styling or upsell service."""

    __tablename__ = "service_engagement"

    engagement_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("customers.customer_id")
    )
    event_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    service_type: Mapped[Optional[str]] = mapped_column(Text)
    outcome: Mapped[Optional[str]] = mapped_column(Text)
    channel: Mapped[Optional[str]] = mapped_column(Text)
    propensity_score: Mapped[Optional[float]] = mapped_column(Float)
    offer_suppressed: Mapped[Optional[bool]] = mapped_column(Boolean)

    customer: Mapped[Optional[Customer]] = relationship(
        back_populates="service_engagements"
    )


class LoyaltyTransaction(Base):
    """A loyalty points accrual or adjustment event."""

    __tablename__ = "loyalty_transactions"

    loyalty_txn_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("customers.customer_id")
    )
    event_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    event_type: Mapped[Optional[str]] = mapped_column(Text)
    points_delta: Mapped[Optional[int]] = mapped_column(Integer)
    reference_id: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)

    customer: Mapped[Optional[Customer]] = relationship(
        back_populates="loyalty_transactions"
    )


class DemoScenario(Base):
    """A curated prompt and expected outcome for a live demo."""

    __tablename__ = "demo_scenarios"

    scenario_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[Optional[str]] = mapped_column(
        Text, ForeignKey("customers.customer_id")
    )
    title: Mapped[Optional[str]] = mapped_column(Text)
    prompt_1: Mapped[Optional[str]] = mapped_column(Text)
    expected: Mapped[Optional[str]] = mapped_column(Text)

    customer: Mapped[Optional[Customer]] = relationship(
        back_populates="demo_scenarios"
    )


class EventInbox(Base):
    """Durable idempotency and replay record for trusted domain events."""

    __tablename__ = "event_inbox"
    __table_args__ = (Index("idx_event_inbox_status", "status"),)

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    result_json: Mapped[Optional[str]] = mapped_column(Text)
    error_code: Mapped[Optional[str]] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class CustomerSegmentHistory(Base):
    """Auditable, event-correlated customer segment transition."""

    __tablename__ = "customer_segment_history"
    __table_args__ = (
        Index("idx_segment_history_customer", "customer_id", "changed_at"),
    )

    segment_history_id: Mapped[str] = mapped_column(Text, primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        Text, ForeignKey("customers.customer_id"), index=True
    )
    previous_segment: Mapped[Optional[str]] = mapped_column(Text)
    new_segment: Mapped[str] = mapped_column(Text)
    previous_loyalty_status: Mapped[Optional[str]] = mapped_column(Text)
    new_loyalty_status: Mapped[str] = mapped_column(Text)
    affluence_band: Mapped[str] = mapped_column(Text)
    purchase_count_90d: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[str] = mapped_column(Text, unique=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime)


class OutboxEvent(Base):
    """Transactional event awaiting in-process or external dispatch."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("idx_outbox_dispatch", "status", "created_at"),
    )

    outbox_id: Mapped[str] = mapped_column(Text, primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(Text)
    aggregate_id: Mapped[str] = mapped_column(Text, index=True)
    event_type: Mapped[str] = mapped_column(Text, index=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[str] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(Text)
    causation_id: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


__all__ = [
    "Base",
    "ClickstreamEvent",
    "Customer",
    "CustomerSegmentHistory",
    "DemoScenario",
    "EventInbox",
    "FitProfile",
    "Inventory",
    "Loyalty",
    "LoyaltyTransaction",
    "Order",
    "OrderItem",
    "OutboxEvent",
    "Product",
    "Return",
    "ServiceEngagement",
]
