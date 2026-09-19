from __future__ import annotations

import os
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["NEUTAIL_JWT_SECRET"] = (
    "test-only-neutail-jwt-secret-at-least-32-bytes"
)
os.environ["NEUTAIL_DEMO_PASSWORD"] = "test-demo-password"
os.environ["NEUTAIL_INTERNAL_EVENT_TOKEN"] = "test-internal-token"
os.environ["NEUTAIL_DEMO_CHECKOUT_ENABLED"] = "true"

from api.main import app  # noqa: E402
from database.session import DEFAULT_DATABASE_PATH  # noqa: E402
from models.entities import (  # noqa: E402
    Customer,
    CustomerSegmentHistory,
    EventInbox,
    Loyalty,
    LoyaltyTransaction,
    Order,
    OutboxEvent,
    OutboxDelivery,
)
from tools.runtime import configure_runtime, get_runtime  # noqa: E402


@pytest.fixture
def client(tmp_path):
    database_path = tmp_path / "profile-event.db"
    shutil.copy2(DEFAULT_DATABASE_PATH, database_path)
    configure_runtime(f"sqlite+pysqlite:///{database_path}")
    with TestClient(app) as test_client:
        yield test_client
    configure_runtime()


def _headers(client: TestClient, email: str) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "test-demo-password"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.parametrize(
    (
        "customer_id",
        "email",
        "sku",
        "size",
        "affluence_band",
        "initial_segment",
        "expected_segment",
    ),
    [
        (
            "CUST041",
            "alice.demo@demo.neutail.local",
            "SKU00008",
            "10",
            "Affluent",
            "Aspiring Loyalist",
            "Prestige Champion",
        ),
        (
            "CUST042",
            "bob.demo@demo.neutail.local",
            "SKU00002",
            "M",
            "Less Affluent",
            "Price Explorer",
            "Value Defender",
        ),
    ],
)
def test_third_purchase_promotes_demo_customer_and_publishes_transition(
    client: TestClient,
    customer_id: str,
    email: str,
    sku: str,
    size: str,
    affluence_band: str,
    initial_segment: str,
    expected_segment: str,
):
    headers = _headers(client, email)
    before = client.get("/api/v1/customers/me/summary", headers=headers)

    checkout = client.post(
        "/api/v1/demo/checkout",
        headers=headers,
        json={
            "idempotency_key": f"{customer_id}-third-purchase",
            "occurred_at": "2026-09-19T12:00:00Z",
            "items": [{"sku": sku, "quantity": 1, "size": size}],
        },
    )
    result = checkout.json()
    after = client.get("/api/v1/customers/me/summary", headers=headers)

    assert before.status_code == 200
    assert before.json()["segment"] == initial_segment
    assert before.json()["loyalty_status"] == "New"
    assert checkout.status_code == 200
    assert result["purchase_count_90d"] == 3
    assert result["profile_version"] == 2
    assert result["transition"] == {
        "previous_segment": initial_segment,
        "new_segment": expected_segment,
        "previous_loyalty_status": "New",
        "new_loyalty_status": "Loyal",
        "changed": True,
        "changed_at": "2026-09-19T12:00:00",
        "policy_version": "purchase-90d-v1",
    }
    assert result["points_transaction_id"] is None
    assert result["points_delta"] is None
    assert result["customer_context"]["segment"] == expected_segment
    assert result["customer_context"]["profile_version"] == "v2"
    assert after.status_code == 200
    assert after.json()["segment"] == expected_segment
    assert after.json()["loyalty_status"] == "Loyal"
    assert after.json()["previous_segment"] == initial_segment
    assert after.json()["purchase_count_90d"] == 3

    with get_runtime().session() as session:
        customer = session.get(Customer, customer_id)
        loyalty = session.get(Loyalty, customer_id)
        history = session.scalars(
            select(CustomerSegmentHistory).where(
                CustomerSegmentHistory.customer_id == customer_id
            )
        ).one()
        outbox = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == customer_id
            )
        ).one()
        delivery = session.get(
            OutboxDelivery,
            (outbox.outbox_id, "profile_context_projection"),
        )
        order_count = session.scalar(
            select(func.count(Order.order_id)).where(
                Order.customer_id == customer_id
            )
        )
        points_transaction_count = session.scalar(
            select(func.count(LoyaltyTransaction.loyalty_txn_id)).where(
                LoyaltyTransaction.customer_id == customer_id
            )
        )

        assert customer is not None
        assert customer.affluence_band == affluence_band
        assert customer.loyalty_status == "Loyal"
        assert customer.segment == expected_segment
        assert customer.profile_version == 2
        assert loyalty is not None
        assert loyalty.version == 2
        assert history.source_event_id == result["event_id"]
        assert outbox.event_type == "CUSTOMER_SEGMENT_CHANGED"
        assert outbox.status == "PUBLISHED"
        assert outbox.attempt_count == 1
        assert delivery is not None
        assert delivery.status == "COMPLETED"
        assert delivery.attempt_count == 1
        assert order_count == 3
        assert points_transaction_count == 0


def test_checkout_is_idempotent_and_rejects_conflicting_retry(
    client: TestClient,
):
    headers = _headers(client, "alice.demo@demo.neutail.local")
    payload = {
        "idempotency_key": "alice-idempotent-third",
        "occurred_at": "2026-09-19T12:00:00",
        "items": [{"sku": "SKU00008", "quantity": 1, "size": "10"}],
    }

    first = client.post("/api/v1/demo/checkout", headers=headers, json=payload)
    replay = client.post("/api/v1/demo/checkout", headers=headers, json=payload)
    conflict = client.post(
        "/api/v1/demo/checkout",
        headers=headers,
        json={
            **payload,
            "occurred_at": "2026-09-19T12:01:00",
        },
    )

    assert first.status_code == 200
    assert first.json()["replayed"] is False
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True
    assert replay.json()["event_id"] == first.json()["event_id"]
    assert conflict.status_code == 409

    with get_runtime().session() as session:
        assert session.scalar(
            select(func.count(Order.order_id)).where(
                Order.customer_id == "CUST041"
            )
        ) == 3
        assert session.scalar(select(func.count(EventInbox.event_id))) == 1
        assert session.scalar(
            select(func.count(CustomerSegmentHistory.segment_history_id))
        ) == 1
        assert session.scalar(select(func.count(OutboxEvent.outbox_id))) == 1


def test_internal_event_status_requires_internal_credential(
    client: TestClient,
):
    headers = _headers(client, "alice.demo@demo.neutail.local")
    checkout = client.post(
        "/api/v1/demo/checkout",
        headers=headers,
        json={
            "idempotency_key": "alice-event-status",
            "occurred_at": "2026-09-19T12:00:00",
            "items": [{"sku": "SKU00008", "quantity": 1, "size": "10"}],
        },
    )
    event_id = checkout.json()["event_id"]

    unauthorized = client.get(f"/internal/v1/events/{event_id}")
    authorized = client.get(
        f"/internal/v1/events/{event_id}",
        headers={"X-Internal-Token": "test-internal-token"},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["status"] == "COMPLETED"
    assert authorized.json()["result"]["event_id"] == event_id
