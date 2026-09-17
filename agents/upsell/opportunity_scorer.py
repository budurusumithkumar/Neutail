"""Deterministic opportunity scoring after policy eligibility succeeds."""

from langsmith import trace

from agents.upsell.constants import (
    DEFAULT_OPPORTUNITY_SCORING,
    OpportunityScoringConfig,
)
from models.dto import BehaviorSummary, CustomerContext, LoyaltyProfile
from models.upsell import OpportunityScore, UpsellEligibilityResult, UpsellTrigger


class OpportunityScorer:
    def __init__(
        self, config: OpportunityScoringConfig = DEFAULT_OPPORTUNITY_SCORING
    ) -> None:
        self.config = config

    def calculate(
        self,
        *,
        customer_context: CustomerContext,
        loyalty: LoyaltyProfile,
        engagement: BehaviorSummary,
        trigger: UpsellTrigger,
        eligibility: UpsellEligibilityResult,
    ) -> OpportunityScore:
        if not eligibility.eligible:
            raise ValueError("opportunity scoring requires an eligible policy result")

        trigger_score = trigger.strength if trigger.strength is not None else 0.0
        premium_score = customer_context.premium_affinity or 0.0
        engagement_score = engagement.engagement_score
        loyalty_score = self.config.loyalty_scores.get(
            (loyalty.tier or "").casefold(), 0.0
        )
        segment_name = customer_context.segment.replace("_", " ").casefold()
        segment_score = self.config.segment_scores.get(segment_name, 0.0)
        with trace(
            name="calculate_opportunity_score",
            run_type="chain",
            inputs={
                "trigger_type": trigger.trigger_type.value,
                "trigger_strength": trigger_score,
                "segment": customer_context.segment,
            },
            tags=["upsell-agent", "deterministic-scoring"],
        ) as run:
            score = round(
                min(
                    max(
                        trigger_score * self.config.trigger_weight
                        + premium_score * self.config.premium_affinity_weight
                        + engagement_score * self.config.engagement_weight
                        + loyalty_score * self.config.loyalty_weight
                        + segment_score * self.config.segment_affinity_weight,
                        0.0,
                    ),
                    1.0,
                ),
                3,
            )
            band = (
                "HIGH"
                if score >= self.config.high_band_min
                else "MEDIUM"
                if score >= self.config.medium_band_min
                else "LOW"
            )
            reasons = list(dict.fromkeys(eligibility.eligibility_reasons))
            if premium_score >= 0.70:
                reasons.append("HIGH_PREMIUM_AFFINITY")
            if loyalty_score >= 0.75:
                reasons.append("LOYAL_CUSTOMER")
            segment_reason = customer_context.segment.upper().replace(" ", "_")
            if segment_reason in {"PRESTIGE_CHAMPION", "ASPIRING_LOYALIST"}:
                reasons.append(segment_reason)
            result = OpportunityScore(
                score=score,
                band=band,
                reason_codes=list(dict.fromkeys(reasons)),
            )
            run.end(outputs=result.model_dump(mode="json"))
            return result


__all__ = ["OpportunityScorer"]
