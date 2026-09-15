"""FastMCP adapters for catalogue and inventory capabilities."""

from __future__ import annotations

from models.dto import (
    InventoryRecord,
    InventorySummary,
    Product,
    ProductCandidate,
    ProductSearchInput,
    SemanticProductMatch,
    SemanticProductSearchInput,
)
from services.inventory_service import InventoryService
from services.product_catalog_service import ProductCatalogService
from services.product_retrieval_service import ProductRetrievalService
from tools.contracts import tool_contract
from tools.runtime import get_runtime


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


@tool_contract(
    name="search_products",
    title="Search Products for Discovery",
    description="Retrieve factual active catalogue candidates using exact structured constraints; no personalization is applied.",
    capability="discovery.catalog.search",
)
def search_products(criteria: ProductSearchInput) -> list[ProductCandidate]:
    with get_runtime().session() as session:
        result = ProductCatalogService(session).search_products(
            criteria.to_catalog_criteria()
        )
        return [ProductCandidate.model_validate(item) for item in result.products]


@tool_contract(
    name="get_product_details",
    title="Get Discovery Product Details",
    description="Fetch authoritative product facts for one semantic retrieval SKU.",
    capability="discovery.catalog.details",
)
def get_product_details(sku: str) -> Product:
    with get_runtime().session() as session:
        return ProductCatalogService(session).get_product(sku)


@tool_contract(
    name="check_inventory",
    title="Check Discovery Candidate Inventory",
    description="Bulk-check live authoritative availability for discovery candidates.",
    capability="discovery.inventory.bulk_available",
)
def check_inventory(skus: list[str]) -> dict[str, bool]:
    with get_runtime().session() as session:
        return InventoryService(session).get_available_skus(skus)


@tool_contract(
    name="semantic_product_search",
    title="Semantic Product Search",
    description="Retrieve active product SKUs by semantic similarity over stable catalogue documents.",
    capability="discovery.vector.search",
)
def semantic_product_search(
    request: SemanticProductSearchInput,
) -> list[SemanticProductMatch]:
    with get_runtime().session() as session:
        return ProductRetrievalService(session).semantic_search(request)


PRODUCT_TOOLS = (
    product_get,
    inventory_get,
    inventory_get_by_location,
    inventory_is_available,
    inventory_get_available_skus,
    search_products,
    get_product_details,
    check_inventory,
    semantic_product_search,
)


__all__ = ["PRODUCT_TOOLS"]
