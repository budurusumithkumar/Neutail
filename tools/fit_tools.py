"""FastMCP adapters for deterministic product-fit evidence."""

from __future__ import annotations

from typing import Optional

from models.dto import BrandSizeAdjustment, FitEvidence, FitProfile, FitRisk
from services.fit_profile_service import FitProfileService
from tools.contracts import tool_contract
from tools.runtime import get_runtime


@tool_contract(
    name="fit_get_profile",
    title="Get Fit Profile",
    description="Fetch the customer's seeded size, fit, confidence, adjustment, and risk facts.",
    capability="fit.profile.get",
)
def fit_get_profile(customer_id: str) -> FitProfile:
    with get_runtime().session() as session:
        return FitProfileService(session).get_fit_profile(customer_id)


@tool_contract(
    name="fit_build_evidence",
    title="Build Fit Evidence",
    description="Combine fit profile, product characteristics, purchases, and returns into explainable evidence.",
    capability="fit.evidence.build",
)
def fit_build_evidence(
    customer_id: str,
    sku: str,
    requested_size: Optional[str] = None,
) -> FitEvidence:
    with get_runtime().session() as session:
        return FitProfileService(session).build_fit_evidence(
            customer_id, sku, requested_size
        )


@tool_contract(
    name="fit_get_brand_adjustment",
    title="Get Brand Size Adjustment",
    description="Return a known customer size adjustment and evidence count for a brand.",
    capability="fit.brand.adjustment",
)
def fit_get_brand_adjustment(
    customer_id: str, brand: str
) -> BrandSizeAdjustment:
    with get_runtime().session() as session:
        return FitProfileService(session).get_brand_adjustment(customer_id, brand)


@tool_contract(
    name="fit_calculate_risk",
    title="Calculate Fit Risk",
    description="Calculate a transparent deterministic fit-risk score, band, and reason codes.",
    capability="fit.risk.calculate",
)
def fit_calculate_risk(customer_id: str, sku: str) -> FitRisk:
    with get_runtime().session() as session:
        return FitProfileService(session).calculate_fit_risk(customer_id, sku)


FIT_TOOLS = (
    fit_get_profile,
    fit_build_evidence,
    fit_get_brand_adjustment,
    fit_calculate_risk,
)


__all__ = ["FIT_TOOLS"]
