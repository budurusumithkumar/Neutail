"""Persistence adapter for historical product-fit outcomes."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from models.entities import Order, OrderItem, Product, Return
from models.fit import FitCaseRecord
from services._date_utils import sqlite_datetime


class FitEvidenceRepository:
    """Keep fit-history SQL below the service and agent boundaries."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_customer_relevant(
        self,
        customer_id: str,
        *,
        sku: str,
        brand: str,
        category: str,
    ) -> list[FitCaseRecord]:
        statement = self._base_statement().where(
            Order.customer_id == customer_id,
            or_(
                OrderItem.sku == sku,
                func.lower(Product.brand) == brand.casefold(),
                func.lower(Product.category) == category.casefold(),
            ),
        )
        return self._execute(statement)

    def list_all(self) -> list[FitCaseRecord]:
        return self._execute(self._base_statement())

    @staticmethod
    def _base_statement():
        return (
            select(
                OrderItem.order_item_id.label("evidence_id"),
                Order.customer_id,
                Order.order_datetime,
                OrderItem.sku,
                Product.product_name,
                Product.brand,
                Product.category,
                Product.fit_type,
                Product.material,
                OrderItem.size.label("purchased_size"),
                case(
                    (Return.return_id.is_not(None), "RETURNED"),
                    else_="KEPT",
                ).label("outcome"),
                Return.reason_code.label("return_reason"),
                Return.resolution,
                Return.exchange_size,
            )
            .join(Order, Order.order_id == OrderItem.order_id)
            .join(Product, Product.sku == OrderItem.sku)
            .outerjoin(Return, Return.order_item_id == OrderItem.order_item_id)
            .where(
                Order.customer_id.is_not(None),
                OrderItem.sku.is_not(None),
                OrderItem.size.is_not(None),
                OrderItem.size != "",
                func.datetime(Order.order_datetime)
                <= sqlite_datetime(datetime.now(timezone.utc)),
            )
            .order_by(Order.order_datetime.desc(), OrderItem.order_item_id.desc())
        )

    def _execute(self, statement) -> list[FitCaseRecord]:
        return [
            FitCaseRecord.model_validate(row)
            for row in self._session.execute(statement).mappings()
        ]


__all__ = ["FitEvidenceRepository"]
