"""Purchase history and deterministic purchase-behavior aggregates."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.dto import Order, PurchasedItem, PurchaseSummary, SizeHistoryItem
from models.entities import Order as OrderEntity
from models.entities import OrderItem as OrderItemEntity
from models.entities import Product as ProductEntity
from services._date_utils import (
    months_before,
    sqlite_datetime,
    utc_now,
    validate_positive_int,
)


class OrderHistoryService:
    """Expose purchase evidence without making recommendation decisions."""

    TOP_VALUE_LIMIT = 3
    RECENT_SIZE_MONTHS = 12

    def __init__(
        self,
        session: Session,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now

    def get_orders(self, customer_id: str, months: int = 12) -> list[Order]:
        """Return recent orders, newest first."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        cutoff = self._cutoff(months)
        now = self._clock()
        statement = (
            select(OrderEntity)
            .where(
                OrderEntity.customer_id == normalized_customer_id,
                func.datetime(OrderEntity.order_datetime) >= sqlite_datetime(cutoff),
                func.datetime(OrderEntity.order_datetime) <= sqlite_datetime(now),
            )
            .order_by(OrderEntity.order_datetime.desc(), OrderEntity.order_id.desc())
        )
        return [
            Order.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_order_items(
        self, customer_id: str, months: int = 12
    ) -> list[PurchasedItem]:
        """Return recent item-level purchase history, newest first."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        cutoff = self._cutoff(months)
        now = self._clock()
        statement = (
            select(OrderItemEntity)
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .where(
                OrderEntity.customer_id == normalized_customer_id,
                func.datetime(OrderEntity.order_datetime) >= sqlite_datetime(cutoff),
                func.datetime(OrderEntity.order_datetime) <= sqlite_datetime(now),
            )
            .order_by(
                OrderEntity.order_datetime.desc(),
                OrderItemEntity.order_item_id.desc(),
            )
        )
        return [
            PurchasedItem.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_purchase_summary(
        self, customer_id: str, months: int = 12
    ) -> PurchaseSummary:
        """Aggregate spend, products, sizes, and channel behavior."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        cutoff = self._cutoff(months)
        now = self._clock()

        orders_statement = select(OrderEntity).where(
            OrderEntity.customer_id == normalized_customer_id,
            func.datetime(OrderEntity.order_datetime) >= sqlite_datetime(cutoff),
            func.datetime(OrderEntity.order_datetime) <= sqlite_datetime(now),
        )
        orders = self._session.scalars(orders_statement).all()

        item_statement = (
            select(
                OrderItemEntity.quantity,
                OrderItemEntity.size,
                ProductEntity.category,
                ProductEntity.brand,
            )
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .outerjoin(ProductEntity, ProductEntity.sku == OrderItemEntity.sku)
            .where(
                OrderEntity.customer_id == normalized_customer_id,
                func.datetime(OrderEntity.order_datetime) >= sqlite_datetime(cutoff),
                func.datetime(OrderEntity.order_datetime) <= sqlite_datetime(now),
            )
        )
        item_rows = self._session.execute(item_statement).all()

        order_count = len(orders)
        item_count = sum(row.quantity or 0 for row in item_rows)
        total_spend_gbp = round(sum(order.total_gbp or 0 for order in orders), 2)
        avg_order_value_gbp = (
            round(total_spend_gbp / order_count, 2) if order_count else 0.0
        )

        category_counts: Counter[str] = Counter()
        brand_counts: Counter[str] = Counter()
        size_counts: Counter[str] = Counter()
        for row in item_rows:
            quantity = row.quantity or 0
            if row.category:
                category_counts[row.category] += quantity
            if row.brand:
                brand_counts[row.brand] += quantity
            if row.size:
                size_counts[row.size] += quantity

        channel_counts = Counter(
            order.channel for order in orders if order.channel is not None
        )
        channel_mix = {
            channel: round(count / order_count, 3)
            for channel, count in sorted(channel_counts.items())
        } if order_count else {}

        return PurchaseSummary(
            order_count=order_count,
            item_count=item_count,
            total_spend_gbp=total_spend_gbp,
            avg_order_value_gbp=avg_order_value_gbp,
            top_categories=self._top_values(category_counts),
            top_brands=self._top_values(brand_counts),
            common_sizes=self._top_values(size_counts),
            channel_mix=channel_mix,
        )

    def get_recent_sizes(
        self, customer_id: str, category: Optional[str] = None
    ) -> list[SizeHistoryItem]:
        """Return recent purchased sizes with product context, newest first."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        cutoff = self._cutoff(self.RECENT_SIZE_MONTHS)
        now = self._clock()
        statement = (
            select(
                OrderItemEntity.order_item_id,
                OrderItemEntity.order_id,
                OrderEntity.order_datetime,
                OrderItemEntity.sku,
                ProductEntity.product_name,
                ProductEntity.category,
                ProductEntity.brand,
                OrderItemEntity.size,
                OrderItemEntity.quantity,
            )
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .join(ProductEntity, ProductEntity.sku == OrderItemEntity.sku)
            .where(
                OrderEntity.customer_id == normalized_customer_id,
                func.datetime(OrderEntity.order_datetime) >= sqlite_datetime(cutoff),
                func.datetime(OrderEntity.order_datetime) <= sqlite_datetime(now),
                OrderItemEntity.size.is_not(None),
                OrderItemEntity.size != "",
            )
            .order_by(
                OrderEntity.order_datetime.desc(),
                OrderItemEntity.order_item_id.desc(),
            )
        )
        if category and category.strip():
            statement = statement.where(
                func.lower(ProductEntity.category) == category.strip().casefold()
            )

        return [SizeHistoryItem.model_validate(row) for row in self._session.execute(statement)]

    def _cutoff(self, months: int) -> datetime:
        validate_positive_int(months, "months")
        return months_before(self._clock(), months)

    @classmethod
    def _top_values(cls, counts: Counter[str]) -> list[str]:
        ranked = sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold(), item[0]),
        )
        return [value for value, _ in ranked[: cls.TOP_VALUE_LIMIT]]

    @staticmethod
    def _normalize_customer_id(customer_id: Any) -> str:
        if isinstance(customer_id, str) and customer_id.strip():
            return customer_id.strip()
        raise ValueError("customer_id must be a non-empty string")


__all__ = ["OrderHistoryService"]
