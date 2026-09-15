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


def _login_headers(
    client: TestClient,
    email: str = "olivia.hart1@demo.neutail.local",
) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "test-demo-password"},
    )
    assert response.status_code == 200
    return {
        "authorization": f"Bearer {response.json()['access_token']}"
    }


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


def test_local_ui_cors_preflight_and_response_headers():
    origin = "http://localhost:5173"
    with TestClient(app) as client:
        preflight = client.options(
            "/api/v1/auth/login",
            headers={
                "origin": origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": (
                    "authorization,content-type,x-request-id"
                ),
            },
        )
        health = client.get("/health", headers={"origin": origin})

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "authorization" in preflight.headers[
        "access-control-allow-headers"
    ].casefold()
    assert health.headers["access-control-allow-origin"] == origin
    assert health.headers["access-control-expose-headers"] == (
        "X-Request-ID, X-Process-Time-Ms"
    )


def test_unknown_ui_origin_is_not_allowed_by_cors():
    with TestClient(app) as client:
        response = client.get(
            "/health",
            headers={"origin": "https://untrusted.example"},
        )

    assert "access-control-allow-origin" not in response.headers


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


def test_authenticated_customer_summary_matches_ui_contract():
    with TestClient(app) as client:
        headers = _login_headers(client)
        response = client.get(
            "/api/v1/customers/me/summary",
            headers=headers,
        )
        unauthorized = client.get("/api/v1/customers/me/summary")
        operation = client.get("/openapi.json").json()["paths"][
            "/api/v1/customers/me/summary"
        ]["get"]

    assert response.status_code == 200
    assert response.json() == {
        "customer_id": "CUST001",
        "display_name": "Olivia Hart",
        "city": "London",
        "segment": "Prestige Champion",
        "loyalty_tier": "Platinum",
        "points_balance": 16890,
        "preferred_categories": ["Dresses", "Outerwear"],
        "preferred_colors": ["Navy", "Burgundy", "Black"],
        "preferred_styles": ["Classic", "Tailored"],
        "usual_size": "12",
        "fit_preference": "Tailored",
    }
    assert unauthorized.status_code == 401
    assert operation["operationId"] == "getCustomerSummary"
    assert operation["security"] == [{"BearerAuth": []}]
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ] == {"$ref": "#/components/schemas/CustomerSummary"}


def test_authenticated_session_lifecycle_and_sanitized_context():
    with TestClient(app) as client:
        headers = _login_headers(client)
        created = client.post("/api/v1/sessions", headers=headers)
        session_id = created.json()["session_id"]
        fetched = client.get(
            f"/api/v1/sessions/{session_id}", headers=headers
        )
        initial_context = client.get(
            f"/api/v1/sessions/{session_id}/context", headers=headers
        )
        chat = client.post(
            "/api/v1/chat",
            headers=headers,
            json={
                "session_id": session_id,
                "message": "Show me my profile",
            },
        )
        updated_context = client.get(
            f"/api/v1/sessions/{session_id}/context", headers=headers
        )
        closed = client.delete(
            f"/api/v1/sessions/{session_id}", headers=headers
        )
        missing = client.get(
            f"/api/v1/sessions/{session_id}", headers=headers
        )

    assert created.status_code == 201
    assert created.json()["session_id"].startswith("S")
    assert created.json()["customer_id"] == "CUST001"
    assert created.json()["status"] == "ACTIVE"
    assert created.json()["turn_count"] == 0
    assert created.json()["created_at"]
    assert fetched.status_code == 200
    assert fetched.json() == created.json()
    assert initial_context.status_code == 200
    assert initial_context.json() == {
        "session_id": session_id,
        "customer_id": "CUST001",
        "current_intent": None,
        "occasion": None,
        "category": None,
        "selected_sku": None,
        "requested_size": None,
        "last_agent": None,
        "turn_count": 0,
        "attributes": {"channel": "web"},
    }
    assert chat.status_code == 200
    assert updated_context.status_code == 200
    assert updated_context.json()["current_intent"] == "CUSTOMER_CONTEXT"
    assert updated_context.json()["last_agent"] == "profiling_agent"
    assert updated_context.json()["turn_count"] == 1
    assert "conversation" not in updated_context.json()
    assert "customer_context" not in updated_context.json()
    assert closed.status_code == 204
    assert closed.content == b""
    assert missing.status_code == 404


def test_sessions_require_auth_and_hide_other_customer_sessions():
    with TestClient(app) as client:
        owner_headers = _login_headers(client)
        other_headers = _login_headers(
            client,
            email="emma.clark2@demo.neutail.local",
        )
        unauthorized = client.post("/api/v1/sessions")
        created = client.post(
            "/api/v1/sessions",
            headers=owner_headers,
            json={"channel": "mobile"},
        )
        session_id = created.json()["session_id"]
        other_customer_get = client.get(
            f"/api/v1/sessions/{session_id}", headers=other_headers
        )
        other_customer_close = client.delete(
            f"/api/v1/sessions/{session_id}", headers=other_headers
        )
        owner_context = client.get(
            f"/api/v1/sessions/{session_id}/context", headers=owner_headers
        )

    assert unauthorized.status_code == 401
    assert other_customer_get.status_code == 404
    assert other_customer_close.status_code == 404
    assert owner_context.status_code == 200
    assert owner_context.json()["attributes"] == {"channel": "mobile"}


def test_session_openapi_contract_operations_use_bearer_auth():
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    operations = {
        ("/api/v1/sessions", "post"): ("createSession", "201"),
        ("/api/v1/sessions/{session_id}", "get"): (
            "getSession",
            "200",
        ),
        ("/api/v1/sessions/{session_id}", "delete"): (
            "closeSession",
            "204",
        ),
        ("/api/v1/sessions/{session_id}/context", "get"): (
            "getSessionContext",
            "200",
        ),
    }
    for (path, method), (operation_id, success_status) in operations.items():
        operation = schema["paths"][path][method]
        assert operation["operationId"] == operation_id
        assert success_status in operation["responses"]
        assert operation["security"] == [{"BearerAuth": []}]

    request_body = schema["paths"]["/api/v1/sessions"]["post"][
        "requestBody"
    ]
    assert request_body.get("required", False) is False
