from __future__ import annotations

from agents.discovery.models import DiscoveryCriteria
from agents.discovery.ranking import PersonalizedProductRanker
from models.dto import CustomerContext, CustomerPreferences, Product


def _context(
    segment: str,
    *,
    price_sensitivity: float,
    premium_affinity: float,
) -> CustomerContext:
    return CustomerContext(
        customer_id="TEST-CUSTOMER",
        segment=segment,
        price_sensitivity=price_sensitivity,
        premium_affinity=premium_affinity,
        preferences=CustomerPreferences(
            preferred_colors=["Navy"],
            preferred_styles=["Classic"],
            preferred_occasions=["Wedding"],
        ),
    )


def _product(
    sku: str,
    *,
    tier: str,
    price: float,
    discount: int = 0,
    private_label: bool = False,
    exclusive: bool = False,
    new_arrival: bool = False,
) -> Product:
    return Product(
        sku=sku,
        product_name=f"Product {sku}",
        category="Dresses",
        subcategory="Midi Dress",
        brand="Test Brand",
        brand_tier=tier,
        color="Navy",
        style="Classic",
        occasion="Wedding",
        material="Silk",
        fit_type="Regular",
        base_price_gbp=price,
        current_price_gbp=price,
        discount_pct=discount,
        sizes=["10", "12"],
        new_arrival=new_arrival,
        exclusive_flag=exclusive,
        private_label_flag=private_label,
        active=True,
    )


def test_prestige_champion_prefers_premium_full_price_merchandising():
    premium = _product(
        "PREMIUM",
        tier="Premium",
        price=250,
        exclusive=True,
        new_arrival=True,
    )
    discounted_value = _product(
        "VALUE",
        tier="Value",
        price=60,
        discount=30,
        private_label=True,
    )

    ranked = PersonalizedProductRanker().rank(
        [discounted_value, premium],
        _context(
            "Prestige Champion",
            price_sensitivity=0.1,
            premium_affinity=0.9,
        ),
        DiscoveryCriteria(occasion="Wedding"),
    )

    assert [item.product.sku for item in ranked] == ["PREMIUM", "VALUE"]
    assert "PRESTIGE_CHAMPION_MATCH" in ranked[0].ranking.reason_codes
    assert "FULL_PRICE_MERCHANDISING" in ranked[0].ranking.reason_codes


def test_value_defender_prefers_private_label_value_product():
    premium = _product("PREMIUM", tier="Premium", price=250)
    value = _product(
        "VALUE",
        tier="Value",
        price=50,
        discount=20,
        private_label=True,
    )

    ranked = PersonalizedProductRanker().rank(
        [premium, value],
        _context(
            "Value Defender",
            price_sensitivity=0.9,
            premium_affinity=0.1,
        ),
        DiscoveryCriteria(occasion="Wedding"),
    )

    assert ranked[0].product.sku == "VALUE"
    assert "PRIVATE_LABEL_VALUE" in ranked[0].ranking.reason_codes
    assert "VALUE_DEFENDER_MATCH" in ranked[0].ranking.reason_codes


def test_vector_similarity_contributes_to_score_and_reason_codes():
    product = _product("SEMANTIC", tier="Mid", price=120)
    context = _context(
        "Aspiring Loyalist",
        price_sensitivity=0.5,
        premium_affinity=0.5,
    )
    ranker = PersonalizedProductRanker()

    without_similarity = ranker.rank(
        [product], context, DiscoveryCriteria()
    )[0]
    with_similarity = ranker.rank(
        [product],
        context,
        DiscoveryCriteria(),
        semantic_scores={product.sku: 0.8},
    )[0]

    assert with_similarity.ranking.total_score > without_similarity.ranking.total_score
    assert with_similarity.ranking.semantic_score == 0.8
    assert "SEMANTIC_MATCH" in with_similarity.ranking.reason_codes
