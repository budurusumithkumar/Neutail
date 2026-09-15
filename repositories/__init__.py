"""Persistence adapters used by Neu.Tail domain services."""

from repositories.inventory_repository import InventoryRepository
from repositories.product_repository import ProductRepository

__all__ = ["InventoryRepository", "ProductRepository"]
