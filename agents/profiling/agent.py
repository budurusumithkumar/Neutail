"""LangGraph workflow for deterministic, tool-driven customer profiling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any, Optional, TypedDict

from fastmcp import Client, FastMCP
from fastmcp.exceptions import ClientError, ToolError
from langgraph.graph import END, START, StateGraph
from langsmith import get_current_run_tree, trace, traceable

from agents.profiling.mapper import build_profile_facts, publish_customer_context
from agents.profiling.models import EnrichedCustomerProfileFacts, ProfileAgentRequest
from agents.profiling.segment_classifier import NeuTailSegment, SegmentClassifier
from models.dto import (
    BehaviorSummary,
    CustomerContext,
    CustomerProfileSnapshot,
    LoyaltyProfile,
    PurchaseSummary,
    ReturnSummary,
)
from tools.permissions import AgentName, PROFILE_AGENT_TOOLS
from tools.registry import create_agent_server


def _merge_dicts(left: dict[str, str], right: dict[str, str]) -> dict[str, str]:
    return {**left, **right}


class ProfileGraphState(TypedDict, total=False):
    """Internal graph state; only ``customer_context`` is published."""

    request: ProfileAgentRequest
    discovered_tools: list[str]
    snapshot: CustomerProfileSnapshot
    purchase: Optional[PurchaseSummary]
    returns: Optional[ReturnSummary]
    loyalty: Optional[LoyaltyProfile]
    engagement: Optional[BehaviorSummary]
    facts: EnrichedCustomerProfileFacts
    segment: NeuTailSegment
    customer_context: CustomerContext
    data_quality: Annotated[dict[str, str], _merge_dicts]
    source_errors: Annotated[dict[str, str], _merge_dicts]


class ProfileAgentError(RuntimeError):
    """Base exception for profile workflow failures."""


class CustomerNotFoundError(ProfileAgentError, LookupError):
    """Raised when the required customer profile does not exist."""

    def __init__(self, customer_id: str) -> None:
        self.customer_id = customer_id
        super().__init__(f"Customer '{customer_id}' was not found")


class ProfileToolDiscoveryError(ProfileAgentError):
    """Raised when the scoped FastMCP registry is missing a contract."""


class ProfileToolInvocationError(ProfileAgentError):
    """Raised when the mandatory customer profile tool fails."""


class FastMCPProfileToolClient:
    """Discover and invoke only the Profiling Agent's in-process MCP server."""

    def __init__(
        self,
        server_factory: Callable[[], FastMCP] = lambda: create_agent_server(
            AgentName.PROFILING
        ),
    ) -> None:
        self._server_factory = server_factory

    async def discover_tools(self) -> list[str]:
        with trace(
            name="profile_tool_discovery",
            run_type="tool",
            inputs={"agent": AgentName.PROFILING.value},
            tags=["fastmcp", "tool-discovery"],
        ) as run:
            async with Client(self._server_factory()) as client:
                names = sorted(tool.name for tool in await client.list_tools())
            run.end(outputs={"tools": names})
            return names

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in PROFILE_AGENT_TOOLS:
            raise ProfileToolInvocationError(
                f"Profiling Agent is not permitted to call '{tool_name}'"
            )

        with trace(
            name=tool_name,
            run_type="tool",
            inputs=arguments,
            tags=["fastmcp", "profiling-agent"],
            metadata={"agent": AgentName.PROFILING.value},
        ) as run:
            async with Client(self._server_factory()) as client:
                result = await client.call_tool(tool_name, arguments)
            payload = result.structured_content
            if payload is None:
                payload = result.data
            run.end(outputs={"result": payload})
            return payload


def _trace_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    request = inputs.get("request")
    if isinstance(request, ProfileAgentRequest):
        return {
            "customer_id": request.customer_id,
            "session_id": request.session_id,
            "trace_id": request.trace_id,
            "refresh": request.refresh,
        }
    return {"request": request}


def _trace_output(output: Any) -> dict[str, Any]:
    if isinstance(output, CustomerContext):
        return {
            "customer_id": output.customer_id,
            "segment": output.segment,
            "profile_version": output.profile_version,
            "data_quality": output.data_quality,
        }
    return {"output": output}


