"""Reset and seed Alice/Bob for the third-purchase segmentation demo."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import delete, select

from database.migrations import ensure_schema
from database.session import create_session_factory
from models.entities import (
    Customer,
    CustomerSegmentHistory,
    EventInbox,
    FitProfile,
    Loyalty,
    Order,
    OrderItem,
    OutboxEvent,
    Product,
)


DEMO_CUSTOMERS = (
    {
        "customer_id": "CUST041",
        "first_name": "Alice",
        "last_name": "Morgan",
        "email": "alice.demo@demo.neutail.local",
        "city": "London",
        "affluence_band": "Affluent",
        "segment": "Aspiring Loyalist",
        "annual_income_gbp": 145_000,
        "estimated_clv_gbp": 8_500.0,
        "price_sensitivity": 0.2,
        "premium_affinity": 0.9,
        "preferred_gender": "Women",
        "preferred_categories": "Dresses|Outerwear",
        "preferred_colors": "Navy|Burgundy|White",
        "preferred_styles": "Classic|Tailored",
        "preferred_occasions": "Formal|Wedding",
        "usual_size": "10",
        "fit_preference": "Tailored",
        "tier": "Bronze",
        "points_balance": 800,
        "sku": "SKU00008",
    },
    {
        "customer_id": "CUST042",
        "first_name": "Bob",
        "last_name": "Reed",
        "email": "bob.demo@demo.neutail.local",
        "city": "Manchester",
        "affluence_band": "Less Affluent",
        "segment": "Price Explorer",
        "annual_income_gbp": 38_000,
        "estimated_clv_gbp": 1_600.0,
        "price_sensitivity": 0.9,
        "premium_affinity": 0.2,
        "preferred_gender": "Men",
        "preferred_categories": "Shirts|Trousers",
        "preferred_colors": "Navy|Grey|Cream",
        "preferred_styles": "Classic|Relaxed",
        "preferred_occasions": "Work|Everyday",
        "usual_size": "M",
        "fit_preference": "Regular",
        "tier": "Bronze",
        "points_balance": 350,
        "sku": "SKU00002",
    },
)

PURCHASE_DATES = (
    datetime(2026, 8, 1, 10, 0, 0),
    datetime(2026, 8, 20, 10, 0, 0),
)


def seed() -> None:
    engine, session_factory = create_session_factory()
    ensure_schema(engine)
    with session_factory() as session:
        for spec in DEMO_CUSTOMERS:
            customer_id = spec["customer_id"]
            order_ids = list(
                session.scalars(
                    select(Order.order_id).where(Order.customer_id == customer_id)
                ).all()
            )
            if order_ids:
                session.execute(
                    delete(OrderItem).where(OrderItem.order_id.in_(order_ids))
                )
                session.execute(delete(Order).where(Order.order_id.in_(order_ids)))

            source_event_ids = list(
                session.scalars(
                    select(CustomerSegmentHistory.source_event_id).where(
                        CustomerSegmentHistory.customer_id == customer_id
                    )
                ).all()
            )
            session.execute(
                delete(CustomerSegmentHistory).where(
                    CustomerSegmentHistory.customer_id == customer_id
                )
            )
            session.execute(
                delete(OutboxEvent).where(OutboxEvent.aggregate_id == customer_id)
            )
            if source_event_ids:
                session.execute(
                    delete(EventInbox).where(
                        EventInbox.event_id.in_(source_event_ids)
                    )
                )

            customer = session.get(Customer, customer_id) or Customer(
                customer_id=customer_id
            )
            customer.first_name = spec["first_name"]
            customer.last_name = spec["last_name"]
            customer.email = spec["email"]
            customer.city = spec["city"]
            customer.country = "United Kingdom"
            customer.join_date = date(2026, 1, 1)
            customer.affluence_band = spec["affluence_band"]
            customer.loyalty_status = "New"
            customer.segment = spec["segment"]
            customer.annual_income_gbp = spec["annual_income_gbp"]
            customer.estimated_clv_gbp = spec["estimated_clv_gbp"]
            customer.price_sensitivity = spec["price_sensitivity"]
            customer.premium_affinity = spec["premium_affinity"]
            customer.preferred_gender = spec["preferred_gender"]
            customer.preferred_categories = spec["preferred_categories"]
            customer.preferred_colors = spec["preferred_colors"]
            customer.preferred_styles = spec["preferred_styles"]
            customer.preferred_occasions = spec["preferred_occasions"]
            customer.usual_size = spec["usual_size"]
            customer.fit_preference = spec["fit_preference"]
            customer.marketing_consent = True
            customer.golden_demo_customer = True
            customer.profile_version = 1
            customer.updated_at = datetime(2026, 8, 20, 10, 0, 0)
            session.add(customer)

            loyalty = session.get(Loyalty, customer_id) or Loyalty(
                customer_id=customer_id
            )
            loyalty.tier = spec["tier"]
            loyalty.points_balance = spec["points_balance"]
            loyalty.lifetime_points = spec["points_balance"]
            loyalty.member_since = date(2026, 1, 1)
            loyalty.referral_count = 0
            loyalty.streak_days = 2
            loyalty.next_tier_points = 2_000
            loyalty.last_activity_date = date(2026, 8, 20)
            loyalty.version = 1
            loyalty.updated_at = datetime(2026, 8, 20, 10, 0, 0)
            session.add(loyalty)

            fit = session.get(FitProfile, customer_id) or FitProfile(
                customer_id=customer_id
            )
            fit.usual_size = spec["usual_size"]
            fit.preferred_fit = spec["fit_preference"]
            fit.size_confidence = 0.8
            fit.known_brand_adjustments = "{}"
            fit.fit_issue_flags = ""
            fit.return_risk_score = 0.1
            fit.last_updated = date(2026, 8, 20)
            session.add(fit)

            product = session.get(Product, spec["sku"])
            if product is None:
                raise RuntimeError(f"Seed product {spec['sku']} was not found")
            unit_price = product.current_price_gbp or 0.0
            for index, purchased_at in enumerate(PURCHASE_DATES, start=1):
                order_id = f"{customer_id}-PROFILE-{index:02d}"
                session.add(
                    Order(
                        order_id=order_id,
                        customer_id=customer_id,
                        order_datetime=purchased_at,
                        channel="WEB",
                        subtotal_gbp=unit_price,
                        shipping_gbp=0.0,
                        total_gbp=unit_price,
                        payment_type="DEMO",
                        status="COMPLETED",
                        items=[
                            OrderItem(
                                order_item_id=f"{order_id}-ITEM-1",
                                order_id=order_id,
                                sku=spec["sku"],
                                quantity=1,
                                unit_price_gbp=unit_price,
                                size=spec["usual_size"],
                                color=product.color,
                                line_total_gbp=unit_price,
                            )
                        ],
                    )
                )
        session.commit()
    engine.dispose()


if __name__ == "__main__":
    seed()
