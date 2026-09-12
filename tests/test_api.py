from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["LANGSMITH_TRACING"] = "false"

from api.main import app  # noqa: E402


def test_health_and_profile_tool_discovery():
    with TestClient(app) as client:
        health = client.get("/health")
        tools = client.get("/api/v1/agents/profiling/tools")

    assert health.status_code == 200
    assert health.json()["profiling_agent"] == "ready"
    assert health.json()["langsmith_tracing"] is False
    assert {tool["name"] for tool in tools.json()} == {
        "get_customer_profile",
        "get_purchase_history",
        "get_return_history",
        "get_loyalty_profile",
        "get_engagement_summary",
    }


def test_customer_profile_get_and_post_endpoints():
    payload = {
        "customer_id": "CUST001",
        "session_id": "api-session",
        "trace_id": "api-trace",
    }
    with TestClient(app) as client:
        get_response = client.get(
            "/api/v1/customers/CUST001/profile",
            params={"session_id": "api-session"},
        )
        post_response = client.post("/api/v1/profile", json=payload)

    assert get_response.status_code == 200
    assert post_response.status_code == 200
    assert get_response.headers["x-request-id"]
    assert get_response.json()["segment"] == "Prestige Champion"
    assert post_response.json()["customer_id"] == "CUST001"


def test_unknown_customer_is_404():
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/customers/UNKNOWN/profile",
            params={"session_id": "api-session"},
        )

    assert response.status_code == 404
    assert response.json()["error_code"] == "CUSTOMER_NOT_FOUND"

