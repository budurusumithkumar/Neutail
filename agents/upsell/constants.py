"""Central service catalogue and opportunity-scoring policy."""

from dataclasses import dataclass, field

from models.upsell import ServiceOffer, ServiceOfferType


@dataclass(frozen=True)
class OpportunityScoringConfig:
    trigger_weight: float = 0.30
    premium_affinity_weight: float = 0.20
    engagement_weight: float = 0.20
    loyalty_weight: float = 0.15
    segment_affinity_weight: float = 0.15
    medium_band_min: float = 0.45
    high_band_min: float = 0.75
    loyalty_scores: dict[str, float] = field(
        default_factory=lambda: {
            "bronze": 0.25,
            "silver": 0.50,
            "gold": 0.75,
            "platinum": 1.0,
        }
    )
    segment_scores: dict[str, float] = field(
        default_factory=lambda: {
            "prestige champion": 1.0,
            "aspiring loyalist": 0.80,
            "value defender": 0.35,
            "price explorer": 0.20,
        }
    )


DEFAULT_OPPORTUNITY_SCORING = OpportunityScoringConfig()

SERVICE_OFFER_CATALOG: dict[ServiceOfferType, ServiceOffer] = {
    ServiceOfferType.STYLING_ADVISORY: ServiceOffer(
        offer_type=ServiceOfferType.STYLING_ADVISORY,
        title="Styling Advisory",
        description="Optional styling guidance tailored to the current shopping need.",
        priority=100,
    ),
    ServiceOfferType.STYLE_PLUS_TRIAL: ServiceOffer(
        offer_type=ServiceOfferType.STYLE_PLUS_TRIAL,
        title="Try Style+",
        description="An optional trial of the Style+ styling experience.",
        priority=70,
    ),
    ServiceOfferType.STYLE_PLUS: ServiceOffer(
        offer_type=ServiceOfferType.STYLE_PLUS,
        title="Style+",
        description="An optional ongoing Style+ styling service.",
        priority=50,
    ),
}


__all__ = [
    "DEFAULT_OPPORTUNITY_SCORING",
    "OpportunityScoringConfig",
    "SERVICE_OFFER_CATALOG",
]
