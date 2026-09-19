from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["NEUTAIL_DISCOVERY_EXPLANATIONS_LLM"] = "false"
os.environ["NEUTAIL_JWT_SECRET"] = (
    "test-only-neutail-jwt-secret-at-least-32-bytes"
)
os.environ["NEUTAIL_DEMO_PASSWORD"] = "test-demo-password"

from api.main import app  # noqa: E402
from database.session import DEFAULT_DATABASE_PATH  # noqa: E402
from orchestrator import OrchestratorDependencyError  # noqa: E402
from tools.runtime import configure_runtime  # noqa: E402


@pytest.fixture
def recommendation_client(tmp_path: Path):
    database_path = tmp_path / "home-recommendations.db"
    shutil.copy2(DEFAULT_DATABASE_PATH, database_path)
    configure_runtime(f"sqlite+pysqlite:///{database_path}")
    with TestClient(app) as client:
        yield client
    configure_runtime()


def _headers(client: TestClient) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "olivia.hart1@demo.neutail.local",
            "password": "test-demo-password",
        },
    )
    assert login.status_code == 200
    return {"authorization": f"Bearer {login.json()['access_token']}"}


def test_home_recommendations_are_personalized_available_and_limited(
    recommendation_client: TestClient,
):
    response = recommendation_client.get(
        "/api/v1/recommendations/home?limit=3",
        headers=_headers(recommendation_client),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation_id"].startswith("HOME-")
    assert body["trace_id"]
    assert body["status"] == "SUCCESS"
    assert body["strategy"] in {
        "CATEGORY_AFFINITY",
        "PROFILE_PREFERENCE",
        "PROFILE_PERSONALIZATION",
    }
    assert body["categories_used"]

    products = [
        product
        for section in body["sections"]
        for product in section["products"]
    ]
    assert 1 <= len(products) <= 3
    assert len({product["sku"] for product in products}) == len(products)
    assert all(product["available"] is True for product in products)
    assert all("IN_STOCK" in product["reason_codes"] for product in products)
    assert all(
        product["image_url"] == f"/products/{product['sku']}.webp"
        for product in products
    )
    assert all(
        product["category"] in body["categories_used"]
        for product in products
    )


def test_home_recommendations_require_authentication(
    recommendation_client: TestClient,
):
    response = recommendation_client.get("/api/v1/recommendations/home")

    assert response.status_code == 401


def test_home_recommendation_dependency_failure_uses_ui_error_contract(
    recommendation_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    async def fail_recommendations(**_kwargs):
        raise OrchestratorDependencyError("internal dependency detail")

    monkeypatch.setattr(
        recommendation_client.app.state.orchestrator,
        "handle_home_recommendations",
        fail_recommendations,
    )
    response = recommendation_client.get(
        "/api/v1/recommendations/home",
        headers=_headers(recommendation_client),
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "HOME_RECOMMENDATIONS_UNAVAILABLE"
    assert response.json()["trace_id"]
    assert "internal dependency detail" not in response.text


def test_home_recommendations_openapi_contract(
    recommendation_client: TestClient,
):
    operation = recommendation_client.get("/openapi.json").json()["paths"][
        "/api/v1/recommendations/home"
    ]["get"]

    assert operation["operationId"] == "getHomeRecommendations"
    assert operation["security"] == [{"BearerAuth": []}]
