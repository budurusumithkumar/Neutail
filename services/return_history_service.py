"""Customer return evidence and deterministic return metrics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.dto import ReturnRecord, ReturnSummary
from models.entities import Order as OrderEntity
from models.entities import OrderItem as OrderItemEntity
from models.entities import Product as ProductEntity
from models.entities import Return as ReturnEntity
from services._date_utils import (
    months_before,
    sqlite_datetime,
    utc_now,
    validate_positive_int,
)


class ReturnHistoryService:
    """Expose explainable return evidence without deciding product fit."""

    SIZE_RELATED_REASON_CODES = frozenset(
        {"SIZE_TOO_SMALL", "SIZE_TOO_LARGE", "FIT_NOT_AS_EXPECTED"}
    )
    TOP_REASON_LIMIT = 3

    def __init__(
        self,
        session: Session,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now

    def get_returns(
        self, customer_id: str, months: int = 12
    ) -> list[ReturnRecord]:
        """Return recent customer returns, newest first."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        cutoff = self._cutoff(months).date()
        today = self._clock().date()
        statement = (
            select(ReturnEntity)
            .where(
                ReturnEntity.customer_id == normalized_customer_id,
                ReturnEntity.return_date >= cutoff,
                ReturnEntity.return_date <= today,
            )
            .order_by(ReturnEntity.return_date.desc(), ReturnEntity.return_id.desc())
        )
        return [
            ReturnRecord.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_return_summary(
        self, customer_id: str, months: int = 12
    ) -> ReturnSummary:
        """Return metrics for items purchased during the requested window."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        cutoff = self._cutoff(months)
        now = self._clock()

        purchased_item_ids = (
            select(OrderItemEntity.order_item_id)
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .where(
                OrderEntity.customer_id == normalized_customer_id,
                func.datetime(OrderEntity.order_datetime) >= sqlite_datetime(cutoff),
                func.datetime(OrderEntity.order_datetime) <= sqlite_datetime(now),
            )
        )
        purchased_items = int(
            self._session.scalar(
                select(func.count()).select_from(purchased_item_ids.subquery())
            )
            or 0
        )

        returns_statement = select(ReturnEntity).where(
            ReturnEntity.customer_id == normalized_customer_id,
            ReturnEntity.order_item_id.in_(purchased_item_ids),
            ReturnEntity.return_date <= now.date(),
        )
        returns = self._session.scalars(returns_statement).all()
        returned_items = len({item.order_item_id for item in returns})
        return_rate = (
            round(returned_items / purchased_items, 3) if purchased_items else 0.0
        )

        reason_counts = Counter(
            item.reason_code for item in returns if item.reason_code is not None
        )
        top_reason_codes = [
            reason
            for reason, _ in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0].casefold(), item[0]),
            )[: self.TOP_REASON_LIMIT]
        ]
        size_related_return_count = sum(
            1 for item in returns if item.reason_code in self.SIZE_RELATED_REASON_CODES
        )
        exchange_count = sum(1 for item in returns if item.resolution == "EXCHANGE")

        return ReturnSummary(
            returned_items=returned_items,
            purchased_items=purchased_items,
            return_rate=return_rate,
            top_reason_codes=top_reason_codes,
            size_related_return_count=size_related_return_count,
            exchange_count=exchange_count,
        )

    def get_product_returns(
        self,
        customer_id: str,
        sku: Optional[str] = None,
        brand: Optional[str] = None,
    ) -> list[ReturnRecord]:
        """Return customer evidence for an optional SKU and/or brand."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        statement = select(ReturnEntity).where(
            ReturnEntity.customer_id == normalized_customer_id,
            ReturnEntity.return_date <= self._clock().date(),
        )

        if sku and sku.strip():
            statement = statement.where(ReturnEntity.sku == sku.strip())
        if brand and brand.strip():
            statement = statement.join(
                ProductEntity, ProductEntity.sku == ReturnEntity.sku
            ).where(func.lower(ProductEntity.brand) == brand.strip().casefold())

        statement = statement.order_by(
            ReturnEntity.return_date.desc(), ReturnEntity.return_id.desc()
        )
        return [
            ReturnRecord.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_size_related_returns(self, customer_id: str) -> list[ReturnRecord]:
        """Return explicit size and fit-related evidence, newest first."""

        normalized_customer_id = self._normalize_customer_id(customer_id)
        statement = (
            select(ReturnEntity)
            .where(
                ReturnEntity.customer_id == normalized_customer_id,
                ReturnEntity.reason_code.in_(self.SIZE_RELATED_REASON_CODES),
                ReturnEntity.return_date <= self._clock().date(),
            )
            .order_by(ReturnEntity.return_date.desc(), ReturnEntity.return_id.desc())
        )
        return [
            ReturnRecord.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def _cutoff(self, months: int) -> datetime:
        validate_positive_int(months, "months")
        return months_before(self._clock(), months)

    @staticmethod
    def _normalize_customer_id(customer_id: Any) -> str:
        if isinstance(customer_id, str) and customer_id.strip():
            return customer_id.strip()
        raise ValueError("customer_id must be a non-empty string")


__all__ = ["ReturnHistoryService"]
