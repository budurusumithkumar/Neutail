"""Deterministic size recommendation and fit-risk calculation."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Optional

from agents.fit.constants import DEFAULT_FIT_POLICY, FitPolicyConfig
from models.dto import FitProfile
from models.fit import (
    FitDecision,
    FitEvidence,
    FitPurchaseEvidence,
    FitReturnReason,
    ProductFitContext,
)


_DIRECTIONAL_REASONS = {
    FitReturnReason.TOO_SMALL,
    FitReturnReason.TOO_LARGE,
    FitReturnReason.TOO_SHORT,
    FitReturnReason.TOO_LONG,
    FitReturnReason.TIGHT_WAIST,
    FitReturnReason.TIGHT_CHEST,
    FitReturnReason.POOR_FIT,
    FitReturnReason.NOT_AS_EXPECTED,
}


class FitCalculator:
    """Own every size and risk decision; LLMs only explain this output."""

    def __init__(self, config: FitPolicyConfig = DEFAULT_FIT_POLICY) -> None:
        self.config = config

    def evaluate(
        self,
        *,
        profile: FitProfile,
        product: ProductFitContext,
        requested_size: Optional[str],
        evidence: FitEvidence,
    ) -> FitDecision:
        requested = requested_size.strip() if requested_size else None
        usual = profile.usual_size or evidence.usual_size
        available = product.available_sizes
        reasons: list[str] = []

        if requested and requested == usual:
            reasons.append("MATCHES_USUAL_SIZE")
        elif requested and usual:
            reasons.append("REQUESTED_SIZE_DIFFERS_FROM_USUAL")
        if requested and requested not in available:
            reasons.append("SIZE_NOT_AVAILABLE")

        direct_cases = [
            *evidence.same_sku_purchases,
            *evidence.same_brand_purchases,
            *evidence.same_category_purchases,
        ]
        exact_kept = self._kept_size(evidence.same_sku_purchases, requested)
        brand_kept = self._kept_size(evidence.same_brand_purchases, requested)
        kept_requested = exact_kept or brand_kept
        if kept_requested:
            reasons.append("PREVIOUS_SIZE_SUCCESS")

        requested_returns = [
            item
            for item in evidence.size_related_returns
            if requested and item.purchased_size == requested
        ]
        exact_returns = [item for item in requested_returns if item.relevance == "SAME_SKU"]
        brand_returns = [item for item in requested_returns if item.relevance == "SAME_BRAND"]
        category_returns = [
            item for item in requested_returns if item.relevance == "SAME_CATEGORY"
        ]
        if exact_returns:
            matching_returns = exact_returns
        elif exact_kept:
            matching_returns = []
        elif brand_returns:
            matching_returns = brand_returns
        elif brand_kept:
            matching_returns = []
        else:
            matching_returns = category_returns
        for item in matching_returns:
            if item.reason is FitReturnReason.TOO_SMALL:
                reasons.append("PREVIOUS_TOO_SMALL_RETURN")
            elif item.reason is FitReturnReason.TOO_LARGE:
                reasons.append("PREVIOUS_TOO_LARGE_RETURN")

        recommended: Optional[str] = None
        exchange_size = self._preferred_exchange_size(
            matching_returns,
            available,
        )
        if exchange_size:
            recommended = exchange_size
            if self._kept_size(direct_cases, exchange_size):
                reasons.append("SUCCESSFUL_EXCHANGE_SIZE")
            else:
                reasons.append("HISTORICAL_EXCHANGE_SIZE")

        adjustment = self._parse_adjustment(evidence.brand_adjustment.adjustment)
        if adjustment:
            reasons.append(
                "KNOWN_BRAND_RUNS_SMALL" if adjustment > 0 else "KNOWN_BRAND_RUNS_LARGE"
            )
            if recommended is None:
                recommended = self._adjusted_size(
                    usual or requested,
                    adjustment,
                    available,
                )

        if recommended is None and kept_requested and requested in available:
            recommended = requested
        if recommended is None and usual in available:
            recommended = usual
        if recommended is None and requested in available:
            recommended = requested
        if recommended is None:
            vector_size = self._vector_supported_size(evidence, available)
            if vector_size:
                recommended = vector_size
                reasons.append("SIMILAR_FIT_CASE_SUPPORT")

        risk_score = self._risk_score(
            requested=requested,
            usual=usual,
            adjustment=adjustment,
            matching_returns=matching_returns,
            evidence=evidence,
            exact_success=exact_kept,
        )
        if evidence.historical_return_rate > self.config.chronic_return_rate:
            reasons.append("HIGH_RETURN_HISTORY")
        if evidence.evidence_strength < self.config.minimum_evidence_strength:
            reasons.append("LOW_FIT_EVIDENCE")

        confidence = self._confidence(
            profile=profile,
            evidence=evidence,
            exact_success=exact_kept,
            exchange_supported=exchange_size is not None,
        )
        risk_band = self._risk_band(risk_score)
        insufficient = (
            recommended is None
            or (
                evidence.evidence_strength < self.config.minimum_evidence_strength
                and not evidence.similar_fit_cases
                and profile.usual_size is None
            )
        )
        if insufficient:
            action = "INSUFFICIENT_EVIDENCE"
            if "LOW_FIT_EVIDENCE" not in reasons:
                reasons.append("LOW_FIT_EVIDENCE")
        elif requested and recommended != requested:
            action = "RECOMMEND_SIZE_CHANGE"
        elif risk_band == "HIGH" or "SIZE_NOT_AVAILABLE" in reasons:
            action = "SHOW_CAUTION"
        else:
            action = "CONFIRM_SIZE"

        return FitDecision(
            requested_size=requested,
            recommended_size=recommended,
            confidence=confidence,
            risk_score=risk_score,
            risk_band=risk_band,
            action=action,
            reason_codes=list(dict.fromkeys(reasons)),
        )

    def _risk_score(
        self,
        *,
        requested: Optional[str],
        usual: Optional[str],
        adjustment: int,
        matching_returns: list,
        evidence: FitEvidence,
        exact_success: bool,
    ) -> float:
        mismatch = 1.0 if requested and usual and requested != usual else 0.0
        brand_risk = 1.0 if adjustment else 0.0
        return_risk = 0.0
        directional_returns = [
            item for item in matching_returns if item.reason in _DIRECTIONAL_REASONS
        ]
        if directional_returns:
            weights = {
                "SAME_SKU": 1.0,
                "SAME_BRAND": 0.75,
                "SAME_CATEGORY": 0.5,
            }
            return_risk = max(
                weights[item.relevance]
                for item in directional_returns
            )
        similar_for_size = [
            item
            for item in evidence.similar_fit_cases
            if requested is None or item.purchased_size == requested
        ]
        similar_risk = (
            sum(
                item.similarity_score
                for item in similar_for_size
                if item.outcome == "RETURNED"
                and item.return_reason in _DIRECTIONAL_REASONS
            )
            / max(sum(item.similarity_score for item in similar_for_size), 1.0)
            if similar_for_size
            else 0.0
        )
        risk = (
            evidence.historical_return_rate * 0.25
            + mismatch * 0.20
            + brand_risk * 0.20
            + return_risk * 0.25
            + similar_risk * 0.10
        )
        if exact_success and return_risk < 1.0:
            risk -= 0.15
        return round(min(max(risk, 0.0), 1.0), 3)

    @staticmethod
    def _confidence(
        *,
        profile: FitProfile,
        evidence: FitEvidence,
        exact_success: bool,
        exchange_supported: bool,
    ) -> float:
        confidence = (profile.size_confidence or 0.0) * 0.40
        confidence += evidence.evidence_strength * 0.40
        confidence += 0.15 if exact_success else 0.0
        confidence += 0.15 if exchange_supported else 0.0
        confidence += min(len(evidence.similar_fit_cases), 5) * 0.01
        return round(min(max(confidence, 0.0), 1.0), 3)

    def _risk_band(self, score: float) -> str:
        if score < self.config.low_risk_max:
            return "LOW"
        if score < self.config.medium_risk_max:
            return "MEDIUM"
        return "HIGH"

    @staticmethod
    def _parse_adjustment(value: str) -> int:
        normalized = (value or "0").strip().upper()
        if normalized == "SIZE_UP":
            return 1
        if normalized == "SIZE_DOWN":
            return -1
        try:
            return int(normalized)
        except ValueError:
            return 0

    @staticmethod
    def _adjusted_size(
        base_size: Optional[str],
        steps: int,
        available: list[str],
    ) -> Optional[str]:
        if base_size not in available:
            return None
        index = available.index(base_size) + steps
        return available[index] if 0 <= index < len(available) else None

    @staticmethod
    def _kept_size(
        cases: Iterable[FitPurchaseEvidence], size: Optional[str]
    ) -> bool:
        return bool(size) and any(
            item.purchased_size == size and item.outcome == "KEPT" for item in cases
        )

    @staticmethod
    def _preferred_exchange_size(returns, available: list[str]) -> Optional[str]:
        sizes = [item.exchange_size for item in returns if item.exchange_size in available]
        return Counter(sizes).most_common(1)[0][0] if sizes else None

    @staticmethod
    def _vector_supported_size(
        evidence: FitEvidence, available: list[str]
    ) -> Optional[str]:
        weights: Counter[str] = Counter()
        for item in evidence.similar_fit_cases:
            if item.outcome == "KEPT" and item.purchased_size in available:
                weights[item.purchased_size] += item.similarity_score
        return weights.most_common(1)[0][0] if weights else None


__all__ = ["FitCalculator"]
