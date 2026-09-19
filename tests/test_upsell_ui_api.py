from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["NEUTAIL_RESPONSE_SYNTHESIS_LLM"] = "false"
os.environ["NEUTAIL_DISCOVERY_EXPLANATIONS_LLM"] = "false"
os.environ["NEUTAIL_JWT_SECRET"] = (
    "test-only-neutail-jwt-secret-at-least-32-bytes"
)
os.environ["NEUTAIL_DEMO_PASSWORD"] = "test-demo-password"

from api.main import app  # noqa: E402
from database.session import DEFAULT_DATABASE_PATH  # noqa: E402
from models.entities import (  # noqa: E402
    ClickstreamEvent,
    EngagementEventReceipt,
    Inventory,
    OutboxDelivery,
    OutboxEvent,
    ServiceEngagement,
    UpsellDecisionRecordEntity,
)
from tools.permissions import AgentName  # noqa: E402
from tools.runtime import configure_runtime, get_runtime  # noqa: E402


class FakeUpsellGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "Would you like to try our optional Style+ styling experience?"


@pytest.fixture
def upsell_client(tmp_path: Path):
    database_path = tmp_path / "upsell-ui.db"
    shutil.copy2(DEFAULT_DATABASE_PATH, database_path)
    configure_runtime(f"sqlite+pysqlite:///{database_path}")
    with TestClient(app) as client:
        gateway = FakeUpsellGateway()
        adapter = client.app.state.orchestrator.agent_registry._adapters[
            AgentName.UPSELL
        ]
        adapter.agent.llm_gateway = gateway
        yield client, gateway
    configure_runtime()


def _headers(
    client: TestClient,
    email: str = "olivia.hart1@demo.neutail.local",
) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "test-demo-password"},
    )
    assert login.status_code == 200
    return {
        "authorization": f"Bearer {login.json()['access_token']}"
    }


def _session(client: TestClient, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"channel": "demo"},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def _view(
    client: TestClient,
    headers: dict[str, str],
    session_id: str,
    index: int,
    *,
    sku: str = "SKU00006",
    key: str | None = None,
):
    return client.post(
        "/api/v1/engagement/events",
        headers=headers,
        json={
            "session_id": session_id,
            "event_type": "PRODUCT_VIEWED",
            "sku": sku,
            "idempotency_key": key or f"{session_id}-{sku}-view-{index}",
            "metadata": {"source": "PRODUCT_DETAIL"},
        },
    )


def _create_offer(
    client: TestClient, headers: dict[str, str], session_id: str
) -> dict[str, Any]:
    first = _view(client, headers, session_id, 1)
    second = _view(client, headers, session_id, 2)
    third = _view(client, headers, session_id, 3)
    assert first.status_code == second.status_code == third.status_code == 200
    assert first.json()["engagement_count"] == 1
    assert second.json()["engagement_count"] == 2
    assert first.json()["trigger"] is None
    assert second.json()["upsell_result"] is None
    return third.json()


