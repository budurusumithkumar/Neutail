from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["NEUTAIL_RESPONSE_SYNTHESIS_LLM"] = "false"
os.environ["NEUTAIL_JWT_SECRET"] = "test-only-neutail-jwt-secret-at-least-32-bytes"

from api.main import app  # noqa: E402
from llm_gateway import LLMGateway  # noqa: E402
from orchestrator import (  # noqa: E402
    NeuTailOrchestrator,
    OrchestratorCustomerNotFoundError,
    OrchestratorRequest,
)
from services.session_context_service import (  # noqa: E402
    SessionIdentityMismatchError,
)
from tools.permissions import AgentName  # noqa: E402


def _request(
    message: str,
    *,
    customer_id: str = "CUST001",
    session_id: str = "orchestrator-session",
    trace_id: str = "orchestrator-trace",
) -> OrchestratorRequest:
    return OrchestratorRequest(
        customer_id=customer_id,
        session_id=session_id,
        message=message,
        trace_id=trace_id,
    )


def _access_token(customer_id: str) -> str:
    return jwt.encode(
        {
            "sub": customer_id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        os.environ["NEUTAIL_JWT_SECRET"],
        algorithm="HS256",
    )


def test_customer_context_request_executes_profile_agent_end_to_end():
    async def scenario():
        return await NeuTailOrchestrator().handle(
            _request("Show me my profile", session_id="profile-only-session")
        )

    result = asyncio.run(scenario())

    assert result.intent == "CUSTOMER_CONTEXT"
    assert result.execution_plan == [AgentName.PROFILING]
    assert result.completed_agents == [AgentName.PROFILING]
    assert result.customer_context is not None
    assert result.customer_context.segment == "Prestige Champion"
    assert result.errors == []
    assert result.turn_count == 1


def test_multi_turn_context_avoids_reprofiling_and_keeps_entities():
    async def scenario():
        orchestrator = NeuTailOrchestrator()
        first = await orchestrator.handle(
            _request("Show me my profile", session_id="multi-turn-session")
        )
        second = await orchestrator.handle(
            _request(
                "Will size 12 fit SKU00001?",
                session_id="multi-turn-session",
                trace_id="orchestrator-trace-2",
            )
        )
        return orchestrator, first, second

    orchestrator, first, second = asyncio.run(scenario())

    assert first.completed_agents == [AgentName.PROFILING]
    assert second.intent == "FIT_QUERY"
    assert second.execution_plan == [AgentName.FIT]
    assert AgentName.PROFILING not in second.completed_agents
    assert second.extracted_entities["requested_size"] == "12"
    assert second.extracted_entities["selected_sku"] == "SKU00001"
    assert second.customer_context == first.customer_context
    assert second.errors == ["AGENT_UNAVAILABLE:fit_agent"]
    assert second.turn_count == 2
    stored = orchestrator.session_service.get_context(
        "multi-turn-session", "CUST001"
    )
    assert stored is not None
    assert stored.turn_count == 2
    assert len(stored.conversation) == 4


def test_compound_request_builds_ordered_deterministic_plan():
    async def scenario():
        return await NeuTailOrchestrator().handle(
            _request(
                "Show me a wedding dress that fits me with styling support",
                session_id="compound-session",
            )
        )

    result = asyncio.run(scenario())

    assert result.intent == "PRODUCT_DISCOVERY"
    assert result.execution_plan == [
        AgentName.PROFILING,
        AgentName.DISCOVERY,
        AgentName.FIT,
        AgentName.UPSELL,
    ]
    assert result.extracted_entities["category"] == "Dresses"
    assert result.extracted_entities["occasion"] == "Wedding"
    assert result.completed_agents == [
        AgentName.PROFILING,
        AgentName.DISCOVERY,
    ]
    assert result.discovery_result is not None
    assert result.discovery_result["status"] in {"SUCCESS", "NO_RESULTS"}


def test_ambiguous_intent_uses_llm_gateway_and_merges_entities():
    calls: list[dict] = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"intent":"PRODUCT_DISCOVERY",'
                            '"category":"Dresses","occasion":"Wedding",'
                            '"confidence":0.91}'
                        ),
                        parsed=None,
                    )
                )
            ],
            usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=5, total_tokens=15
            ),
            _hidden_params={"response_cost": 0.001},
        )

    async def scenario():
        gateway = LLMGateway(completion=fake_completion)
        orchestrator = NeuTailOrchestrator(llm_gateway=gateway)
        result = await orchestrator.handle(
            _request("Surprise me", session_id="llm-intent-session")
        )
        return result, gateway.telemetry.records

    result, records = asyncio.run(scenario())

    assert result.intent == "PRODUCT_DISCOVERY"
    assert result.extracted_entities["category"] == "Dresses"
    assert calls[0]["response_format"].__name__ == "IntentResult"
    assert records[0].use_case == "intent_detection"


