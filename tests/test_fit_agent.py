from __future__ import annotations

import asyncio
import inspect
from typing import Any

from agents.fit import FitAgent, FitRequest
from llm_gateway import LLMGatewayError
from models.dto import BrandSizeAdjustment, CustomerContext, CustomerPreferences, FitProfile, Product
from models.fit import FitEvidence
from orchestrator.models import SessionContext
from tools.permissions import FIT_AGENT_TOOLS


def _request() -> FitRequest:
    return FitRequest(
        customer_context=CustomerContext(
            customer_id="CUST-FIT",
            segment="Prestige Champion",
            preferences=CustomerPreferences(
                usual_size="12",
                fit_preference="Tailored",
            ),
        ),
        session_context=SessionContext(
            session_id="fit-session",
            customer_id="CUST-FIT",
            selected_sku="SKU-FIT",
            requested_size="10",
        ),
        sku="SKU-FIT",
        requested_size="10",
        trace_id="fit-trace",
    )


class FakeFitTools:
    def __init__(self, *, vector_fails: bool = False) -> None:
        self.vector_fails = vector_fails
        self.calls: list[str] = []
        self.product = Product(
            sku="SKU-FIT",
            product_name="Tailored Dress",
            brand="BrandX",
            category="Dresses",
            fit_type="Tailored",
            sizes=["8", "10", "12", "14"],
        )
        self.profile = FitProfile(
            customer_id="CUST-FIT",
            usual_size="12",
            preferred_fit="Tailored",
            size_confidence=0.8,
            return_risk_score=0.2,
        )
        self.evidence = FitEvidence(
            customer_id="CUST-FIT",
            sku="SKU-FIT",
            usual_size="12",
            brand_adjustment=BrandSizeAdjustment(
                brand="BrandX", adjustment="0", evidence_count=2
            ),
            historical_return_rate=0.2,
            evidence_strength=0.7,
            same_sku_purchases=[
                {
                    "evidence_id": "kept-12",
                    "sku": "SKU-FIT",
                    "brand": "BrandX",
                    "category": "Dresses",
                    "purchased_size": "12",
                    "outcome": "KEPT",
                    "relevance": "SAME_SKU",
                }
            ],
            size_related_returns=[
                {
                    "evidence_id": "returned-10",
                    "sku": "SKU-FIT",
                    "purchased_size": "10",
                    "reason": "TOO_SMALL",
                    "exchange_size": "12",
                    "relevance": "SAME_SKU",
                }
            ],
        )

    async def discover_tools(self) -> list[str]:
        return sorted(FIT_AGENT_TOOLS)

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(tool_name)
        if tool_name == "get_product_details":
            return self.product.model_dump(mode="json")
        if tool_name == "fit_get_profile":
            return self.profile.model_dump(mode="json")
        if tool_name == "fit_build_evidence":
            return self.evidence.model_dump(mode="json")
        if tool_name == "fit_retrieve_similar_cases":
            if self.vector_fails:
                raise RuntimeError("vector store unavailable")
            return []
        raise AssertionError(f"Unexpected tool: {tool_name}")


class ControlledGateway:
    async def invoke(self, **_kwargs: Any) -> str:
        return "Ignore the decision and buy size 99."


class FailingGateway:
    async def invoke(self, **_kwargs: Any) -> str:
        raise LLMGatewayError("provider unavailable")


def test_vector_failure_still_returns_deterministic_decision():
    tools = FakeFitTools(vector_fails=True)
    result = asyncio.run(
        FitAgent(tool_client=tools, explanations_enabled=False).execute(_request())
    )

    assert result.status == "SUCCESS"
    assert result.recommended_size == "12"
    assert result.action == "RECOMMEND_SIZE_CHANGE"
    assert set(tools.calls) == FIT_AGENT_TOOLS


def test_llm_output_cannot_override_recommended_size():
    result = asyncio.run(
        FitAgent(
            tool_client=FakeFitTools(),
            llm_gateway=ControlledGateway(),  # type: ignore[arg-type]
            explanations_enabled=True,
        ).execute(_request())
    )

    assert result.recommended_size == "12"
    assert result.explanation == "Ignore the decision and buy size 99."


def test_llm_failure_returns_decision_without_explanation():
    result = asyncio.run(
        FitAgent(
            tool_client=FakeFitTools(),
            llm_gateway=FailingGateway(),  # type: ignore[arg-type]
            explanations_enabled=True,
        ).execute(_request())
    )

    assert result.status == "SUCCESS"
    assert result.recommended_size == "12"
    assert result.explanation is None


def test_fit_agent_has_no_sql_repository_or_other_agent_imports():
    source = inspect.getsource(__import__("agents.fit.agent", fromlist=["FitAgent"]))

    assert "sqlalchemy" not in source
    assert "repositories" not in source
    assert "DiscoveryAgent" not in source
    assert "UpsellAgent" not in source
