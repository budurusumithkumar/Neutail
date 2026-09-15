"""Explainable deterministic personalized ranking for Discovery."""

from __future__ import annotations

from collections.abc import Iterable

from agents.discovery.constants import EDITORIAL_STYLES, RANKING_WEIGHTS
from agents.discovery.models import DiscoveryCriteria, RankedProduct, RankingScore
from models.dto import CustomerContext, Product


def _normalized(value: str | None) -> str:
    return value.strip().casefold() if value else ""


def _normalized_set(values: Iterable[str]) -> set[str]:
    return {_normalized(value) for value in values if _normalized(value)}


def _preference_score(
    product_value: str | None,
    exact_values: list[str],
    preferred_values: list[str],
) -> tuple[float, bool]:
    product = _normalized(product_value)
    exact = _normalized_set(exact_values)
    preferred = _normalized_set(preferred_values)
    if exact:
        return (1.0 if product in exact else 0.0), product in exact
    if preferred:
        return (1.0 if product in preferred else 0.35), product in preferred
    return 0.5, False


class PersonalizedProductRanker:
    """Apply fixed, inspectable ranking weights without model judgment."""

    def rank(
        self,
        candidates: list[Product],
        customer_context: CustomerContext,
        criteria: DiscoveryCriteria,
        *,
        semantic_scores: dict[str, float] | None = None,
        availability: dict[str, bool] | None = None,
    ) -> list[RankedProduct]:
        semantic_scores = semantic_scores or {}
        availability = availability or {product.sku: True for product in candidates}
        ranked = [
            self._score(
                product,
                customer_context,
                criteria,
                semantic_score=semantic_scores.get(product.sku, 0.0),
                available=availability.get(product.sku, False),
            )
            for product in candidates
        ]
        return sorted(
            ranked,
            key=lambda item: (
                -item.ranking.total_score,
                item.product.current_price_gbp
                if item.product.current_price_gbp is not None
                else float("inf"),
                item.product.sku,
            ),
        )

    def _score(
        self,
        product: Product,
        context: CustomerContext,
        criteria: DiscoveryCriteria,
        *,
        semantic_score: float,
        available: bool,
    ) -> RankedProduct:
        preferences = context.preferences
        occasion_score, occasion_match = _preference_score(
            product.occasion,
            [criteria.occasion] if criteria.occasion else [],
            preferences.preferred_occasions,
        )
        style_score, style_match = _preference_score(
            product.style,
            criteria.styles,
            preferences.preferred_styles,
        )
        color_score, color_match = _preference_score(
            product.color,
            criteria.colors,
            preferences.preferred_colors,
        )
        segment_score, segment_reasons = self._segment_score(product, context)
        price_score = self._price_score(product, context, criteria)
        availability_score = 1.0 if available else 0.0
        semantic_score = min(max(semantic_score, 0.0), 1.0)

        total = (
            occasion_score * RANKING_WEIGHTS["occasion"]
            + style_score * RANKING_WEIGHTS["style"]
            + color_score * RANKING_WEIGHTS["color"]
            + segment_score * RANKING_WEIGHTS["segment"]
            + price_score * RANKING_WEIGHTS["price"]
            + semantic_score * RANKING_WEIGHTS["semantic"]
            + availability_score * RANKING_WEIGHTS["availability"]
        )
        reasons: list[str] = []
        if occasion_match:
            reasons.append("OCCASION_MATCH")
        if style_match:
            reasons.append("STYLE_MATCH")
        if color_match:
            reasons.append("COLOR_PREFERENCE")
        reasons.extend(segment_reasons)
        if semantic_score > 0:
            reasons.append("SEMANTIC_MATCH")
        if price_score >= 0.75:
            reasons.append("PRICE_AFFINITY")
        if available:
            reasons.append("IN_STOCK")

        ranking = RankingScore(
            sku=product.sku,
            total_score=round(min(max(total, 0.0), 1.0), 6),
            occasion_score=round(occasion_score, 6),
            style_score=round(style_score, 6),
            color_score=round(color_score, 6),
            segment_score=round(segment_score, 6),
            price_score=round(price_score, 6),
            semantic_score=round(semantic_score, 6),
            availability_score=availability_score,
            reason_codes=list(dict.fromkeys(reasons)),
        )
        return RankedProduct(product=product, ranking=ranking)

    @staticmethod
    def _segment_score(
        product: Product,
        context: CustomerContext,
    ) -> tuple[float, list[str]]:
        segment = _normalized(context.segment_code or context.segment).replace(
            " ", "_"
        )
        tier = _normalized(product.brand_tier)
        style = _normalized(product.style)
        discount = product.discount_pct or 0
        price = product.current_price_gbp or 0.0
        score = 0.1
        reasons: list[str] = []

        if segment == "prestige_champion":
            if tier == "premium":
                score += 0.4
                reasons.extend(
                    ["PREMIUM_AFFINITY", "PRESTIGE_CHAMPION_MATCH"]
                )
            if product.exclusive_flag:
                score += 0.2
                reasons.append("EXCLUSIVE_PRODUCT")
            if product.new_arrival:
                score += 0.15
                reasons.append("NEW_ARRIVAL")
            if discount == 0:
                score += 0.15
                reasons.append("FULL_PRICE_MERCHANDISING")
        elif segment == "value_defender":
            if product.private_label_flag:
                score += 0.4
                reasons.append("PRIVATE_LABEL_VALUE")
            if tier == "value":
                score += 0.3
                reasons.append("VALUE_DEFENDER_MATCH")
            if discount > 0:
                score += 0.2
                reasons.append("VALUE_DISCOUNT")
        elif segment == "price_explorer":
            if tier == "value":
                score += 0.3
                reasons.append("PRICE_EXPLORER_MATCH")
            if price <= 75:
                score += 0.3
                reasons.append("ENTRY_PRICE")
            if product.new_arrival:
                score += 0.2
                reasons.append("TREND_LED_NEW_ARRIVAL")
            if discount > 0:
                score += 0.1
                reasons.append("ACCESSIBLE_DISCOUNT")
        elif segment == "aspiring_loyalist":
            if tier == "premium":
                score += 0.35
                reasons.append("ASPIRING_PREMIUM_DISCOVERY")
            if product.new_arrival:
                score += 0.25
                reasons.append("NEW_ARRIVAL")
            if product.exclusive_flag:
                score += 0.15
                reasons.append("CURATED_EXCLUSIVE")
            if style in EDITORIAL_STYLES:
                score += 0.15
                reasons.append("EDITORIAL_STYLE")
        return min(score, 1.0), reasons

    @staticmethod
    def _price_score(
        product: Product,
        context: CustomerContext,
        criteria: DiscoveryCriteria,
    ) -> float:
        price = product.current_price_gbp
        if price is None:
            return 0.0
        if criteria.min_price is not None or criteria.max_price is not None:
            return 1.0

        affordability = 1.0 - min(price / 500.0, 1.0)
        tier_value = {
            "premium": 1.0,
            "mid": 0.6,
            "value": 0.2,
        }.get(_normalized(product.brand_tier), 0.4)
        price_sensitivity = (
            context.price_sensitivity
            if context.price_sensitivity is not None
            else 0.5
        )
        premium_affinity = (
            context.premium_affinity
            if context.premium_affinity is not None
            else 0.5
        )
        denominator = price_sensitivity + premium_affinity
        if denominator == 0:
            return 0.5
        return min(
            max(
                (
                    price_sensitivity * affordability
                    + premium_affinity * tier_value
                )
                / denominator,
                0.0,
            ),
            1.0,
        )


__all__ = ["PersonalizedProductRanker"]
