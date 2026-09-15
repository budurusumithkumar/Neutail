"""Factual product catalogue lookup and structured search service."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from models.dto import Product, ProductSearchCriteria, ProductSearchResult
from repositories.product_repository import ProductRepository


class ProductNotFoundError(LookupError):
    """Raised when a requested SKU does not exist."""

    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"Product '{sku}' was not found")


class ProductCatalogService:
    """Read product facts without applying personalized ranking.

    A repository, or a SQLAlchemy session used to construct one, is injected by
    the tool-call boundary. Search results use SKU order solely to make the
    limited result set deterministic; Discovery owns personalized ranking.
    """

    def __init__(
        self,
        session: Session | None = None,
        *,
        repository: ProductRepository | None = None,
    ) -> None:
        if repository is None and session is None:
            raise ValueError("session or repository is required")
        if repository is not None:
            self._repository = repository
        else:
            assert session is not None
            self._repository = ProductRepository(session)

    def search_products(
        self, criteria: ProductSearchCriteria
    ) -> ProductSearchResult:
        """Search catalogue products using structured, factual filters.

        Values are matched case-insensitively. Multiple colors, styles, or
        sizes use OR semantics within that field and AND semantics across
        different fields. ``total_matches`` is calculated before ``limit`` is
        applied.
        """

        if not isinstance(criteria, ProductSearchCriteria):
            criteria = ProductSearchCriteria.model_validate(criteria)

        total_matches, products = self._repository.search(criteria)

        return ProductSearchResult(
            criteria=criteria,
            total_matches=total_matches,
            products=products,
        )

    def get_product(self, sku: str) -> Product:
        """Fetch one product by SKU, regardless of its active status.

        Raises:
            ValueError: If ``sku`` is empty.
            ProductNotFoundError: If the SKU does not exist.
        """

        normalized_sku = self._normalize_sku(sku, required=True)
        product = self._repository.get(normalized_sku)
        if product is None:
            raise ProductNotFoundError(normalized_sku)
        return product

    def get_products(self, skus: Iterable[str]) -> list[Product]:
        """Fetch many products in one query while preserving requested order.

        Duplicate and blank SKUs are ignored. Unknown SKUs are omitted so a
        stale recommendation candidate cannot invalidate the entire batch.
        """

        if isinstance(skus, (str, bytes)):
            raise ValueError("skus must be an iterable of SKU strings")

        ordered_skus: list[str] = []
        seen: set[str] = set()
        for sku in skus:
            normalized_sku = self._normalize_sku(sku, required=False)
            if normalized_sku is not None and normalized_sku not in seen:
                seen.add(normalized_sku)
                ordered_skus.append(normalized_sku)

        if not ordered_skus:
            return []

        return self._repository.get_many(ordered_skus)

    def is_active(self, sku: str) -> bool:
        """Return whether a SKU exists and is active."""

        normalized_sku = self._normalize_sku(sku, required=False)
        if normalized_sku is None:
            return False

        return self._repository.is_active(normalized_sku)

    @staticmethod
    def _normalize_sku(sku: Any, *, required: bool) -> str | None:
        if isinstance(sku, str):
            normalized_sku = sku.strip()
            if normalized_sku:
                return normalized_sku
        if required:
            raise ValueError("sku must be a non-empty string")
        return None


__all__ = ["ProductCatalogService", "ProductNotFoundError"]
