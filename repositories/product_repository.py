"""SQLAlchemy persistence adapter for product catalogue reads."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

from sqlalchemy import ColumnElement, func, literal, or_, select
from sqlalchemy.orm import Session

from models.dto import Product, ProductSearchCriteria
from models.entities import Product as ProductEntity


class ProductRepository:
    """Keep SQLAlchemy catalogue queries below the domain-service boundary."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def search(
        self,
        criteria: ProductSearchCriteria,
    ) -> tuple[int, list[Product]]:
        filters = self._build_filters(criteria)
        total_statement = select(func.count()).select_from(ProductEntity)
        products_statement = select(ProductEntity).order_by(ProductEntity.sku)
        if filters:
            total_statement = total_statement.where(*filters)
            products_statement = products_statement.where(*filters)

        total = int(self._session.scalar(total_statement) or 0)
        entities = self._session.scalars(
            products_statement.limit(criteria.limit)
        ).all()
        return total, [Product.model_validate(entity) for entity in entities]

    def get(self, sku: str) -> Optional[Product]:
        entity = self._session.get(ProductEntity, sku)
        return Product.model_validate(entity) if entity is not None else None

    def get_many(self, skus: list[str]) -> list[Product]:
        if not skus:
            return []
        statement = select(ProductEntity).where(ProductEntity.sku.in_(skus))
        entities = self._session.scalars(statement).all()
        by_sku = {entity.sku: entity for entity in entities}
        return [
            Product.model_validate(by_sku[sku])
            for sku in skus
            if sku in by_sku
        ]

    def list_active(self) -> list[Product]:
        statement = (
            select(ProductEntity)
            .where(ProductEntity.active.is_(True))
            .order_by(ProductEntity.sku)
        )
        return [
            Product.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def is_active(self, sku: str) -> bool:
        statement = select(ProductEntity.active).where(ProductEntity.sku == sku)
        return bool(self._session.scalar(statement))

    @classmethod
    def _build_filters(
        cls,
        criteria: ProductSearchCriteria,
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
            delimited_sizes = func.lower(
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
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, str):
                continue
            item = value.strip().casefold()
            if item and item not in seen:
                seen.add(item)
                normalized.append(item)
        return normalized


__all__ = ["ProductRepository"]
