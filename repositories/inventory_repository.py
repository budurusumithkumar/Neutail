"""SQLAlchemy persistence adapter for inventory reads."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.dto import InventoryRecord
from models.entities import Inventory


class InventoryRepository:
    """Keep inventory SQL below the deterministic inventory service."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_sku(self, sku: str) -> list[InventoryRecord]:
        statement = (
            select(Inventory)
            .where(Inventory.sku == sku)
            .order_by(Inventory.location_id)
        )
        return [
            InventoryRecord.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_at_location(
        self,
        sku: str,
        location_id: str,
    ) -> Optional[InventoryRecord]:
        entity = self._session.get(Inventory, (sku, location_id))
        return (
            InventoryRecord.model_validate(entity)
            if entity is not None
            else None
        )

    def total_available(self, sku: str) -> int:
        statement = select(
            func.coalesce(func.sum(Inventory.available_qty), 0)
        ).where(Inventory.sku == sku)
        return int(self._session.scalar(statement) or 0)

    def available_skus(self, skus: list[str]) -> dict[str, bool]:
        availability = {sku: False for sku in skus}
        if not skus:
            return availability
        statement = (
            select(
                Inventory.sku,
                func.coalesce(func.sum(Inventory.available_qty), 0),
            )
            .where(Inventory.sku.in_(skus))
            .group_by(Inventory.sku)
        )
        for sku, total_available in self._session.execute(statement):
            availability[sku] = int(total_available or 0) >= 1
        return availability


__all__ = ["InventoryRepository"]
