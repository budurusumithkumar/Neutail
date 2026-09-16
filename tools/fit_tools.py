"""FastMCP adapters for deterministic product-fit evidence."""

from __future__ import annotations

from typing import Optional

from models.dto import FitProfile
from models.fit import FitEvidence, SimilarFitCase, SimilarFitCaseRequest
from services.fit_evidence_service import FitEvidenceService
from services.fit_profile_service import FitProfileService
from services.fit_retrieval_service import FitRetrievalService
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
        return FitEvidenceService(session).build(customer_id, sku, requested_size)


@tool_contract(
    name="fit_retrieve_similar_cases",
    title="Retrieve Similar Fit Cases",
    description="Retrieve anonymized historical fit outcomes from the vector fit index.",
    capability="fit.vector.search",
)
def fit_retrieve_similar_cases(
    request: SimilarFitCaseRequest,
) -> list[SimilarFitCase]:
    with get_runtime().session() as session:
        return FitRetrievalService(session).retrieve(request)


FIT_TOOLS = (
    fit_get_profile,
    fit_build_evidence,
    fit_retrieve_similar_cases,
)


__all__ = ["FIT_TOOLS"]
