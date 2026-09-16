"""Typed request and result contracts owned by the Fit Agent."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from models.dto import CustomerContext, DTOModel
from models.fit import (
    FitDecision,
    FitEvidence,
    FitEvidenceSummary,
    FitPurchaseEvidence,
    FitResult,
    FitReturnEvidence,
    FitReturnReason,
    FitSignal,
    ProductFitContext,
    SimilarFitCase,
    SimilarFitCaseRequest,
)
from orchestrator.models import SessionContext


class FitRequest(DTOModel):
    customer_context: CustomerContext
    session_context: SessionContext
    sku: str = Field(min_length=1, max_length=128)
    requested_size: Optional[str] = Field(default=None, min_length=1, max_length=32)
    mode: Literal["PREVENT", "RECOVER"] = "PREVENT"
    trace_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


__all__ = [
    "FitDecision",
    "FitEvidence",
    "FitEvidenceSummary",
    "FitPurchaseEvidence",
    "FitRequest",
    "FitResult",
    "FitReturnEvidence",
    "FitReturnReason",
    "FitSignal",
    "ProductFitContext",
    "SimilarFitCase",
    "SimilarFitCaseRequest",
]
