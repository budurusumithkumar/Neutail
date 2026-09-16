from __future__ import annotations

from agents.discovery import DiscoveryCriteria, DiscoveryRequest, RetrievalStrategy
from agents.discovery.query_builder import (
    DiscoveryQueryBuilder,
    RetrievalStrategySelector,
)
from models.dto import CustomerContext, CustomerPreferences
from orchestrator.models import SessionContext


def _request(
    query: str,
    *,
    criteria: DiscoveryCriteria | None = None,
    selected_sku: str | None = None,
    preferred_gender: str | None = None,
) -> DiscoveryRequest:
    return DiscoveryRequest(
        query=query,
        criteria=criteria,
        customer_context=CustomerContext(
            customer_id="CUST001",
            segment="Prestige Champion",
            preferences=CustomerPreferences(preferred_gender=preferred_gender),
        ),
        session_context=SessionContext(
            session_id="discovery-query-session",
            customer_id="CUST001",
            selected_sku=selected_sku,
        ),
    )


def _strategy(request: DiscoveryRequest) -> tuple[DiscoveryCriteria, RetrievalStrategy]:
    criteria = DiscoveryQueryBuilder().build(request)
    return criteria, RetrievalStrategySelector().select(request, criteria)


def test_exact_constraints_select_structured_retrieval():
    criteria, strategy = _strategy(
        _request("Show me black dresses under £100")
    )

    assert strategy is RetrievalStrategy.STRUCTURED
    assert criteria.category == "Dresses"
    assert criteria.colors == ["Black"]
    assert criteria.max_price == 100
    assert criteria.semantic_query is None


def test_conceptual_query_selects_semantic_retrieval():
    criteria, strategy = _strategy(
        _request("Something elegant for an evening wedding")
    )

    assert strategy is RetrievalStrategy.SEMANTIC
    assert criteria.occasion == "Wedding"
    assert criteria.semantic_query is not None


def test_semantic_query_with_exact_constraints_selects_hybrid_retrieval():
    criteria, strategy = _strategy(
        _request("Find me an elegant navy dress for a wedding under £200")
    )

    assert strategy is RetrievalStrategy.HYBRID
    assert criteria.colors == ["Navy"]
    assert criteria.max_price == 200


def test_dollar_budget_and_birthday_are_normalized_to_catalogue_constraints():
    criteria, strategy = _strategy(
        _request(
            "I need a dress below $100 for my birthday and it should fit perfectly"
        )
    )

    assert strategy is RetrievalStrategy.STRUCTURED
    assert criteria.category == "Dresses"
    assert criteria.occasion == "Party"
    assert criteria.max_price == 100


def test_customer_gender_is_a_default_constraint_and_explicit_gender_overrides_it():
    message = "suggest me a dress above $80 for my wedding and should fit perfect"
    criteria, strategy = _strategy(
        _request(message, preferred_gender="Men")
    )

    assert strategy is RetrievalStrategy.STRUCTURED
    assert criteria.gender == "Men"
    assert criteria.category == "Dresses"
    assert criteria.occasion == "Wedding"
    assert criteria.min_price == 80

    explicit, _ = _strategy(
        _request(
            "suggest me a women's dress above $80 for my wedding",
            preferred_gender="Men",
        )
    )
    assert explicit.gender == "Women"


def test_selected_product_and_similarity_language_select_similar_item():
    criteria, strategy = _strategy(
        _request("Show me more like this", selected_sku="SKU00123")
    )

    assert strategy is RetrievalStrategy.SIMILAR_ITEM
    assert criteria.semantic_query == "Show me more like this"


def test_orchestrator_supplied_criteria_take_precedence():
    request = _request(
        "Surprise me",
        criteria=DiscoveryCriteria(
            category="Dresses",
            occasion="Wedding",
            semantic_query="elegant evening wedding dress",
        ),
    )

    criteria, strategy = _strategy(request)

    assert criteria.category == "Dresses"
    assert criteria.occasion == "Wedding"
    assert strategy is RetrievalStrategy.SEMANTIC
