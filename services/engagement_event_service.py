"""Durable, inventory-aware UI product-engagement ingestion."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.dto import BehaviorEvent
from models.entities import EngagementEventReceipt, OutboxEvent
from models.upsell import UpsellTrigger
from services._date_utils import utc_now
from services.engagement_service import EngagementService
from services.inventory_service import InventoryService
from services.product_catalog_service import ProductCatalogService


class EngagementProductUnavailableError(LookupError):
    """Raised when a UI event references an inactive or out-of-stock SKU."""


class EngagementIdempotencyConflictError(RuntimeError):
    """Raised when a UI idempotency key is reused for another payload."""


@dataclass(frozen=True)
class RecordedProductView:
    receipt_id: str
    event_id: str
    engagement_count: int
    premium_product: bool
    trigger: UpsellTrigger | None
    outbox_id: str | None
    replayed: bool = False
    completed_response: dict[str, Any] | None = None


class EngagementEventService:
    """Record one view and its context-bus event in a single transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def find_receipt(
        self,
        *,
        customer_id: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> RecordedProductView | None:
        receipt = self._find_receipt(customer_id, idempotency_key)
        if receipt is None:
            return None
        self._validate_fingerprint(receipt, fingerprint)
        return self._to_result(receipt, replayed=True)

    def record_product_view(
        self,
        *,
        customer_id: str,
        session_id: str,
        sku: str,
        idempotency_key: str,
        fingerprint: str,
        trace_id: str,
        metadata: dict[str, Any],
    ) -> RecordedProductView:
        existing = self._find_receipt(customer_id, idempotency_key)
        if existing is not None:
            self._validate_fingerprint(existing, fingerprint)
            return self._to_result(existing, replayed=True)

        catalogue = ProductCatalogService(self._session)
        try:
            product = catalogue.get_product(sku)
        except LookupError as exc:
            raise EngagementProductUnavailableError(sku) from exc
        if not product.active or not InventoryService(
            self._session
        ).is_available(product.sku):
            raise EngagementProductUnavailableError(sku)

        event_id = f"EVT-{uuid4().hex}"
        now = utc_now()
        raw_source = metadata.get("source", "UI")
        source = raw_source.strip() if isinstance(raw_source, str) else "UI"
        engagement = EngagementService(self._session)
        engagement.record_behavior_event(
            BehaviorEvent(
                event_id=event_id,
                customer_id=customer_id,
                event_datetime=now,
                session_id=session_id,
                event_type="VIEW_PRODUCT",
                sku=product.sku,
                device="WEB",
                source=source or "UI",
            )
        )
        count = engagement.count_product_views(
            customer_id, session_id, product.sku
        )
        premium_product = (product.brand_tier or "").casefold() == "premium"
        trigger: UpsellTrigger | None = None
        outbox_id: str | None = None
        if premium_product and count == 3:
            trigger = UpsellTrigger(
                trigger_type="HIGH_PRODUCT_ENGAGEMENT",
                source_agent="EngagementService",
                sku=product.sku,
                strength=0.91,
                metadata={"view_count": count},
            )
            outbox_id = f"OUT-{uuid4().hex}"
            self._session.add(
                OutboxEvent(
                    outbox_id=outbox_id,
                    aggregate_type="CUSTOMER",
                    aggregate_id=customer_id,
                    event_type="HIGH_PRODUCT_ENGAGEMENT",
                    schema_version=1,
                    payload_json=json.dumps(
                        {
                            "customer_id": customer_id,
                            "session_id": session_id,
                            "sku": product.sku,
                            "engagement_event_id": event_id,
                            "view_count": count,
                            "trigger": trigger.model_dump(mode="json"),
                        },
                        sort_keys=True,
                    ),
                    trace_id=trace_id,
                    causation_id=event_id,
                    correlation_id=trace_id,
                    status="PENDING",
                    attempt_count=0,
                    created_at=now,
                )
            )
            # The receipt references this row, but there is no ORM relationship
            # for SQLAlchemy to use when ordering the two pending inserts.
            self._session.flush()

        receipt = EngagementEventReceipt(
            receipt_id=self.receipt_id(customer_id, idempotency_key),
            customer_id=customer_id,
            session_id=session_id,
            idempotency_key=idempotency_key,
            fingerprint=fingerprint,
            event_id=event_id,
            sku=product.sku,
            engagement_count=count,
            premium_product=premium_product,
            trigger_json=(
                trigger.model_dump_json() if trigger is not None else None
            ),
            outbox_id=outbox_id,
            status="PROCESSING",
            created_at=now,
            updated_at=now,
        )
        self._session.add(receipt)
        self._session.flush()
        return self._to_result(receipt, replayed=False)

    def complete(
        self, receipt_id: str, response: dict[str, Any]
    ) -> None:
        receipt = self._session.get(EngagementEventReceipt, receipt_id)
        if receipt is None:
            raise LookupError(receipt_id)
        receipt.status = "COMPLETED"
        receipt.response_json = json.dumps(
            response, sort_keys=True, default=str
        )
        receipt.updated_at = utc_now()

    @staticmethod
    def receipt_id(customer_id: str, idempotency_key: str) -> str:
        digest = hashlib.sha256(
            f"{customer_id}:{idempotency_key}".encode("utf-8")
        ).hexdigest()
        return f"ENG-{digest}"

    def _find_receipt(
        self, customer_id: str, idempotency_key: str
    ) -> EngagementEventReceipt | None:
        return self._session.scalars(
            select(EngagementEventReceipt).where(
                EngagementEventReceipt.customer_id == customer_id,
                EngagementEventReceipt.idempotency_key == idempotency_key,
            )
        ).one_or_none()

    @staticmethod
    def _validate_fingerprint(
        receipt: EngagementEventReceipt, fingerprint: str
    ) -> None:
        if receipt.fingerprint != fingerprint:
            raise EngagementIdempotencyConflictError(
                receipt.idempotency_key
            )

    @staticmethod
    def _to_result(
        receipt: EngagementEventReceipt, *, replayed: bool
    ) -> RecordedProductView:
        return RecordedProductView(
            receipt_id=receipt.receipt_id,
            event_id=receipt.event_id,
            engagement_count=receipt.engagement_count,
            premium_product=receipt.premium_product,
            trigger=(
                UpsellTrigger.model_validate_json(receipt.trigger_json)
                if receipt.trigger_json
                else None
            ),
            outbox_id=receipt.outbox_id,
            replayed=replayed,
            completed_response=(
                json.loads(receipt.response_json)
                if receipt.status == "COMPLETED" and receipt.response_json
                else None
            ),
        )


__all__ = [
    "EngagementEventService",
    "EngagementIdempotencyConflictError",
    "EngagementProductUnavailableError",
    "RecordedProductView",
]
