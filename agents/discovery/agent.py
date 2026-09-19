"""Tool-driven Discovery Agent with deterministic ranking and safe explanation."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any, Optional, Protocol

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ClientError, ToolError
from langsmith import get_current_run_tree, trace, traceable

from agents.discovery.models import (
    DiscoveryCriteria,
    DiscoveryExplanations,
    DiscoveryRequest,
    DiscoveryResult,
    DiscoverySignal,
    ProductRecommendation,
    RankedProduct,
    RetrievalStrategy,
)
from agents.discovery.query_builder import (
    DiscoveryQueryBuilder,
    RetrievalStrategySelector,
)
from agents.discovery.ranking import PersonalizedProductRanker
from llm_gateway import LLMGateway, LLMGatewayError
from models.dto import (
    Product,
    ProductSearchInput,
    SemanticProductMatch,
    SemanticProductSearchInput,
)
from services.product_image_service import get_product_image_url
from tools.permissions import AgentName, DISCOVERY_AGENT_TOOLS
from tools.registry import create_agent_server


DISCOVERY_EXPLANATIONS_ENV = "NEUTAIL_DISCOVERY_EXPLANATIONS_LLM"
DEFAULT_RETRIEVAL_LIMIT = 30


class DiscoveryAgentError(RuntimeError):
    """Base error raised by the Discovery Agent boundary."""


class DiscoveryToolDiscoveryError(DiscoveryAgentError):
    """Raised when the runtime tool scope lacks a required capability."""


class DiscoveryToolInvocationError(DiscoveryAgentError):
    """Raised when a required Discovery MCP tool fails."""


class DiscoveryToolClient(Protocol):
    async def discover_tools(self) -> list[str]: ...

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any: ...

    async def call_many(
        self,
        tool_name: str,
        arguments: list[dict[str, Any]],
    ) -> list[Any]: ...


def _tool_payload(result: Any) -> Any:
    payload = result.structured_content
    if payload is None:
        payload = result.data
    if isinstance(payload, dict) and set(payload) == {"result"}:
        return payload["result"]
    return payload


class FastMCPDiscoveryToolClient:
    """Discover and invoke only the registry-scoped Discovery MCP server."""

    def __init__(
        self,
        server_factory: Callable[[], FastMCP] = lambda: create_agent_server(
            AgentName.DISCOVERY
        ),
    ) -> None:
        self._server_factory = server_factory

    async def discover_tools(self) -> list[str]:
        with trace(
            name="discovery_tool_discovery",
            run_type="tool",
            inputs={"agent": AgentName.DISCOVERY.value},
            tags=["fastmcp", "tool-discovery", "discovery-agent"],
        ) as run:
            async with Client(self._server_factory()) as client:
                names = sorted(tool.name for tool in await client.list_tools())
            run.end(outputs={"tools": names})
            return names

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        self._require_tool(tool_name)
        with trace(
            name=tool_name,
            run_type="tool",
            inputs={"argument_keys": sorted(arguments)},
            tags=["fastmcp", "discovery-agent"],
            metadata={"agent": AgentName.DISCOVERY.value},
        ) as run:
            async with Client(self._server_factory()) as client:
                result = await client.call_tool(tool_name, arguments)
            payload = _tool_payload(result)
            run.end(outputs={"result_type": type(payload).__name__})
            return payload

    async def call_many(
        self,
        tool_name: str,
        arguments: list[dict[str, Any]],
    ) -> list[Any]:
        self._require_tool(tool_name)
        with trace(
            name=f"{tool_name}_batch",
            run_type="tool",
            inputs={"call_count": len(arguments)},
            tags=["fastmcp", "discovery-agent", "batch"],
            metadata={"agent": AgentName.DISCOVERY.value},
        ) as run:
            async with Client(self._server_factory()) as client:
                tool_results = await asyncio.gather(
                    *(
                        client.call_tool(tool_name, item)
                        for item in arguments
                    )
                )
                results = [_tool_payload(result) for result in tool_results]
            run.end(outputs={"result_count": len(results)})
            return results

    @staticmethod
    def _require_tool(tool_name: str) -> None:
        if tool_name not in DISCOVERY_AGENT_TOOLS:
            raise DiscoveryToolInvocationError(
                f"Discovery Agent is not permitted to call '{tool_name}'"
            )


def _trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    if isinstance(request, DiscoveryRequest):
        return {
            "customer_id": request.customer_context.customer_id,
            "session_id": request.session_context.session_id,
            "segment": request.customer_context.segment_code,
            "query_characters": len(request.query),
            "max_results": request.max_results,
        }
    return {"request_type": type(request).__name__}


def _trace_outputs(output: Any) -> dict[str, Any]:
    if isinstance(output, DiscoveryResult):
        return {
            "status": output.status,
            "retrieval_strategy": output.retrieval_strategy.value,
            "candidate_count": output.candidates_retrieved,
            "final_result_count": len(output.recommendations),
            "signal_count": len(output.downstream_signals),
        }
    return {"output_type": type(output).__name__}


class DiscoveryAgent:
    """Retrieve through MCP, filter facts, and rank without an LLM."""

    def __init__(
        self,
        *,
        tool_client: DiscoveryToolClient | None = None,
        query_builder: DiscoveryQueryBuilder | None = None,
        strategy_selector: RetrievalStrategySelector | None = None,
        ranker: PersonalizedProductRanker | None = None,
        llm_gateway: LLMGateway | None = None,
        explanations_enabled: Optional[bool] = None,
    ) -> None:
        self.tool_client = tool_client or FastMCPDiscoveryToolClient()
        self.query_builder = query_builder or DiscoveryQueryBuilder()
        self.strategy_selector = strategy_selector or RetrievalStrategySelector()
        self.ranker = ranker or PersonalizedProductRanker()
        self.llm_gateway = llm_gateway or LLMGateway()
        self.explanations_enabled = (
            os.getenv(DISCOVERY_EXPLANATIONS_ENV, "false").casefold() == "true"
            if explanations_enabled is None
            else explanations_enabled
        )

    @traceable(
        name="discovery_agent",
        run_type="chain",
        tags=["discovery-agent", "deterministic-ranking", "vector-retrieval"],
        process_inputs=_trace_inputs,
        process_outputs=_trace_outputs,
    )
    async def execute(
        self,
        request: DiscoveryRequest | dict[str, Any],
    ) -> DiscoveryResult:
        if not isinstance(request, DiscoveryRequest):
            request = DiscoveryRequest.model_validate(request)
        self._attach_request_trace_metadata(request)

        await self._verify_tool_scope()
        with trace(
            name="build_discovery_criteria",
            run_type="chain",
            inputs={"session_id": request.session_context.session_id},
        ) as run:
            criteria = self.query_builder.build(request)
            run.end(outputs=criteria.model_dump(mode="json"))

        with trace(
            name="select_retrieval_strategy",
            run_type="chain",
            inputs={"has_semantic_query": criteria.semantic_query is not None},
        ) as run:
            strategy = self.strategy_selector.select(request, criteria)
            run.end(outputs={"retrieval_strategy": strategy.value})

        try:
            candidates, semantic_scores, retrieved_count = await self._retrieve(
                request,
                criteria,
                strategy,
            )
            if not candidates:
                return self._empty_result(strategy, retrieved_count)

            availability = await self._check_inventory(candidates)
            available_candidates = [
                product
                for product in candidates
                if availability.get(product.sku, False)
            ]
            with trace(
                name="apply_hard_constraints",
                run_type="chain",
                inputs={"candidate_count": len(available_candidates)},
            ) as run:
                filtered = self._apply_constraints(
                    available_candidates,
                    criteria,
                )
                run.end(outputs={"candidate_count": len(filtered)})
            if not filtered:
                return self._empty_result(strategy, retrieved_count)

            with trace(
                name="personalized_ranking",
                run_type="chain",
                inputs={
                    "candidate_count": len(filtered),
                    "segment": request.customer_context.segment_code,
                },
            ) as run:
                ranked = self.ranker.rank(
                    filtered,
                    request.customer_context,
                    criteria,
                    semantic_scores=semantic_scores,
                    availability=availability,
                )
                run.end(outputs={"ranked_count": len(ranked)})

            complete_ranked = [
                item for item in ranked if self._has_required_facts(item.product)
            ]
            top_ranked = complete_ranked[: request.max_results]
            if not top_ranked:
                return self._empty_result(strategy, retrieved_count)

            recommendations = [
                self._to_recommendation(item) for item in top_ranked
            ]
            recommendations = await self._explain(recommendations, request)
            signals = self._generate_signals(top_ranked, request)
            result = DiscoveryResult(
                status="SUCCESS",
                retrieval_strategy=strategy,
                recommendations=recommendations,
                candidates_retrieved=retrieved_count,
                candidates_after_filtering=len(complete_ranked),
                downstream_signals=signals,
                ranking_scores=[item.ranking for item in complete_ranked],
            )
            self._attach_trace_metadata(result)
            return result
        except (ClientError, ToolError, DiscoveryToolInvocationError) as exc:
            result = DiscoveryResult(
                status="FAILED",
                retrieval_strategy=strategy,
                recommendations=[],
                candidates_retrieved=0,
                candidates_after_filtering=0,
                errors=[type(exc).__name__],
            )
            self._attach_trace_metadata(result)
            return result

    async def _verify_tool_scope(self) -> None:
        discovered = set(await self.tool_client.discover_tools())
        missing = DISCOVERY_AGENT_TOOLS.difference(discovered)
        if missing:
            raise DiscoveryToolDiscoveryError(
                f"Discovery Agent is missing required MCP tools: {sorted(missing)}"
            )

    async def _retrieve(
        self,
        request: DiscoveryRequest,
        criteria: DiscoveryCriteria,
        strategy: RetrievalStrategy,
    ) -> tuple[list[Product], dict[str, float], int]:
        if strategy is RetrievalStrategy.STRUCTURED:
            payload = await self.tool_client.call_tool(
                "search_products",
                {
                    "criteria": ProductSearchInput(
                        gender=criteria.gender,
                        category=criteria.category,
                        subcategory=criteria.subcategory,
                        occasion=criteria.occasion,
                        colors=criteria.colors,
                        styles=criteria.styles,
                        min_price=criteria.min_price,
                        max_price=criteria.max_price,
                        sizes=(
                            [criteria.requested_size]
                            if criteria.requested_size
                            else []
                        ),
                        limit=DEFAULT_RETRIEVAL_LIMIT,
                    ).model_dump(mode="json")
                },
            )
            products = [Product.model_validate(item) for item in (payload or [])]
            return products, {}, len(products)

        semantic_query = criteria.semantic_query or request.query
        semantic_gender = criteria.gender
        semantic_category = criteria.category
        semantic_occasion = criteria.occasion
        selected_sku = request.session_context.selected_sku
        if strategy is RetrievalStrategy.SIMILAR_ITEM and selected_sku:
            source_payload = await self.tool_client.call_tool(
                "get_product_details",
                {"sku": selected_sku},
            )
            source = Product.model_validate(source_payload)
            semantic_query = " ".join(
                value
                for value in (
                    source.product_name,
                    source.category,
                    source.subcategory,
                    source.brand_tier,
                    source.color,
                    source.style,
                    source.occasion,
                    source.material,
                )
                if value
            )
            semantic_category = source.category
            semantic_occasion = None

        with trace(
            name="semantic_product_search",
            run_type="retriever",
            inputs={
                "gender": semantic_gender,
                "category": semantic_category,
                "occasion": semantic_occasion,
                "limit": DEFAULT_RETRIEVAL_LIMIT,
            },
        ) as run:
            match_payload = await self.tool_client.call_tool(
                "semantic_product_search",
                {
                    "request": SemanticProductSearchInput(
                        query=semantic_query,
                        gender=semantic_gender,
                        category=semantic_category,
                        occasion=semantic_occasion,
                        limit=DEFAULT_RETRIEVAL_LIMIT,
                    ).model_dump(mode="json")
                },
            )
            matches = [
                match
                for item in (match_payload or [])
                if (match := SemanticProductMatch.model_validate(item)).sku
                != selected_sku
            ]
            run.end(outputs={"match_count": len(matches)})

        detail_payloads = await self.tool_client.call_many(
            "get_product_details",
            [{"sku": match.sku} for match in matches],
        )
        products = [Product.model_validate(item) for item in detail_payloads]
        return (
            products,
            {match.sku: match.similarity_score for match in matches},
            len(matches),
        )

    async def _check_inventory(
        self,
        candidates: list[Product],
    ) -> dict[str, bool]:
        with trace(
            name="check_inventory",
            run_type="tool",
            inputs={"candidate_count": len(candidates)},
        ) as run:
            payload = await self.tool_client.call_tool(
                "check_inventory",
                {"skus": [product.sku for product in candidates]},
            )
            availability = {
                str(sku): bool(available)
                for sku, available in (payload or {}).items()
            }
            run.end(
                outputs={
                    "available_count": sum(availability.values()),
                }
            )
            return availability

    @staticmethod
    def _apply_constraints(
        candidates: list[Product],
        criteria: DiscoveryCriteria,
    ) -> list[Product]:
        def matches(value: str | None, expected: str | None) -> bool:
            return expected is None or (
                value is not None and value.casefold() == expected.casefold()
            )

        colors = {value.casefold() for value in criteria.colors}
        styles = {value.casefold() for value in criteria.styles}
        requested_size = (
            criteria.requested_size.casefold()
            if criteria.requested_size
            else None
        )
        filtered: list[Product] = []
        for product in candidates:
            price = product.current_price_gbp
            if product.active is False:
                continue
            if not matches(product.gender, criteria.gender):
                continue
            if not matches(product.category, criteria.category):
                continue
            if not matches(product.subcategory, criteria.subcategory):
                continue
            if not matches(product.occasion, criteria.occasion):
                continue
            if colors and (product.color or "").casefold() not in colors:
                continue
            if styles and (product.style or "").casefold() not in styles:
                continue
            if criteria.min_price is not None and (
                price is None or price < criteria.min_price
            ):
                continue
            if criteria.max_price is not None and (
                price is None or price > criteria.max_price
            ):
                continue
            if requested_size and requested_size not in {
                size.casefold() for size in product.sizes
            }:
                continue
            filtered.append(product)
        return filtered

    async def _explain(
        self,
        recommendations: list[ProductRecommendation],
        request: DiscoveryRequest,
    ) -> list[ProductRecommendation]:
        if not self.explanations_enabled or not request.allow_llm_explanations:
            return recommendations
        explanation_candidates = recommendations[:3]
        with trace(
            name="discovery_explanation",
            run_type="chain",
            inputs={"recommendation_count": len(explanation_candidates)},
        ) as run:
            try:
                response = await self.llm_gateway.invoke(
                    use_case="discovery_explanation",
                    prompt_version="v1",
                    context={
                        "customer_context": {
                            "segment": request.customer_context.segment,
                            "preferences": request.customer_context.preferences,
                        },
                        "intent": request.query,
                        "products": [
                            item.model_dump(
                                mode="json",
                                exclude={"explanation"},
                            )
                            for item in explanation_candidates
                        ],
                        "reason_codes": {
                            item.sku: item.reason_codes
                            for item in explanation_candidates
                        },
                    },
                    agent_name="DiscoveryAgent",
                    trace_id=request.trace_id or request.session_context.session_id,
                    session_id=request.session_context.session_id,
                    response_model=DiscoveryExplanations,
                )
            except (LLMGatewayError, LookupError, TimeoutError) as exc:
                run.end(outputs={"status": "UNAVAILABLE"}, error=type(exc).__name__)
                return recommendations

            if not isinstance(response, DiscoveryExplanations):
                run.end(outputs={"status": "INVALID_RESPONSE"})
                return recommendations
            allowed_skus = {item.sku for item in explanation_candidates}
            explanations = {
                item.sku: item.explanation
                for item in response.explanations
                if item.sku in allowed_skus
            }
            run.end(outputs={"explained_count": len(explanations)})
            return [
                item.model_copy(
                    update={"explanation": explanations.get(item.sku)}
                )
                for item in recommendations
            ]

    @staticmethod
    def _generate_signals(
        ranked: list[RankedProduct],
        request: DiscoveryRequest,
    ) -> list[DiscoverySignal]:
        with trace(
            name="publish_discovery_signals",
            run_type="chain",
            inputs={"recommendation_count": len(ranked)},
        ) as run:
            raw_counts = request.session_context.attributes.get(
                "product_view_counts",
                {},
            )
            view_counts = raw_counts if isinstance(raw_counts, dict) else {}
            signals: list[DiscoverySignal] = []
            for item in ranked:
                count = view_counts.get(item.product.sku, 0)
                if isinstance(count, bool) or not isinstance(count, int):
                    continue
                if (
                    count >= 3
                    and (item.product.brand_tier or "").casefold() == "premium"
                ):
                    signals.append(
                        DiscoverySignal(
                            signal_type="HIGH_PRODUCT_ENGAGEMENT",
                            sku=item.product.sku,
                            strength=min(round(0.6 + count * 0.1, 2), 1.0),
                        )
                    )
            run.end(outputs={"signal_count": len(signals)})
            return signals

    @staticmethod
    def _has_required_facts(product: Product) -> bool:
        return (
            bool(product.product_name)
            and bool(product.brand)
            and product.current_price_gbp is not None
        )

    @staticmethod
    def _to_recommendation(item: RankedProduct) -> ProductRecommendation:
        product = item.product
        return ProductRecommendation(
            sku=product.sku,
            product_name=product.product_name or product.sku,
            price_gbp=product.current_price_gbp or 0.0,
            brand=product.brand or "",
            image_url=get_product_image_url(product.sku),
            score=item.ranking.total_score,
            reason_codes=item.ranking.reason_codes,
            gender=product.gender,
            category=product.category,
            subcategory=product.subcategory,
            brand_tier=product.brand_tier,
            color=product.color,
            style=product.style,
            occasion=product.occasion,
            available_sizes=product.sizes,
        )

    @staticmethod
    def _empty_result(
        strategy: RetrievalStrategy,
        retrieved_count: int,
    ) -> DiscoveryResult:
        result = DiscoveryResult(
            status="NO_RESULTS",
            retrieval_strategy=strategy,
            recommendations=[],
            candidates_retrieved=retrieved_count,
            candidates_after_filtering=0,
        )
        DiscoveryAgent._attach_trace_metadata(result)
        return result

    @staticmethod
    def _attach_request_trace_metadata(request: DiscoveryRequest) -> None:
        current_run = get_current_run_tree()
        if current_run is not None:
            current_run.metadata.update(
                {
                    "customer_id": request.customer_context.customer_id,
                    "session_id": request.session_context.session_id,
                    "segment": request.customer_context.segment_code,
                }
            )

    @staticmethod
    def _attach_trace_metadata(result: DiscoveryResult) -> None:
        current_run = get_current_run_tree()
        if current_run is not None:
            current_run.metadata.update(
                {
                    "retrieval_strategy": result.retrieval_strategy.value,
                    "candidate_count": result.candidates_retrieved,
                    "final_result_count": len(result.recommendations),
                }
            )


__all__ = [
    "DISCOVERY_EXPLANATIONS_ENV",
    "DiscoveryAgent",
    "DiscoveryAgentError",
    "DiscoveryToolClient",
    "DiscoveryToolDiscoveryError",
    "DiscoveryToolInvocationError",
    "FastMCPDiscoveryToolClient",
]