def test_third_premium_view_returns_offer_and_explicit_acceptance(upsell_client):
    client, gateway = upsell_client
    headers = _headers(client)
    session_id = _session(client, headers)

    third = _create_offer(client, headers, session_id)

    assert third["engagement_count"] == 3
    assert third["trigger"] == {
        "trigger_type": "HIGH_PRODUCT_ENGAGEMENT",
        "source_agent": "EngagementService",
        "sku": "SKU00006",
        "strength": 0.91,
        "metadata": {"view_count": 3},
    }
    result = third["upsell_result"]
    assert result["status"] == "OFFER_AVAILABLE"
    assert result["should_offer"] is True
    assert result["decision_id"].startswith("UPSELL-")
    assert result["llm_invoked"] is True
    assert len(gateway.calls) == 1

    pending = client.get(
        "/api/v1/upsell/decisions/pending", headers=headers
    )
    assert pending.status_code == 200
    assert len(pending.json()) == 1
    assert pending.json()[0]["decision_id"] == result["decision_id"]
    assert pending.json()[0]["session_id"] == session_id

    with get_runtime().session() as database_session:
        outbox = database_session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "HIGH_PRODUCT_ENGAGEMENT"
            )
        ).one()
        delivery = database_session.get(
            OutboxDelivery,
            (outbox.outbox_id, "upsell_high_engagement"),
        )
        decision = database_session.get(
            UpsellDecisionRecordEntity, result["decision_id"]
        )
        assert outbox.status == "PUBLISHED"
        assert outbox.attempt_count == 1
        assert delivery is not None
        assert delivery.status == "COMPLETED"
        assert decision is not None
        assert decision.source_event_id == outbox.outbox_id

    decision_id = result["decision_id"]
    payload = {
        "session_id": session_id,
        "event_type": "OFFER_ACCEPTED",
        "idempotency_key": f"{decision_id}-OFFER_ACCEPTED",
    }
    accepted = client.post(
        f"/api/v1/upsell/decisions/{decision_id}/events",
        headers=headers,
        json=payload,
    )
    replay = client.post(
        f"/api/v1/upsell/decisions/{decision_id}/events",
        headers=headers,
        json=payload,
    )
    second_resolution = client.post(
        f"/api/v1/upsell/decisions/{decision_id}/events",
        headers=headers,
        json={
            "session_id": session_id,
            "event_type": "OFFER_DECLINED",
            "idempotency_key": f"{decision_id}-OFFER_DECLINED",
        },
    )

    assert accepted.status_code == replay.status_code == 200
    assert accepted.json() == replay.json()
    assert accepted.json()["state"] == "INTEREST_RECORDED"
    assert second_resolution.status_code == 409
    assert second_resolution.json()["error_code"] == (
        "UPSELL_DECISION_CONFLICT"
    )
    assert client.get(
        "/api/v1/upsell/decisions/pending", headers=headers
    ).json() == []
    with get_runtime().session() as database_session:
        event = database_session.scalars(
            select(ServiceEngagement).where(
                ServiceEngagement.customer_id == "CUST001",
                ServiceEngagement.outcome == "OFFER_ACCEPTED",
            )
        ).one()
    assert event.service_type == "STYLE_PLUS_TRIAL"
    assert event.outcome == "OFFER_ACCEPTED"


def test_recent_decline_suppresses_next_trigger_without_an_llm_call(upsell_client):
    client, gateway = upsell_client
    headers = _headers(client)
    first_session = _session(client, headers)
    offered = _create_offer(client, headers, first_session)["upsell_result"]
    decision_id = offered["decision_id"]

    declined = client.post(
        f"/api/v1/upsell/decisions/{decision_id}/events",
        headers=headers,
        json={
            "session_id": first_session,
            "event_type": "OFFER_DECLINED",
            "idempotency_key": f"{decision_id}-OFFER_DECLINED",
        },
    )
    second_session = _session(client, headers)
    suppressed = _create_offer(client, headers, second_session)["upsell_result"]

    assert declined.status_code == 200
    assert declined.json()["state"] == "DECLINE_RECORDED"
    assert suppressed["status"] == "NO_OFFER"
    assert suppressed["suppression_reasons"] == ["RECENT_STYLE_PLUS_DECLINE"]
    assert suppressed["llm_invoked"] is False
    assert len(gateway.calls) == 1


def test_engagement_idempotency_and_product_fact_controls(upsell_client):
    client, _gateway = upsell_client
    headers = _headers(client)
    session_id = _session(client, headers)
    key = f"{session_id}-stable-key"

    original = _view(client, headers, session_id, 1, key=key)
    replay = _view(client, headers, session_id, 1, key=key)
    conflict = _view(
        client,
        headers,
        session_id,
        1,
        sku="SKU00007",
        key=key,
    )
    unknown = _view(
        client,
        headers,
        session_id,
        2,
        sku="UNKNOWN-SKU",
    )

    assert original.status_code == replay.status_code == 200
    assert original.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_CONFLICT"
    assert unknown.status_code == 400
    assert unknown.json()["error_code"] == "PRODUCT_NOT_AVAILABLE"


def test_out_of_stock_view_is_not_counted_or_published(upsell_client):
    client, _gateway = upsell_client
    headers = _headers(client)
    session_id = _session(client, headers)
    with get_runtime().session(write=True) as database_session:
        records = database_session.scalars(
            select(Inventory).where(Inventory.sku == "SKU00006")
        ).all()
        assert records
        for record in records:
            record.available_qty = 0

    response = _view(client, headers, session_id, 1)

    assert response.status_code == 400
    assert response.json()["error_code"] == "PRODUCT_NOT_AVAILABLE"
    with get_runtime().session() as database_session:
        assert database_session.scalars(
            select(ClickstreamEvent).where(
                ClickstreamEvent.session_id == session_id,
                ClickstreamEvent.sku == "SKU00006",
            )
        ).all() == []
        assert database_session.scalars(
            select(EngagementEventReceipt).where(
                EngagementEventReceipt.session_id == session_id
            )
        ).all() == []


