"""Shared factual contracts for deterministic size-and-fit evaluation."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from models.dto import BrandSizeAdjustment, DTOModel


class FitReturnReason(str, Enum):
    TOO_SMALL = "TOO_SMALL"
    TOO_LARGE = "TOO_LARGE"
    TOO_SHORT = "TOO_SHORT"
    TOO_LONG = "TOO_LONG"
    TIGHT_WAIST = "TIGHT_WAIST"
    TIGHT_CHEST = "TIGHT_CHEST"
    POOR_FIT = "POOR_FIT"
    NOT_AS_EXPECTED = "NOT_AS_EXPECTED"
    NON_FIT_REASON = "NON_FIT_REASON"


class FitCaseRecord(DTOModel):
    """Repository result used to build customer and vector evidence."""

    evidence_id: str
    customer_id: str
    order_datetime: datetime
    sku: str
    product_name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    fit_type: Optional[str] = None
    material: Optional[str] = None
    purchased_size: str
    outcome: Literal["KEPT", "RETURNED"]
    return_reason: Optional[str] = None
    resolution: Optional[str] = None
    exchange_size: Optional[str] = None


class FitPurchaseEvidence(DTOModel):
    evidence_id: str
    sku: str
    brand: Optional[str] = None
    category: Optional[str] = None
    fit_type: Optional[str] = None
    purchased_size: str
    outcome: Literal["KEPT", "RETURNED"]
    return_reason: Optional[FitReturnReason] = None
    exchange_size: Optional[str] = None
    relevance: Literal["SAME_SKU", "SAME_BRAND", "SAME_CATEGORY"]


class FitReturnEvidence(DTOModel):
    evidence_id: str
    sku: str
    purchased_size: str
    reason: FitReturnReason
    exchange_size: Optional[str] = None
    relevance: Literal["SAME_SKU", "SAME_BRAND", "SAME_CATEGORY"]


class SimilarFitCaseRequest(DTOModel):
    customer_id: str = Field(min_length=1, max_length=128)
    sku: str = Field(min_length=1, max_length=128)
    requested_size: Optional[str] = Field(default=None, min_length=1, max_length=32)
    limit: int = Field(default=5, ge=1, le=20)


class SimilarFitCase(DTOModel):
    evidence_id: str
    similarity_score: float = Field(ge=0, le=1)
    brand: Optional[str] = None
    category: Optional[str] = None
    fit_type: Optional[str] = None
    purchased_size: str
    outcome: Literal["KEPT", "RETURNED"]
    return_reason: Optional[FitReturnReason] = None
    exchange_size: Optional[str] = None


class ProductFitContext(DTOModel):
    sku: str
    product_name: str
    brand: str
    category: str
    fit_type: Optional[str] = None
    available_sizes: list[str] = Field(default_factory=list)
    material: Optional[str] = None


class FitEvidenceSummary(DTOModel):
    exact_product_cases: int = Field(ge=0)
    same_brand_cases: int = Field(ge=0)
    same_category_cases: int = Field(ge=0)
    size_related_returns: int = Field(ge=0)
    vector_matches: int = Field(ge=0)
    historical_return_rate: float = Field(ge=0, le=1)
    evidence_strength: float = Field(ge=0, le=1)


class FitEvidence(DTOModel):
    customer_id: str
    sku: str
    usual_size: Optional[str] = None
    same_sku_purchases: list[FitPurchaseEvidence] = Field(default_factory=list)
    same_brand_purchases: list[FitPurchaseEvidence] = Field(default_factory=list)
    same_category_purchases: list[FitPurchaseEvidence] = Field(default_factory=list)
    size_related_returns: list[FitReturnEvidence] = Field(default_factory=list)
    brand_adjustment: BrandSizeAdjustment
    historical_return_rate: float = Field(ge=0, le=1)
    similar_fit_cases: list[SimilarFitCase] = Field(default_factory=list)
    evidence_strength: float = Field(ge=0, le=1)

    def to_summary(self) -> FitEvidenceSummary:
        return FitEvidenceSummary(
            exact_product_cases=len(self.same_sku_purchases),
            same_brand_cases=len(self.same_brand_purchases),
            same_category_cases=len(self.same_category_purchases),
            size_related_returns=len(self.size_related_returns),
            vector_matches=len(self.similar_fit_cases),
            historical_return_rate=self.historical_return_rate,
            evidence_strength=self.evidence_strength,
        )


class FitDecision(DTOModel):
    requested_size: Optional[str] = None
    recommended_size: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    risk_score: float = Field(ge=0, le=1)
    risk_band: Literal["LOW", "MEDIUM", "HIGH"]
    action: Literal[
        "CONFIRM_SIZE",
        "RECOMMEND_SIZE_CHANGE",
        "SHOW_CAUTION",
        "INSUFFICIENT_EVIDENCE",
    ]
    reason_codes: list[str] = Field(default_factory=list)


class FitSignal(DTOModel):
    signal_type: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    strength: float = Field(ge=0, le=1)


class FitResult(DTOModel):
    status: Literal[
        "SUCCESS",
        "INVALID_REQUEST",
        "PRODUCT_NOT_FOUND",
        "INSUFFICIENT_EVIDENCE",
        "FAILED",
    ]
    sku: Optional[str] = None
    requested_size: Optional[str] = None
    recommended_size: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    risk_score: float = Field(default=0.0, ge=0, le=1)
    risk_band: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    action: Literal[
        "CONFIRM_SIZE",
        "RECOMMEND_SIZE_CHANGE",
        "SHOW_CAUTION",
        "INSUFFICIENT_EVIDENCE",
    ] = "INSUFFICIENT_EVIDENCE"
    reason_codes: list[str] = Field(default_factory=list)
    explanation: Optional[str] = None
    downstream_signals: list[FitSignal] = Field(default_factory=list)
    evidence_summary: Optional[FitEvidenceSummary] = None
    errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def successful_result_has_sku(self) -> "FitResult":
        if self.status in {"SUCCESS", "INSUFFICIENT_EVIDENCE"} and not self.sku:
            raise ValueError("sku is required for an evaluated fit result")
        return self


__all__ = [
    "FitCaseRecord",
    "FitDecision",
    "FitEvidence",
    "FitEvidenceSummary",
    "FitPurchaseEvidence",
    "FitResult",
    "FitReturnEvidence",
    "FitReturnReason",
    "FitSignal",
    "ProductFitContext",
    "SimilarFitCase",
    "SimilarFitCaseRequest",
]
