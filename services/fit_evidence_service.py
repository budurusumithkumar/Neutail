"""Build hierarchical, normalized fit evidence from authoritative history."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from models.dto import BrandSizeAdjustment
from models.fit import (
    FitCaseRecord,
    FitEvidence,
    FitPurchaseEvidence,
    FitReturnEvidence,
    FitReturnReason,
)
from repositories.fit_evidence_repository import FitEvidenceRepository
from services.fit_profile_service import FitProfileNotFoundError, FitProfileService
from services.product_catalog_service import ProductCatalogService
from services.return_history_service import ReturnHistoryService


_RETURN_REASON_MAP = {
    "SIZE_TOO_SMALL": FitReturnReason.TOO_SMALL,
    "TOO_SMALL": FitReturnReason.TOO_SMALL,
    "SIZE_TOO_LARGE": FitReturnReason.TOO_LARGE,
    "TOO_LARGE": FitReturnReason.TOO_LARGE,
    "TOO_SHORT": FitReturnReason.TOO_SHORT,
    "TOO_LONG": FitReturnReason.TOO_LONG,
    "TIGHT_WAIST": FitReturnReason.TIGHT_WAIST,
    "WAIST_TIGHT": FitReturnReason.TIGHT_WAIST,
    "TIGHT_CHEST": FitReturnReason.TIGHT_CHEST,
    "FIT_NOT_AS_EXPECTED": FitReturnReason.POOR_FIT,
    "POOR_FIT": FitReturnReason.POOR_FIT,
    "NOT_AS_EXPECTED": FitReturnReason.NOT_AS_EXPECTED,
}


def normalize_fit_return_reason(value: Optional[str]) -> FitReturnReason:
    if not value:
        return FitReturnReason.NON_FIT_REASON
    return _RETURN_REASON_MAP.get(
        value.strip().upper().replace(" ", "_"),
        FitReturnReason.NON_FIT_REASON,
    )


class FitEvidenceService:
    """Combine exact, brand, category, and return evidence without deciding fit."""

    def __init__(
        self,
        session: Session,
        *,
        repository: FitEvidenceRepository | None = None,
    ) -> None:
        self._session = session
        self._repository = repository or FitEvidenceRepository(session)

    def build(
        self,
        customer_id: str,
        sku: str,
        requested_size: Optional[str] = None,
    ) -> FitEvidence:
        del requested_size  # The evidence remains factual; the calculator uses size.
        customer_id = self._required(customer_id, "customer_id")
        sku = self._required(sku, "sku")
        product = ProductCatalogService(self._session).get_product(sku)
        brand = product.brand or "UNKNOWN"
        category = product.category or "UNKNOWN"

        fit_service = FitProfileService(self._session)
        try:
            profile = fit_service.get_fit_profile(customer_id)
            adjustment = fit_service.get_brand_adjustment(customer_id, brand)
            usual_size = profile.usual_size
            profile_available = True
        except FitProfileNotFoundError:
            adjustment = BrandSizeAdjustment(
                brand=brand,
                adjustment="0",
                evidence_count=0,
            )
            usual_size = None
            profile_available = False

        exact: list[FitPurchaseEvidence] = []
        same_brand: list[FitPurchaseEvidence] = []
        same_category: list[FitPurchaseEvidence] = []
        fit_returns: list[FitReturnEvidence] = []
        cases = self._repository.list_customer_relevant(
            customer_id,
            sku=sku,
            brand=brand,
            category=category,
        )
        for case in cases:
            relevance = self._relevance(case, sku, brand, category)
            reason = normalize_fit_return_reason(case.return_reason)
            purchase = FitPurchaseEvidence(
                evidence_id=case.evidence_id,
                sku=case.sku,
                brand=case.brand,
                category=case.category,
                fit_type=case.fit_type,
                purchased_size=case.purchased_size,
                outcome=case.outcome,
                return_reason=reason if case.outcome == "RETURNED" else None,
                exchange_size=case.exchange_size or None,
                relevance=relevance,
            )
            if relevance == "SAME_SKU":
                exact.append(purchase)
            elif relevance == "SAME_BRAND":
                same_brand.append(purchase)
            else:
                same_category.append(purchase)
            if case.outcome == "RETURNED" and reason is not FitReturnReason.NON_FIT_REASON:
                fit_returns.append(
                    FitReturnEvidence(
                        evidence_id=case.evidence_id,
                        sku=case.sku,
                        purchased_size=case.purchased_size,
                        reason=reason,
                        exchange_size=case.exchange_size or None,
                        relevance=relevance,
                    )
                )

        summary = ReturnHistoryService(self._session).get_return_summary(customer_id)
        strength = self._evidence_strength(
            exact_count=len(exact),
            brand_count=len(same_brand),
            category_count=len(same_category),
            fit_return_count=len(fit_returns),
            profile_available=profile_available,
        )
        return FitEvidence(
            customer_id=customer_id,
            sku=sku,
            usual_size=usual_size,
            same_sku_purchases=exact,
            same_brand_purchases=same_brand,
            same_category_purchases=same_category,
            size_related_returns=fit_returns,
            brand_adjustment=adjustment,
            historical_return_rate=summary.return_rate,
            evidence_strength=strength,
        )

    @staticmethod
    def _relevance(
        case: FitCaseRecord,
        sku: str,
        brand: str,
        category: str,
    ) -> str:
        if case.sku == sku:
            return "SAME_SKU"
        if case.brand and case.brand.casefold() == brand.casefold():
            return "SAME_BRAND"
        return "SAME_CATEGORY"

    @staticmethod
    def _evidence_strength(
        *,
        exact_count: int,
        brand_count: int,
        category_count: int,
        fit_return_count: int,
        profile_available: bool,
    ) -> float:
        score = 0.15 if profile_available else 0.0
        score += min(exact_count, 2) * 0.25
        score += min(brand_count, 3) * 0.07
        score += min(category_count, 3) * 0.03
        score += min(fit_return_count, 2) * 0.025
        return round(min(score, 1.0), 3)

    @staticmethod
    def _required(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()


__all__ = ["FitEvidenceService", "normalize_fit_return_reason"]