def test_unknown_customer_and_cross_customer_session_are_rejected():
    async def scenario():
        orchestrator = NeuTailOrchestrator()
        with pytest.raises(OrchestratorCustomerNotFoundError):
            await orchestrator.handle(
                _request(
                    "Show me my profile",
                    customer_id="UNKNOWN",
                    session_id="unknown-session",
                )
            )
        await orchestrator.handle(
            _request("Show me my profile", session_id="owned-session")
        )
        with pytest.raises(SessionIdentityMismatchError):
            await orchestrator.handle(
                _request(
                    "Show me my profile",
                    customer_id="CUST002",
                    session_id="owned-session",
                )
            )

    asyncio.run(scenario())


def test_agent_registry_reports_profile_and_discovery_as_implemented():
    descriptors = NeuTailOrchestrator().agent_registry.list_agents()
    implemented = {item.name for item in descriptors if item.implemented}

    assert implemented == {AgentName.PROFILING, AgentName.DISCOVERY}
    assert {item.name for item in descriptors} == set(AgentName)


def test_chat_api_uses_boundary_customer_identity_and_exposes_agents():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat",
            headers={
                "authorization": f"Bearer {_access_token('CUST001')}",
                "x-request-id": "chat-trace",
            },
            json={"session_id": "api-chat-session", "message": "Show me my profile"},
        )
        missing_identity = client.post(
            "/api/v1/chat",
            json={"session_id": "missing-id-session", "message": "My profile"},
        )
        agents = client.get("/api/v1/agents")

    assert response.status_code == 200
    assert response.json()["trace_id"] == "chat-trace"
    assert response.json()["intent"] == "CUSTOMER_CONTEXT"
    assert response.json()["customer_context"]["segment"] == "Prestige Champion"
    assert missing_identity.status_code == 401
    assert {item["name"] for item in agents.json()} == {
        "profiling_agent",
        "discovery_agent",
        "fit_agent",
        "upsell_agent",
    }


def test_chat_api_returns_structured_discovery_contract():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat",
            headers={
                "authorization": f"Bearer {_access_token('CUST001')}",
                "x-request-id": "discovery-contract-trace",
            },
            json={
                "session_id": "api-discovery-session",
                "message": "Find me an elegant navy dress for a wedding under £500",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "PRODUCT_DISCOVERY"
    assert payload["completed_agents"] == [
        "profiling_agent",
        "discovery_agent",
    ]
    discovery = payload["discovery_result"]
    assert discovery["status"] in {"SUCCESS", "NO_RESULTS"}
    assert discovery["retrieval_strategy"] == "HYBRID"
    assert isinstance(discovery["recommendations"], list)
    assert isinstance(discovery["downstream_signals"], list)
    for recommendation in discovery["recommendations"]:
        assert {
            "sku",
            "product_name",
            "price_gbp",
            "brand",
            "score",
            "reason_codes",
            "explanation",
        }.issubset(recommendation)
