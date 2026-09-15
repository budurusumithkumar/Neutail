from __future__ import annotations

import os

import jwt
from fastapi.testclient import TestClient

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["NEUTAIL_JWT_SECRET"] = (
    "test-only-neutail-jwt-secret-at-least-32-bytes"
)
os.environ["NEUTAIL_DEMO_PASSWORD"] = "test-demo-password"

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


def test_login_returns_contract_identity_and_usable_access_token():
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "OLIVIA.HART1@DEMO.NEUTAIL.LOCAL",
                "password": "test-demo-password",
            },
        )
        body = login.json()
        me = client.get(
            "/api/v1/auth/me",
            headers={"authorization": f"Bearer {body['access_token']}"},
        )

    claims = jwt.decode(
        body["access_token"],
        os.environ["NEUTAIL_JWT_SECRET"],
        algorithms=["HS256"],
    )
    assert login.status_code == 200
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert body["user"] == {
        "customer_id": "CUST001",
        "display_name": "Olivia Hart",
        "email": "olivia.hart1@demo.neutail.local",
        "role": "customer",
    }
    assert claims["sub"] == "CUST001"
    assert claims["role"] == "customer"
    assert me.status_code == 200
    assert me.json() == body["user"]


def test_login_rejects_unknown_email_and_wrong_password_generically():
    with TestClient(app) as client:
        unknown_email = client.post(
            "/api/v1/auth/login",
            headers={"x-request-id": "invalid-login"},
            json={
                "email": "unknown@demo.neutail.local",
                "password": "test-demo-password",
            },
        )
        wrong_password = client.post(
            "/api/v1/auth/login",
            json={
                "email": "olivia.hart1@demo.neutail.local",
                "password": "incorrect",
            },
        )

    assert unknown_email.status_code == 401
    assert unknown_email.json() == {
        "error_code": "INVALID_CREDENTIALS",
        "message": "The email or password is incorrect",
        "trace_id": "invalid-login",
        "details": None,
    }
    assert wrong_password.status_code == 401
    assert wrong_password.json()["error_code"] == "INVALID_CREDENTIALS"


def test_logout_revokes_the_presented_access_token():
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "olivia.hart1@demo.neutail.local",
                "password": "test-demo-password",
            },
        )
        headers = {
            "authorization": f"Bearer {login.json()['access_token']}"
        }
        logout = client.post("/api/v1/auth/logout", headers=headers)
        me = client.get("/api/v1/auth/me", headers=headers)

    assert logout.status_code == 204
    assert logout.content == b""
    assert me.status_code == 401
    assert me.json()["detail"] == "The access token has been revoked"


def test_auth_openapi_contract_uses_bearer_auth():
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert schema["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "description": "Neu.Tail JWT access token",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    assert schema["paths"]["/api/v1/auth/me"]["get"]["security"] == [
        {"BearerAuth": []}
    ]
    assert schema["paths"]["/api/v1/auth/logout"]["post"]["security"] == [
        {"BearerAuth": []}
    ]
