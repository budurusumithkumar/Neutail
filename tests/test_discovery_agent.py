from __future__ import annotations

import asyncio
import inspect
from typing import Any

from agents.discovery import DiscoveryAgent, DiscoveryRequest, RetrievalStrategy
from agents.discovery.models import DiscoveryExplanations, ProductExplanation
from llm_gateway import LLMGatewayError
from models.dto import CustomerContext, CustomerPreferences, Product
from orchestrator.models import SessionContext
from tools.permissions import AgentName, DISCOVERY_AGENT_TOOLS
from tools.registry import TOOL_REGISTRY


def _product(
    sku: str,
    *,
    price: float = 90,
    tier: str = "Premium",
    available_sizes: list[str] | None = None,
    gender: str = "Women",
) -> Product:
    return Product(
        sku=sku,
        product_name=f"Product {sku}",
        gender=gender,
        category="Dresses",
        subcategory="Midi Dress",
        brand="Neu Test",
        brand_tier=tier,
        color="Black",
        style="Classic",
        occasion="Wedding",
        material="Silk",
        fit_type="Regular",
        base_price_gbp=price,
        current_price_gbp=price,
        discount_pct=0,
        sizes=available_sizes or ["10", "12"],
        rating=4.7,
        new_arrival=True,
        exclusive_flag=tier == "Premium",
        private_label_flag=tier == "Value",
        active=True,
    )


def _request(
    query: str = "Show me black dresses under £100",
    *,
    view_counts: dict[str, int] | None = None,
    preferred_gender: str | None = None,
) -> DiscoveryRequest:
    return DiscoveryRequest(
        query=query,
        customer_context=CustomerContext(
            customer_id="CUST001",
            segment="Prestige Champion",
            price_sensitivity=0.2,
            premium_affinity=0.91,
            preferences=CustomerPreferences(
                preferred_gender=preferred_gender,
                preferred_categories=["Dresses"],
                preferred_colors=["Black"],
                preferred_styles=["Classic"],
                preferred_occasions=["Wedding"],
                usual_size="12",
            ),
        ),
        session_context=SessionContext(
            session_id="discovery-agent-session",
            customer_id="CUST001",
            attributes={"product_view_counts": view_counts or {}},
        ),
        trace_id="discovery-agent-trace",
    )


class FakeDiscoveryTools:
    def __init__(
        self,
        products: list[Product],
        *,
        inventory: dict[str, bool] | None = None,
        semantic_scores: dict[str, float] | None = None,
    ) -> None:
        self.products = products
        self.inventory = inventory or {
            product.sku: True for product in products
        }
        self.semantic_scores = semantic_scores or {}
        self.calls: list[str] = []

    async def discover_tools(self) -> list[str]:
        return sorted(DISCOVERY_AGENT_TOOLS)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        self.calls.append(tool_name)
        if tool_name == "search_products":
            return [product.model_dump(mode="json") for product in self.products]
        if tool_name == "semantic_product_search":
            return [
                {
                    "sku": product.sku,
                    "similarity_score": self.semantic_scores.get(
                        product.sku, 0.5
                    ),
                }
                for product in self.products
            ]
        if tool_name == "get_product_details":
            sku = arguments["sku"]
            return next(
                product.model_dump(mode="json")
                for product in self.products
                if product.sku == sku
            )
        if tool_name == "check_inventory":
            return {
                sku: self.inventory.get(sku, False)
                for sku in arguments["skus"]
            }
        raise AssertionError(f"Unexpected tool: {tool_name}")

    async def call_many(
        self, tool_name: str, arguments: list[dict[str, Any]]
    ) -> list[Any]:
        return [await self.call_tool(tool_name, item) for item in arguments]


class FailingGateway:
    async def invoke(self, **_kwargs: Any) -> Any:
        raise LLMGatewayError("explanation provider unavailable")


class UnexpectedGateway:
    async def invoke(self, **_kwargs: Any) -> Any:
        raise AssertionError("The LLM must not be invoked for this request")


class ControlledExplanationGateway:
    async def invoke(self, **_kwargs: Any) -> DiscoveryExplanations:
        return DiscoveryExplanations(
            explanations=[
                ProductExplanation(
                    sku="INVENTED-SKU",
                    explanation="This product was not supplied.",
                ),
                ProductExplanation(
                    sku="FACTUAL-SKU",
                    explanation="It matches the supplied ranking reasons.",
                ),
            ]
        )


def test_discovery_runtime_scope_is_exact():
    visible = {
        tool.name for tool in TOOL_REGISTRY.list_tools(AgentName.DISCOVERY)
    }

    assert visible == DISCOVERY_AGENT_TOOLS


def test_explicit_price_ceiling_and_inventory_are_hard_filters():
    within_budget = _product("IN-BUDGET", price=90)
    above_budget = _product("TOO-EXPENSIVE", price=140)
    unavailable = _product("NO-STOCK", price=80)
    tools = FakeDiscoveryTools(
        [within_budget, above_budget, unavailable],
        inventory={
            "IN-BUDGET": True,
            "TOO-EXPENSIVE": True,
            "NO-STOCK": False,
        },
    )

    result = asyncio.run(DiscoveryAgent(tool_client=tools).execute(_request()))

    assert result.status == "SUCCESS"
    assert result.retrieval_strategy is RetrievalStrategy.STRUCTURED
    assert [item.sku for item in result.recommendations] == ["IN-BUDGET"]
    assert result.candidates_retrieved == 3
    assert result.candidates_after_filtering == 1
    assert "check_inventory" in tools.calls


