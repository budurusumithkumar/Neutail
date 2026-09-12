"""FastMCP adapters for catalogue and inventory capabilities."""

from __future__ import annotations

from models.dto import (
    InventoryRecord,
    InventorySummary,
    Product,
    ProductSearchCriteria,
    ProductSearchResult,
)
from services.inventory_service import InventoryService
from services.product_catalog_service import ProductCatalogService
from tools.contracts import tool_contract
from tools.runtime import get_runtime


@tool_contract(
    name="product_search",
    title="Search Products",
    description="Search active catalogue facts with structured filters; results are not personalized or ranked.",
    capability="product.catalog.search",
)
def product_search(criteria: ProductSearchCriteria) -> ProductSearchResult:
    with get_runtime().session() as session:
        return ProductCatalogService(session).search_products(criteria)


@tool_contract(
    name="product_get",
    title="Get Product",
    description="Fetch factual catalogue data for one SKU, regardless of active status.",
    capability="product.catalog.get",
)
def product_get(sku: str) -> Product:
    with get_runtime().session() as session:
        return ProductCatalogService(session).get_product(sku)


@tool_contract(
    name="product_get_many",
    title="Get Products",
    description="Fetch many SKUs in one query while preserving requested order and omitting unknown SKUs.",
    capability="product.catalog.bulk_get",
)
def product_get_many(skus: list[str]) -> list[Product]:
    with get_runtime().session() as session:
        return ProductCatalogService(session).get_products(skus)


@tool_contract(
    name="product_is_active",
    title="Check Product Active",
    description="Confirm that a SKU exists and is active for recommendation consideration.",
    capability="product.catalog.active",
)
def product_is_active(sku: str) -> bool:
    with get_runtime().session() as session:
        return ProductCatalogService(session).is_active(sku)


@tool_contract(
    name="inventory_get",
    title="Get Inventory",
    description="Return authoritative aggregate availability and all store/DC balances for a SKU.",
    capability="product.inventory.summary",
)
def inventory_get(sku: str) -> InventorySummary:
    with get_runtime().session() as session:
        return InventoryService(session).get_inventory(sku)


@tool_contract(
    name="inventory_get_by_location",
    title="Get Location Inventory",
    description="Return the authoritative inventory balance for one SKU and location.",
    capability="product.inventory.location",
)
def inventory_get_by_location(sku: str, location_id: str) -> InventoryRecord:
    with get_runtime().session() as session:
        return InventoryService(session).get_inventory_by_location(sku, location_id)


@tool_contract(
    name="inventory_is_available",
    title="Check Product Availability",
    description="Check whether summed SKU availability across locations meets a minimum quantity.",
    capability="product.inventory.available",
)
def inventory_is_available(sku: str, min_qty: int = 1) -> bool:
    with get_runtime().session() as session:
        return InventoryService(session).is_available(sku, min_qty)


@tool_contract(
    name="inventory_get_available_skus",
    title="Bulk Check Product Availability",
    description="Check availability for ranked SKU candidates using one grouped database query.",
    capability="product.inventory.bulk_available",
)
def inventory_get_available_skus(skus: list[str]) -> dict[str, bool]:
    with get_runtime().session() as session:
        return InventoryService(session).get_available_skus(skus)


PRODUCT_TOOLS = (
    product_search,
    product_get,
    product_get_many,
    product_is_active,
    inventory_get,
    inventory_get_by_location,
    inventory_is_available,
    inventory_get_available_skus,
)


__all__ = ["PRODUCT_TOOLS"]
