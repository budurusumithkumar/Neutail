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
) -> DiscoveryRequest:
    return DiscoveryRequest(
        query=query,
        criteria=criteria,
        customer_context=CustomerContext(
            customer_id="CUST001",
            segment="Prestige Champion",
            preferences=CustomerPreferences(),
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
