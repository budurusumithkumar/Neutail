"""Factual product catalogue lookup and structured search service."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import ColumnElement, func, literal, or_, select
from sqlalchemy.orm import Session

from models.dto import Product, ProductSearchCriteria, ProductSearchResult
from models.entities import Product as ProductEntity


class ProductNotFoundError(LookupError):
    """Raised when a requested SKU does not exist."""

    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"Product '{sku}' was not found")


class ProductCatalogService:
    """Read product facts without applying personalized ranking.

    A SQLAlchemy session is injected by the API or tool-call boundary. Search
    results use SKU order solely to make the limited result set deterministic;
    the Discovery Agent remains responsible for personalized ranking.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

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

        filters = self._build_filters(criteria)
        total_statement = select(func.count()).select_from(ProductEntity)
        products_statement = select(ProductEntity).order_by(ProductEntity.sku)

        if filters:
            total_statement = total_statement.where(*filters)
            products_statement = products_statement.where(*filters)

        total_matches = int(self._session.scalar(total_statement) or 0)
        entities = self._session.scalars(
            products_statement.limit(criteria.limit)
        ).all()

        return ProductSearchResult(
            criteria=criteria,
            total_matches=total_matches,
            products=[Product.model_validate(entity) for entity in entities],
        )

    def get_product(self, sku: str) -> Product:
        """Fetch one product by SKU, regardless of its active status.

        Raises:
            ValueError: If ``sku`` is empty.
            ProductNotFoundError: If the SKU does not exist.
        """

        normalized_sku = self._normalize_sku(sku, required=True)
        entity = self._session.get(ProductEntity, normalized_sku)
        if entity is None:
            raise ProductNotFoundError(normalized_sku)
        return Product.model_validate(entity)

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

        statement = select(ProductEntity).where(ProductEntity.sku.in_(ordered_skus))
        entities = self._session.scalars(statement).all()
        entities_by_sku = {entity.sku: entity for entity in entities}

        return [
            Product.model_validate(entities_by_sku[sku])
            for sku in ordered_skus
            if sku in entities_by_sku
        ]

    def is_active(self, sku: str) -> bool:
        """Return whether a SKU exists and is active."""

        normalized_sku = self._normalize_sku(sku, required=False)
        if normalized_sku is None:
            return False

        statement = select(ProductEntity.active).where(
            ProductEntity.sku == normalized_sku
        )
        return bool(self._session.scalar(statement))

    @classmethod
    def _build_filters(
        cls, criteria: ProductSearchCriteria
    ) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = [
            ProductEntity.active.is_(criteria.active)
        ]

        scalar_filters = (
            (ProductEntity.gender, criteria.gender),
            (ProductEntity.category, criteria.category),
            (ProductEntity.subcategory, criteria.subcategory),
            (ProductEntity.occasion, criteria.occasion),
            (ProductEntity.brand_tier, criteria.brand_tier),
        )
        for column, value in scalar_filters:
            if value:
                filters.append(func.lower(column) == value.casefold())

        colors = cls._normalized_values(criteria.colors)
        if colors:
            filters.append(func.lower(ProductEntity.color).in_(colors))

        styles = cls._normalized_values(criteria.styles)
        if styles:
            filters.append(func.lower(ProductEntity.style).in_(styles))

        if criteria.min_price is not None:
            filters.append(ProductEntity.current_price_gbp >= criteria.min_price)
        if criteria.max_price is not None:
            filters.append(ProductEntity.current_price_gbp <= criteria.max_price)

        sizes = cls._normalized_values(criteria.sizes)
        if sizes:
            delimited_sizes = (
                literal("|")
                + func.coalesce(ProductEntity.sizes, "")
                + literal("|")
            )
            filters.append(
                or_(
                    *(
                        delimited_sizes.contains(f"|{size}|", autoescape=True)
                        for size in sizes
                    )
                )
            )

        return filters

    @staticmethod
    def _normalized_values(values: Iterable[str]) -> list[str]:
        """Case-normalize and deduplicate non-empty structured filter values."""

        normalized_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            normalized_value = value.strip().casefold()
            if normalized_value and normalized_value not in seen:
                seen.add(normalized_value)
                normalized_values.append(normalized_value)
        return normalized_values

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
