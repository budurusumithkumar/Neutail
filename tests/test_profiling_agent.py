from __future__ import annotations

import asyncio
import os

import pytest

os.environ["LANGSMITH_TRACING"] = "false"

from agents.profiling import (  # noqa: E402
    CustomerNotFoundError,
    NeuTailSegment,
    ProfileAgent,
    ProfileAgentRequest,
    SegmentClassifier,
)
from tools.permissions import PROFILE_AGENT_TOOLS, ToolPermissionError  # noqa: E402
from tools.registry import TOOL_REGISTRY  # noqa: E402


@pytest.mark.parametrize(
    ("affluence", "loyalty", "expected"),
    [
        ("AFFLUENT", "LOYAL", NeuTailSegment.PRESTIGE_CHAMPION),
        ("LESS_AFFLUENT", "LOYAL", NeuTailSegment.VALUE_DEFENDER),
        ("Affluent", "New", NeuTailSegment.ASPIRING_LOYALIST),
        ("Less Affluent", "New", NeuTailSegment.PRICE_EXPLORER),
    ],
)
def test_segment_classifier_four_way_matrix(affluence, loyalty, expected):
    assert SegmentClassifier().classify_values(affluence, loyalty) is expected


def test_profile_tool_scope_is_exact_and_product_tools_are_denied():
    visible = {
        tool.name for tool in TOOL_REGISTRY.list_tools("profiling_agent")
    }
    assert visible == PROFILE_AGENT_TOOLS
    with pytest.raises(ToolPermissionError):
        TOOL_REGISTRY.describe_tool("product_search", "profiling_agent")


def test_agent_builds_seed_validated_customer_context():
    async def scenario():
        agent = ProfileAgent()
        expected_segments = {
            "CUST001": "Prestige Champion",
            "CUST002": "Value Defender",
            "CUST003": "Aspiring Loyalist",
            "CUST004": "Price Explorer",
        }
        for customer_id, expected in expected_segments.items():
            context = await agent.execute(
                ProfileAgentRequest(
                    customer_id=customer_id,
                    session_id=f"session-{customer_id}",
                    trace_id=f"trace-{customer_id}",
                )
            )
            assert context.segment == expected
            assert context.segment_code == expected.upper().replace(" ", "_")
            assert context.clv == context.clv_gbp
            assert context.customer_id == customer_id
            assert set(context.data_quality.values()) == {"AVAILABLE"}
            assert context.order_count_12m > 0
            assert context.profile_version == "v1"

    asyncio.run(scenario())


def test_agent_raises_for_unknown_customer():
    async def scenario():
        with pytest.raises(CustomerNotFoundError):
            await ProfileAgent().execute(
                ProfileAgentRequest(
                    customer_id="UNKNOWN",
                    session_id="session-missing",
                    trace_id="trace-missing",
                )
            )

    asyncio.run(scenario())


def test_agent_reuses_context_per_session_and_refreshes_explicitly():
    async def scenario():
        agent = ProfileAgent()
        request = ProfileAgentRequest(
            customer_id="CUST001",
            session_id="cache-session",
            trace_id="cache-trace",
        )
        first = await agent.execute(request)
        second = await agent.execute(request)
        refreshed = await agent.execute(request.model_copy(update={"refresh": True}))

        assert first == second == refreshed
        assert first is not second

    asyncio.run(scenario())
