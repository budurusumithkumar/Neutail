from __future__ import annotations

from agents.fit import FitCalculator, FitPolicy
from models.dto import BrandSizeAdjustment, FitProfile
from models.fit import (
    FitEvidence,
    FitPurchaseEvidence,
    FitReturnEvidence,
    FitReturnReason,
    ProductFitContext,
)


def _profile(**updates) -> FitProfile:
    values = {
        "customer_id": "CUST-FIT",
        "usual_size": "12",
        "preferred_fit": "Tailored",
        "size_confidence": 0.8,
        "return_risk_score": 0.2,
    }
    values.update(updates)
    return FitProfile(**values)


def _product(**updates) -> ProductFitContext:
    values = {
        "sku": "SKU-FIT",
        "product_name": "Tailored Dress",
        "brand": "BrandX",
        "category": "Dresses",
        "fit_type": "Tailored",
        "available_sizes": ["8", "10", "12", "14"],
        "material": "Satin",
    }
    values.update(updates)
    return ProductFitContext(**values)


def _evidence(**updates) -> FitEvidence:
    values = {
        "customer_id": "CUST-FIT",
        "sku": "SKU-FIT",
        "usual_size": "12",
        "brand_adjustment": BrandSizeAdjustment(
            brand="BrandX", adjustment="0", evidence_count=1
        ),
        "historical_return_rate": 0.1,
        "evidence_strength": 0.6,
    }
    values.update(updates)
    return FitEvidence(**values)


def _purchase(
    *,
    size: str,
    outcome: str,
    relevance: str = "SAME_SKU",
) -> FitPurchaseEvidence:
    return FitPurchaseEvidence(
        evidence_id=f"purchase-{size}-{outcome}",
        sku="SKU-FIT",
        brand="BrandX",
        category="Dresses",
        fit_type="Tailored",
        purchased_size=size,
        outcome=outcome,
        relevance=relevance,
    )


def _return(
    *,
    size: str,
    reason: FitReturnReason,
    exchange_size: str | None = None,
    relevance: str = "SAME_SKU",
) -> FitReturnEvidence:
    return FitReturnEvidence(
        evidence_id=f"return-{size}-{reason.value}",
        sku="SKU-FIT",
        purchased_size=size,
        reason=reason,
        exchange_size=exchange_size,
        relevance=relevance,
    )


def test_usual_size_with_successful_history_is_low_risk():
    decision = FitCalculator().evaluate(
        profile=_profile(),
        product=_product(),
        requested_size="12",
        evidence=_evidence(
            same_sku_purchases=[_purchase(size="12", outcome="KEPT")]
        ),
    )

    assert decision.recommended_size == "12"
    assert decision.risk_band == "LOW"
    assert decision.action == "CONFIRM_SIZE"
    assert "MATCHES_USUAL_SIZE" in decision.reason_codes
    assert "PREVIOUS_SIZE_SUCCESS" in decision.reason_codes


def test_too_small_return_and_exchange_recommends_larger_size():
    decision = FitCalculator().evaluate(
        profile=_profile(),
        product=_product(),
        requested_size="10",
        evidence=_evidence(
            same_sku_purchases=[
                _purchase(size="10", outcome="RETURNED"),
                _purchase(size="12", outcome="KEPT"),
            ],
            size_related_returns=[
                _return(
                    size="10",
                    reason=FitReturnReason.TOO_SMALL,
                    exchange_size="12",
                )
            ],
        ),
    )

    assert decision.recommended_size == "12"
    assert decision.action == "RECOMMEND_SIZE_CHANGE"
    assert "PREVIOUS_TOO_SMALL_RETURN" in decision.reason_codes
    assert "SUCCESSFUL_EXCHANGE_SIZE" in decision.reason_codes
    assert decision.risk_score >= 0.45


def test_known_brand_size_up_adjustment_changes_recommendation():
    decision = FitCalculator().evaluate(
        profile=_profile(),
        product=_product(),
        requested_size="12",
        evidence=_evidence(
            brand_adjustment=BrandSizeAdjustment(
                brand="BrandX", adjustment="SIZE_UP", evidence_count=2
            )
        ),
    )

    assert decision.recommended_size == "14"
    assert "KNOWN_BRAND_RUNS_SMALL" in decision.reason_codes


def test_non_fit_return_does_not_materially_increase_fit_risk():
    decision = FitCalculator().evaluate(
        profile=_profile(),
        product=_product(),
        requested_size="12",
        evidence=_evidence(
            historical_return_rate=1.0,
            size_related_returns=[
                _return(size="12", reason=FitReturnReason.NON_FIT_REASON)
            ],
        ),
    )

    assert decision.risk_score == 0.25
    assert decision.risk_band == "LOW"
    assert "PREVIOUS_TOO_SMALL_RETURN" not in decision.reason_codes
    assert "PREVIOUS_TOO_LARGE_RETURN" not in decision.reason_codes


def test_unavailable_adjusted_size_is_not_recommended():
    decision = FitCalculator().evaluate(
        profile=_profile(usual_size="14"),
        product=_product(available_sizes=["8", "10", "12"]),
        requested_size="14",
        evidence=_evidence(
            usual_size="14",
            brand_adjustment=BrandSizeAdjustment(
                brand="BrandX", adjustment="SIZE_UP", evidence_count=3
            ),
        ),
    )

    assert decision.recommended_size is None
    assert decision.action == "INSUFFICIENT_EVIDENCE"
    assert "SIZE_NOT_AVAILABLE" in decision.reason_codes


def test_chronic_return_rate_publishes_signal_without_making_offer():
    evidence = _evidence(historical_return_rate=0.75)
    decision = FitCalculator().evaluate(
        profile=_profile(),
        product=_product(),
        requested_size="12",
        evidence=evidence,
    )
    signals = FitPolicy().evaluate_signals(decision=decision, evidence=evidence)

    assert [signal.signal_type for signal in signals] == ["CHRONIC_FIT_RISK"]
    assert signals[0].severity == "HIGH"


def test_no_profile_or_history_returns_insufficient_evidence():
    decision = FitCalculator().evaluate(
        profile=_profile(usual_size=None, size_confidence=None),
        product=_product(),
        requested_size=None,
        evidence=_evidence(usual_size=None, evidence_strength=0.0),
    )

    assert decision.recommended_size is None
    assert decision.action == "INSUFFICIENT_EVIDENCE"
    assert "LOW_FIT_EVIDENCE" in decision.reason_codes


def test_exact_customer_evidence_outranks_conflicting_vector_cases():
    decision = FitCalculator().evaluate(
        profile=_profile(),
        product=_product(),
        requested_size="10",
        evidence=_evidence(
            same_sku_purchases=[_purchase(size="12", outcome="KEPT")],
            size_related_returns=[
                _return(
                    size="10",
                    reason=FitReturnReason.TOO_SMALL,
                    exchange_size="12",
                )
            ],
            similar_fit_cases=[
                {
                    "evidence_id": "vector-conflict",
                    "similarity_score": 0.99,
                    "brand": "Other Brand",
                    "category": "Dresses",
                    "purchased_size": "8",
                    "outcome": "KEPT",
                }
            ],
        ),
    )

    assert decision.recommended_size == "12"
    assert "SIMILAR_FIT_CASE_SUPPORT" not in decision.reason_codes