class ProfileAgent:
    """Build and cache a trustworthy shared ``CustomerContext``."""

    HISTORY_MONTHS = 12

    def __init__(
        self,
        tool_client: Optional[FastMCPProfileToolClient] = None,
        segment_classifier: Optional[SegmentClassifier] = None,
    ) -> None:
        self.tool_client = tool_client or FastMCPProfileToolClient()
        self.segment_classifier = segment_classifier or SegmentClassifier()
        self.graph = self._build_graph()
        self._cache: dict[tuple[str, str], CustomerContext] = {}
        self._cache_lock = asyncio.Lock()

    @traceable(
        name="profile_agent",
        run_type="chain",
        tags=["profiling-agent", "deterministic", "no-llm"],
        process_inputs=_trace_inputs,
        process_outputs=_trace_output,
    )
    async def execute(self, request: ProfileAgentRequest) -> CustomerContext:
        """Run the graph once per session, unless ``refresh`` is requested."""

        if not isinstance(request, ProfileAgentRequest):
            request = ProfileAgentRequest.model_validate(request)

        cache_key = (request.session_id, request.customer_id)
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None and not request.refresh:
            self._mark_cache_hit(True)
            return cached.model_copy(deep=True)

        self._mark_cache_hit(False)
        result = await self.graph.ainvoke(
            {
                "request": request,
                "data_quality": {},
                "source_errors": {},
            },
            {
                "run_name": "profile_agent_graph",
                "tags": ["profiling-agent", "customer-context"],
                "metadata": {
                    "customer_id": request.customer_id,
                    "session_id": request.session_id,
                    "trace_id": request.trace_id,
                    "refresh": request.refresh,
                },
            },
        )
        context = result.get("customer_context")
        if not isinstance(context, CustomerContext):
            raise ProfileAgentError("Profile graph did not publish customer context")

        async with self._cache_lock:
            self._cache[cache_key] = context.model_copy(deep=True)
        return context

    async def clear_session(self, session_id: str) -> None:
        """Invalidate cached profiles when an external customer event occurs."""

        normalized = session_id.strip()
        async with self._cache_lock:
            keys = [key for key in self._cache if key[0] == normalized]
            for key in keys:
                del self._cache[key]

    async def clear_customer(self, customer_id: str) -> None:
        """Invalidate every cached context owned by one customer."""

        normalized = customer_id.strip()
        async with self._cache_lock:
            keys = [key for key in self._cache if key[1] == normalized]
            for key in keys:
                del self._cache[key]

    def _build_graph(self) -> Any:
        builder = StateGraph(ProfileGraphState)
        builder.add_node("discover_tools", self._discover_tools)
        builder.add_node("get_customer_profile", self._get_customer_profile)
        builder.add_node("get_purchase_history", self._get_purchase_history)
        builder.add_node("get_return_history", self._get_return_history)
        builder.add_node("get_loyalty_profile", self._get_loyalty_profile)
        builder.add_node("get_engagement_summary", self._get_engagement_summary)
        builder.add_node("build_profile_facts", self._build_profile_facts)
        builder.add_node("classify_segment", self._classify_segment)
        builder.add_node("publish_customer_context", self._publish_context)

        builder.add_edge(START, "discover_tools")
        builder.add_edge("discover_tools", "get_customer_profile")
        enrichment_nodes = [
            "get_purchase_history",
            "get_return_history",
            "get_loyalty_profile",
            "get_engagement_summary",
        ]
        for node in enrichment_nodes:
            builder.add_edge("get_customer_profile", node)
        builder.add_edge(enrichment_nodes, "build_profile_facts")
        builder.add_edge("build_profile_facts", "classify_segment")
        builder.add_edge("classify_segment", "publish_customer_context")
        builder.add_edge("publish_customer_context", END)
        return builder.compile()

    async def _discover_tools(self, _state: ProfileGraphState) -> dict[str, Any]:
        names = await self.tool_client.discover_tools()
        missing = PROFILE_AGENT_TOOLS.difference(names)
        unexpected = set(names).difference(PROFILE_AGENT_TOOLS)
        if missing or unexpected:
            raise ProfileToolDiscoveryError(
                "Profiling Agent tool scope mismatch: "
                f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
            )
        return {"discovered_tools": names}

    async def _get_customer_profile(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        customer_id = state["request"].customer_id
        try:
            payload = await self.tool_client.call_tool(
                "get_customer_profile", {"customer_id": customer_id}
            )
        except (ToolError, ClientError) as exc:
            if "not found" in str(exc).casefold():
                raise CustomerNotFoundError(customer_id) from exc
            raise ProfileToolInvocationError(
                f"Unable to load required customer profile for '{customer_id}'"
            ) from exc
        return {
            "snapshot": CustomerProfileSnapshot.model_validate(payload),
            "data_quality": {"profile": "AVAILABLE"},
        }

    async def _get_purchase_history(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        payload, quality, error = await self._optional_tool(
            "get_purchase_history",
            {
                "customer_id": state["request"].customer_id,
                "months": self.HISTORY_MONTHS,
            },
        )
        update: dict[str, Any] = {
            "purchase": PurchaseSummary.model_validate(payload) if payload else None,
            "data_quality": {"purchase_history": quality},
        }
        if error:
            update["source_errors"] = {"purchase_history": error}
        return update

    async def _get_return_history(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        payload, quality, error = await self._optional_tool(
            "get_return_history",
            {
                "customer_id": state["request"].customer_id,
                "months": self.HISTORY_MONTHS,
            },
        )
        update: dict[str, Any] = {
            "returns": ReturnSummary.model_validate(payload) if payload else None,
            "data_quality": {"return_history": quality},
        }
        if error:
            update["source_errors"] = {"return_history": error}
        return update

    async def _get_loyalty_profile(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        payload, quality, error = await self._optional_tool(
            "get_loyalty_profile", {"customer_id": state["request"].customer_id}
        )
        update: dict[str, Any] = {
            "loyalty": LoyaltyProfile.model_validate(payload) if payload else None,
            "data_quality": {"loyalty": quality},
        }
        if error:
            update["source_errors"] = {"loyalty": error}
        return update

    async def _get_engagement_summary(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        payload, quality, error = await self._optional_tool(
            "get_engagement_summary",
            {"customer_id": state["request"].customer_id},
        )
        update: dict[str, Any] = {
            "engagement": BehaviorSummary.model_validate(payload) if payload else None,
            "data_quality": {"engagement": quality},
        }
        if error:
            update["source_errors"] = {"engagement": error}
        return update

    async def _optional_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> tuple[Any, str, Optional[str]]:
        try:
            return await self.tool_client.call_tool(tool_name, arguments), "AVAILABLE", None
        except (ToolError, ClientError) as exc:
            quality = "MISSING" if "not found" in str(exc).casefold() else "UNAVAILABLE"
            return None, quality, type(exc).__name__

    async def _build_profile_facts(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        return {
            "facts": build_profile_facts(
                snapshot=state["snapshot"],
                purchase=state.get("purchase"),
                returns=state.get("returns"),
                loyalty=state.get("loyalty"),
                engagement=state.get("engagement"),
            )
        }

    async def _classify_segment(
        self, state: ProfileGraphState
    ) -> dict[str, Any]:
        return {"segment": self.segment_classifier.classify(state["facts"])}

    async def _publish_context(self, state: ProfileGraphState) -> dict[str, Any]:
        return {
            "customer_context": publish_customer_context(
                snapshot=state["snapshot"],
                facts=state["facts"],
                segment=state["segment"],
                data_quality=state["data_quality"],
            )
        }

    @staticmethod
    def _mark_cache_hit(cache_hit: bool) -> None:
        current_run = get_current_run_tree()
        if current_run is not None:
            current_run.metadata["cache_hit"] = cache_hit


__all__ = [
    "CustomerNotFoundError",
    "FastMCPProfileToolClient",
    "ProfileAgent",
    "ProfileAgentError",
    "ProfileGraphState",
    "ProfileToolDiscoveryError",
    "ProfileToolInvocationError",
]