def test_decision_is_hidden_from_another_authenticated_customer(upsell_client):
    client, _gateway = upsell_client
    owner_headers = _headers(client)
    owner_session = _session(client, owner_headers)
    result = _create_offer(client, owner_headers, owner_session)["upsell_result"]
    other_headers = _headers(
        client, email="emma.clark2@demo.neutail.local"
    )

    response = client.post(
        f"/api/v1/upsell/decisions/{result['decision_id']}/events",
        headers=other_headers,
        json={
            "session_id": owner_session,
            "event_type": "OFFER_ACCEPTED",
            "idempotency_key": "cross-customer-attempt",
        },
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "UPSELL_DECISION_NOT_FOUND"


def test_offer_and_engagement_replay_survive_application_restart(tmp_path: Path):
    database_path = tmp_path / "upsell-restart.db"
    shutil.copy2(DEFAULT_DATABASE_PATH, database_path)
    database_url = f"sqlite+pysqlite:///{database_path}"
    configure_runtime(database_url)
    try:
        with TestClient(app) as first_client:
            first_client.app.state.orchestrator.agent_registry._adapters[
                AgentName.UPSELL
            ].agent.llm_gateway = FakeUpsellGateway()
            first_headers = _headers(first_client)
            session_id = _session(first_client, first_headers)
            third = _create_offer(first_client, first_headers, session_id)
            decision_id = third["upsell_result"]["decision_id"]

        configure_runtime(database_url)
        with TestClient(app) as restarted_client:
            restarted_headers = _headers(restarted_client)
            pending = restarted_client.get(
                "/api/v1/upsell/decisions/pending",
                headers=restarted_headers,
            )
            replay = _view(
                restarted_client,
                restarted_headers,
                session_id,
                3,
            )
            accepted = restarted_client.post(
                f"/api/v1/upsell/decisions/{decision_id}/events",
                headers=restarted_headers,
                json={
                    "session_id": session_id,
                    "event_type": "OFFER_ACCEPTED",
                    "idempotency_key": f"{decision_id}-restart-accept",
                },
            )

            assert pending.status_code == 200
            assert pending.json()[0]["decision_id"] == decision_id
            assert replay.status_code == 200
            assert replay.json() == third
            assert accepted.status_code == 200
            assert accepted.json()["state"] == "INTEREST_RECORDED"
    finally:
        configure_runtime()


def test_upsell_ui_openapi_contract(upsell_client):
    client, _gateway = upsell_client
    schema = client.get("/openapi.json").json()

    engagement = schema["paths"]["/api/v1/engagement/events"]["post"]
    decision = schema["paths"][
        "/api/v1/upsell/decisions/{decision_id}/events"
    ]["post"]
    pending = schema["paths"][
        "/api/v1/upsell/decisions/pending"
    ]["get"]
    chat = schema["paths"]["/api/v1/chat"]["post"]

    assert engagement["operationId"] == "recordEngagementEvent"
    assert decision["operationId"] == "recordUpsellDecisionEvent"
    assert pending["operationId"] == "listPendingUpsellDecisions"
    assert chat["operationId"] == "chatWithUpsellResult"
    assert engagement["security"] == decision["security"] == pending[
        "security"
    ] == [
        {"BearerAuth": []}
    ]
    required = schema["components"]["schemas"]["UpsellResult"][
        "required"
    ]
    assert {"decision_id", "trigger", "llm_invoked"}.issubset(required)


def test_chat_serializes_typed_no_offer_result(upsell_client):
    client, gateway = upsell_client
    headers = _headers(client)
    session_id = _session(client, headers)

    response = client.post(
        "/api/v1/chat",
        headers=headers,
        json={
            "session_id": session_id,
            "message": "Can I get styling support?",
        },
    )

    assert response.status_code == 200
    result = response.json()["upsell_result"]
    assert result["status"] == "NO_OFFER"
    assert result["trigger"]["trigger_type"] == "STYLING_ENGAGEMENT"
    assert result["llm_invoked"] is False
    assert gateway.calls == []
