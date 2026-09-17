"""Deterministic, value-first selection from policy-approved offers only."""

from langsmith import trace

from models.dto import CustomerContext
from models.upsell import (
    OpportunityScore,
    ServiceOfferType,
    UpsellTrigger,
    UpsellTriggerType,
)


class OfferSelector:
    def select(
        self,
        *,
        eligible_offers: list[ServiceOfferType],
        opportunity: OpportunityScore,
        trigger: UpsellTrigger,
        customer_context: CustomerContext,
    ) -> ServiceOfferType | None:
        allowed = set(eligible_offers)
        if not allowed:
            return None

        segment = customer_context.segment.replace("_", " ").casefold()
        with trace(
            name="select_offer",
            run_type="chain",
            inputs={
                "eligible_offers": sorted(item.value for item in allowed),
                "trigger_type": trigger.trigger_type.value,
                "opportunity_band": opportunity.band,
                "segment": customer_context.segment,
            },
            tags=["upsell-agent", "deterministic-selection"],
        ) as run:
            if (
                trigger.trigger_type is UpsellTriggerType.CHRONIC_FIT_RISK
                and ServiceOfferType.STYLING_ADVISORY in allowed
            ):
                selected = ServiceOfferType.STYLING_ADVISORY
            elif segment in {"value defender", "price explorer"}:
                selected = (
                    ServiceOfferType.STYLING_ADVISORY
                    if ServiceOfferType.STYLING_ADVISORY in allowed
                    else None
                )
            elif (
                trigger.trigger_type is UpsellTriggerType.STYLING_ENGAGEMENT
                and opportunity.band == "HIGH"
                and ServiceOfferType.STYLE_PLUS in allowed
            ):
                selected = ServiceOfferType.STYLE_PLUS
            elif (
                trigger.trigger_type
                in {
                    UpsellTriggerType.HIGH_PRODUCT_ENGAGEMENT,
                    UpsellTriggerType.PREMIUM_PRODUCT_INTEREST,
                }
                and opportunity.band in {"MEDIUM", "HIGH"}
                and ServiceOfferType.STYLE_PLUS_TRIAL in allowed
            ):
                selected = ServiceOfferType.STYLE_PLUS_TRIAL
            else:
                selected = next(
                    (
                        item
                        for item in (
                            ServiceOfferType.STYLING_ADVISORY,
                            ServiceOfferType.STYLE_PLUS_TRIAL,
                            ServiceOfferType.STYLE_PLUS,
                        )
                        if item in allowed
                    ),
                    None,
                )
            if selected is not None and selected not in allowed:
                raise AssertionError("OfferSelector selected a policy-disallowed offer")
            run.end(outputs={"selected_offer": selected.value if selected else None})
            return selected


__all__ = ["OfferSelector"]