def test_customer_gender_is_enforced_as_a_hard_constraint():
    mens_product = _product("MENS", gender="Men")
    womens_product = _product("WOMENS", gender="Women")
    tools = FakeDiscoveryTools([womens_product, mens_product])

    result = asyncio.run(
        DiscoveryAgent(tool_client=tools).execute(
            _request(preferred_gender="Men")
        )
    )

    assert result.status == "SUCCESS"
    assert [item.sku for item in result.recommendations] == ["MENS"]
    assert result.recommendations[0].gender == "Men"


def test_semantic_retrieval_similarity_reaches_ranking_components():
    first = _product("SEMANTIC-HIGH")
    second = _product("SEMANTIC-LOW")
    tools = FakeDiscoveryTools(
        [first, second],
        semantic_scores={"SEMANTIC-HIGH": 0.95, "SEMANTIC-LOW": 0.1},
    )

    result = asyncio.run(
        DiscoveryAgent(tool_client=tools).execute(
            _request("Something elegant for an evening wedding")
        )
    )

    assert result.retrieval_strategy is RetrievalStrategy.SEMANTIC
    assert result.recommendations[0].sku == "SEMANTIC-HIGH"
    assert result.ranking_scores[0].semantic_score == 0.95
    assert "semantic_product_search" in tools.calls


def test_llm_failure_keeps_ranked_products_without_explanation():
    tools = FakeDiscoveryTools([_product("SAFE-FALLBACK")])
    agent = DiscoveryAgent(
        tool_client=tools,
        llm_gateway=FailingGateway(),  # type: ignore[arg-type]
        explanations_enabled=True,
    )

    result = asyncio.run(agent.execute(_request()))

    assert result.status == "SUCCESS"
    assert result.recommendations[0].sku == "SAFE-FALLBACK"
    assert result.recommendations[0].explanation is None


def test_caller_can_disable_llm_explanations_for_homepage_latency():
    tools = FakeDiscoveryTools([_product("HOME-NO-LLM")])
    agent = DiscoveryAgent(
        tool_client=tools,
        llm_gateway=UnexpectedGateway(),  # type: ignore[arg-type]
        explanations_enabled=True,
    )
    request = _request().model_copy(
        update={"allow_llm_explanations": False}
    )

    result = asyncio.run(agent.execute(request))

    assert result.status == "SUCCESS"
    assert result.recommendations[0].sku == "HOME-NO-LLM"
    assert result.recommendations[0].explanation is None


def test_llm_can_only_attach_explanations_to_already_ranked_products():
    product = _product("FACTUAL-SKU", price=75)
    tools = FakeDiscoveryTools([product])
    agent = DiscoveryAgent(
        tool_client=tools,
        llm_gateway=ControlledExplanationGateway(),  # type: ignore[arg-type]
        explanations_enabled=True,
    )

    result = asyncio.run(agent.execute(_request()))

    assert [item.sku for item in result.recommendations] == ["FACTUAL-SKU"]
    recommendation = result.recommendations[0]
    assert recommendation.product_name == product.product_name
    assert recommendation.price_gbp == product.current_price_gbp
    assert recommendation.explanation == (
        "It matches the supplied ranking reasons."
    )


def test_no_candidates_returns_no_results_without_inventory_call():
    tools = FakeDiscoveryTools([])

    result = asyncio.run(DiscoveryAgent(tool_client=tools).execute(_request()))

    assert result.status == "NO_RESULTS"
    assert result.recommendations == []
    assert tools.calls == ["search_products"]


def test_high_premium_engagement_emits_signal_not_business_decision():
    tools = FakeDiscoveryTools([_product("ENGAGED-PREMIUM")])

    result = asyncio.run(
        DiscoveryAgent(tool_client=tools).execute(
            _request(view_counts={"ENGAGED-PREMIUM": 3})
        )
    )

    assert [signal.signal_type for signal in result.downstream_signals] == [
        "HIGH_PRODUCT_ENGAGEMENT"
    ]
    assert result.downstream_signals[0].sku == "ENGAGED-PREMIUM"
    assert set(tools.calls).issubset(DISCOVERY_AGENT_TOOLS)
    source = inspect.getsource(DiscoveryAgent)
    assert "FitAgent" not in source
    assert "UpsellAgent" not in source
    assert "Style+" not in source


def test_discovery_agent_has_no_direct_persistence_or_provider_dependencies():
    source = inspect.getsource(inspect.getmodule(DiscoveryAgent))

    for forbidden in (
        "sqlalchemy",
        "ProductRepository",
        "InventoryRepository",
        "chromadb",
        "faiss",
        "openai",
        "google.generativeai",
    ):
        assert forbidden not in source
