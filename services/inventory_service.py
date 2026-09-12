"""Authoritative, deterministic product inventory service."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.dto import InventoryRecord, InventorySummary
from models.entities import Inventory


class InventoryNotFoundError(LookupError):
    """Raised when no inventory records exist for a SKU."""

    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"Inventory for product '{sku}' was not found")


class InventoryLocationNotFoundError(LookupError):
    """Raised when a SKU has no inventory record at a requested location."""

    def __init__(self, sku: str, location_id: str) -> None:
        self.sku = sku
        self.location_id = location_id
        super().__init__(
            f"Inventory for product '{sku}' at location '{location_id}' was not found"
        )


class InventoryService:
    """Read stock availability without mutating inventory balances.

    A SQLAlchemy session is injected by the API or tool-call boundary. All
    aggregate checks use ``available_qty``, which is the database's authoritative
    post-reservation balance.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_inventory(self, sku: str) -> InventorySummary:
        """Return a SKU's balances across all stores and the distribution centre.

        Locations are ordered by ``location_id`` so repeated calls are
        deterministic.

        Raises:
            ValueError: If ``sku`` is empty.
            InventoryNotFoundError: If the SKU has no inventory records.
        """

        normalized_sku = self._normalize_identifier(sku, "sku", required=True)
        statement = (
            select(Inventory)
            .where(Inventory.sku == normalized_sku)
            .order_by(Inventory.location_id)
        )
        entities = self._session.scalars(statement).all()
        if not entities:
            raise InventoryNotFoundError(normalized_sku)

        locations = [InventoryRecord.model_validate(entity) for entity in entities]
        total_available_qty = sum(
            location.available_qty or 0 for location in locations
        )
        return InventorySummary(
            sku=normalized_sku,
            total_available_qty=total_available_qty,
            available=total_available_qty > 0,
            locations=locations,
        )

    def get_inventory_by_location(
        self, sku: str, location_id: str
    ) -> InventoryRecord:
        """Return a SKU's inventory balance at one location.

        Raises:
            ValueError: If ``sku`` or ``location_id`` is empty.
            InventoryLocationNotFoundError: If the balance does not exist.
        """

        normalized_sku = self._normalize_identifier(sku, "sku", required=True)
        normalized_location_id = self._normalize_identifier(
            location_id, "location_id", required=True
        )
        entity = self._session.get(
            Inventory,
            (normalized_sku, normalized_location_id),
        )
        if entity is None:
            raise InventoryLocationNotFoundError(
                normalized_sku, normalized_location_id
            )
        return InventoryRecord.model_validate(entity)

    def is_available(self, sku: str, min_qty: int = 1) -> bool:
        """Return whether total availability across locations meets ``min_qty``."""

        if isinstance(min_qty, bool) or not isinstance(min_qty, int) or min_qty < 1:
            raise ValueError("min_qty must be a positive integer")

        normalized_sku = self._normalize_identifier(
            sku, "sku", required=False
        )
        if normalized_sku is None:
            return False

        statement = select(func.coalesce(func.sum(Inventory.available_qty), 0)).where(
            Inventory.sku == normalized_sku
        )
        total_available_qty = int(self._session.scalar(statement) or 0)
        return total_available_qty >= min_qty

    def get_available_skus(self, skus: Iterable[str]) -> dict[str, bool]:
        """Check many SKUs with one grouped query.

        Input order is preserved in the returned dictionary. Duplicate and blank
        identifiers are ignored; unknown SKUs are retained with a ``False`` value.
        """

        if isinstance(skus, (str, bytes)):
            raise ValueError("skus must be an iterable of SKU strings")

        ordered_skus: list[str] = []
        seen: set[str] = set()
        for sku in skus:
            normalized_sku = self._normalize_identifier(
                sku, "sku", required=False
            )
            if normalized_sku is not None and normalized_sku not in seen:
                seen.add(normalized_sku)
                ordered_skus.append(normalized_sku)

        availability = {sku: False for sku in ordered_skus}
        if not ordered_skus:
            return availability

        statement = (
            select(
                Inventory.sku,
                func.coalesce(func.sum(Inventory.available_qty), 0),
            )
            .where(Inventory.sku.in_(ordered_skus))
            .group_by(Inventory.sku)
        )
        for sku, total_available_qty in self._session.execute(statement):
            availability[sku] = int(total_available_qty or 0) >= 1
        return availability

    @staticmethod
    def _normalize_identifier(
        value: Any, field_name: str, *, required: bool
    ) -> str | None:
        if isinstance(value, str):
            normalized_value = value.strip()
            if normalized_value:
                return normalized_value
        if required:
            raise ValueError(f"{field_name} must be a non-empty string")
        return None


__all__ = [
    "InventoryLocationNotFoundError",
    "InventoryNotFoundError",
    "InventoryService",
]
