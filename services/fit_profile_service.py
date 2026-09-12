"""Fit-specific facts and transparent deterministic evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from models.dto import BrandSizeAdjustment, FitEvidence, FitProfile, FitRisk, Product
from models.entities import FitProfile as FitProfileEntity
from models.entities import Order as OrderEntity
from models.entities import OrderItem as OrderItemEntity
from models.entities import Product as ProductEntity
from models.entities import Return as ReturnEntity
from services._date_utils import sqlite_datetime, utc_now


class FitProfileNotFoundError(LookupError):
    """Raised when a customer has no stored fit profile."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Fit profile for customer '{customer_id}' was not found")


class FitProductNotFoundError(LookupError):
    """Raised when fit evidence is requested for an unknown SKU."""

    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"Product '{sku}' was not found")


class FitProfileService:
    """Build reusable fit facts while leaving conversational advice to the agent."""

    SIZE_RELATED_REASON_CODES = frozenset(
        {"SIZE_TOO_SMALL", "SIZE_TOO_LARGE", "FIT_NOT_AS_EXPECTED"}
    )
    MAX_PRIOR_SIZES = 10
    MAX_SIZE_CANDIDATES = 3

    def __init__(
        self,
        session: Session,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now

    def get_fit_profile(self, customer_id: str) -> FitProfile:
        """Fetch a customer's seeded fit profile."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        entity = self._session.get(FitProfileEntity, normalized_customer_id)
        if entity is None:
            raise FitProfileNotFoundError(normalized_customer_id)
        return FitProfile.model_validate(entity)

    def build_fit_evidence(
        self,
        customer_id: str,
        sku: str,
        requested_size: Optional[str] = None,
    ) -> FitEvidence:
        """Combine profile, product, purchase, and return evidence."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        normalized_sku = self._normalize_identifier(sku, "sku")
        profile = self.get_fit_profile(normalized_customer_id)
        product_entity = self._get_product_entity(normalized_sku)
        product = Product.model_validate(product_entity)
        brand_adjustment = self.get_brand_adjustment(
            normalized_customer_id, product.brand or "UNKNOWN"
        )

        prior_sizes = self._get_prior_sizes(
            normalized_customer_id, product.category
        )
        relevant_return_reasons = self._get_relevant_return_reasons(
            normalized_customer_id,
            sku=normalized_sku,
            brand=product.brand,
            category=product.category,
        )
        normalized_requested_size = (
            requested_size.strip()
            if isinstance(requested_size, str) and requested_size.strip()
            else None
        )
        suggested_size_candidates = self._suggest_size_candidates(
            available_sizes=product.sizes,
            requested_size=normalized_requested_size,
            usual_size=profile.usual_size,
            prior_sizes=prior_sizes,
            adjustment=brand_adjustment.adjustment,
        )

        confidence_inputs: dict[str, Any] = {
            "size_confidence": profile.size_confidence,
            "profile_return_risk_score": profile.return_risk_score,
            "prior_size_count": len(prior_sizes),
            "relevant_return_reason_count": len(relevant_return_reasons),
            "requested_size_available": (
                normalized_requested_size in product.sizes
                if normalized_requested_size is not None
                else None
            ),
            "brand_adjustment_evidence_count": brand_adjustment.evidence_count,
        }
        return FitEvidence(
            usual_size=profile.usual_size,
            requested_size=normalized_requested_size,
            product_fit_type=product.fit_type,
            preferred_fit=profile.preferred_fit,
            brand_adjustment=brand_adjustment,
            prior_sizes=prior_sizes,
            relevant_return_reasons=relevant_return_reasons,
            suggested_size_candidates=suggested_size_candidates,
            confidence_inputs=confidence_inputs,
        )

    def get_brand_adjustment(
        self, customer_id: str, brand: str
    ) -> BrandSizeAdjustment:
        """Return a known brand adjustment and its purchase evidence count."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        normalized_brand = self._normalize_identifier(brand, "brand")
        profile = self.get_fit_profile(normalized_customer_id)
        adjustments = profile.known_brand_adjustments or {}
        known_adjustment = next(
            (
                (name, value)
                for name, value in adjustments.items()
                if name.casefold() == normalized_brand.casefold()
            ),
            None,
        )
        canonical_brand, adjustment = known_adjustment or (normalized_brand, "0")

        evidence_statement = (
            select(func.count(OrderItemEntity.order_item_id))
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .join(ProductEntity, ProductEntity.sku == OrderItemEntity.sku)
            .where(
                OrderEntity.customer_id == normalized_customer_id,
                func.lower(ProductEntity.brand) == normalized_brand.casefold(),
                func.datetime(OrderEntity.order_datetime)
                <= sqlite_datetime(self._clock()),
            )
        )
        evidence_count = int(self._session.scalar(evidence_statement) or 0)
        return BrandSizeAdjustment(
            brand=canonical_brand,
            adjustment=adjustment,
            evidence_count=evidence_count,
        )

    def calculate_fit_risk(self, customer_id: str, sku: str) -> FitRisk:
        """Calculate transparent profile- and product-specific fit risk."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        normalized_sku = self._normalize_identifier(sku, "sku")
        profile = self.get_fit_profile(normalized_customer_id)
        product = Product.model_validate(self._get_product_entity(normalized_sku))
        brand_adjustment = self.get_brand_adjustment(
            normalized_customer_id, product.brand or "UNKNOWN"
        )
        return_reasons = self._get_relevant_return_reasons(
            normalized_customer_id,
            sku=normalized_sku,
            brand=product.brand,
            category=product.category,
        )

        risk_score = profile.return_risk_score or 0.0
        reason_codes: list[str] = []
        if risk_score >= 0.6:
            reason_codes.append("HIGH_PROFILE_RETURN_RISK")
        elif risk_score >= 0.35:
            reason_codes.append("PROFILE_RETURN_RISK")

        if profile.size_confidence is not None and profile.size_confidence < 0.6:
            risk_score += 0.15
            reason_codes.append("LOW_SIZE_CONFIDENCE")

        if (
            profile.preferred_fit
            and product.fit_type
            and profile.preferred_fit.casefold() != product.fit_type.casefold()
        ):
            risk_score += 0.10
            reason_codes.append("PRODUCT_FIT_DIFFERS_FROM_PREFERENCE")

        if self._parse_adjustment(brand_adjustment.adjustment) != 0:
            risk_score += 0.05
            reason_codes.append("BRAND_SIZE_ADJUSTMENT")

        if return_reasons:
            risk_score += min(len(return_reasons) * 0.05, 0.20)
            reason_codes.append("RELEVANT_SIZE_RETURN_HISTORY")

        risk_score = round(min(max(risk_score, 0.0), 1.0), 3)
        risk_band = "LOW" if risk_score < 0.35 else "MEDIUM"
        if risk_score >= 0.65:
            risk_band = "HIGH"
        if not reason_codes:
            reason_codes.append("NO_ELEVATED_RISK_SIGNALS")

        return FitRisk(
            risk_score=risk_score,
            risk_band=risk_band,
            reason_codes=reason_codes,
        )

    def _get_product_entity(self, sku: str) -> ProductEntity:
        entity = self._session.get(ProductEntity, sku)
        if entity is None:
            raise FitProductNotFoundError(sku)
        return entity

    def _get_prior_sizes(
        self, customer_id: str, category: Optional[str]
    ) -> list[str]:
        statement = (
            select(OrderItemEntity.size)
            .join(OrderEntity, OrderEntity.order_id == OrderItemEntity.order_id)
            .join(ProductEntity, ProductEntity.sku == OrderItemEntity.sku)
            .where(
                OrderEntity.customer_id == customer_id,
                OrderItemEntity.size.is_not(None),
                OrderItemEntity.size != "",
                func.datetime(OrderEntity.order_datetime)
                <= sqlite_datetime(self._clock()),
            )
            .order_by(
                OrderEntity.order_datetime.desc(),
                OrderItemEntity.order_item_id.desc(),
            )
        )
        if category:
            statement = statement.where(
                func.lower(ProductEntity.category) == category.casefold()
            )
        values = self._session.scalars(statement).all()
        return self._unique(values)[: self.MAX_PRIOR_SIZES]

    def _get_relevant_return_reasons(
        self,
        customer_id: str,
        *,
        sku: str,
        brand: Optional[str],
        category: Optional[str],
    ) -> list[str]:
        statement = (
            select(ReturnEntity.reason_code)
            .join(ProductEntity, ProductEntity.sku == ReturnEntity.sku)
            .where(
                ReturnEntity.customer_id == customer_id,
                ReturnEntity.reason_code.in_(self.SIZE_RELATED_REASON_CODES),
                ReturnEntity.return_date <= self._clock().date(),
            )
            .order_by(ReturnEntity.return_date.desc(), ReturnEntity.return_id.desc())
        )
        relevance_filters = [ReturnEntity.sku == sku]
        if brand:
            relevance_filters.append(func.lower(ProductEntity.brand) == brand.casefold())
        if category:
            relevance_filters.append(
                func.lower(ProductEntity.category) == category.casefold()
            )
        statement = statement.where(or_(*relevance_filters))
        return self._unique(self._session.scalars(statement).all())

    @classmethod
    def _suggest_size_candidates(
        cls,
        *,
        available_sizes: list[str],
        requested_size: Optional[str],
        usual_size: Optional[str],
        prior_sizes: list[str],
        adjustment: str,
    ) -> list[str]:
        if not available_sizes:
            return []

        base_size = requested_size or usual_size
        candidates: list[str] = []
        adjustment_steps = cls._parse_adjustment(adjustment)
        if base_size in available_sizes and adjustment_steps:
            base_index = available_sizes.index(base_size)
            adjusted_index = base_index + adjustment_steps
            if 0 <= adjusted_index < len(available_sizes):
                candidates.append(available_sizes[adjusted_index])

        candidates.extend([requested_size, usual_size, *prior_sizes])
        offered_candidates = [
            candidate
            for candidate in candidates
            if candidate is not None and candidate in available_sizes
        ]
        return cls._unique(offered_candidates)[: cls.MAX_SIZE_CANDIDATES]

    @staticmethod
    def _parse_adjustment(adjustment: str) -> int:
        try:
            return int(adjustment)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _unique(values: list[Optional[str]]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value and value not in seen:
                seen.add(value)
                unique_values.append(value)
        return unique_values

    @staticmethod
    def _normalize_identifier(value: Any, field_name: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError(f"{field_name} must be a non-empty string")


__all__ = [
    "FitProductNotFoundError",
    "FitProfileNotFoundError",
    "FitProfileService",
]
