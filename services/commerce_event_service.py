"""Transactional processing for trusted purchase-completed events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from sqlalchemy.orm import Session

from models.dto import Product as ProductDTO
from models.entities import (
    Customer,
    EventInbox,
    Loyalty,
    Order,
    OrderItem,
    Product,
)
from models.events import (
    EventStatusResponse,
    PurchaseCompletedEvent,
    PurchaseEventResult,
)
from repositories.event_repository import EventRepository
from services._date_utils import utc_now
from services.customer_segmentation_service import (
    CustomerSegmentationPolicy,
    CustomerSegmentationService,
)
from services.order_history_service import OrderHistoryService


class CommerceEventError(RuntimeError):
    """Base trusted-event processing error."""


class EventIdempotencyConflictError(CommerceEventError):
    """Raised when an event ID is reused with a different payload."""


class EventAlreadyProcessingError(CommerceEventError):
    """Raised when the claimed event has not reached a terminal state."""


class EventCustomerNotFoundError(CommerceEventError, LookupError):
    """Raised when an event references an unknown customer."""


class EventProductNotFoundError(CommerceEventError, LookupError):
    """Raised when a purchase line references an unavailable product."""


class CommerceEventService:
    """Own the atomic order, segment-history, inbox, and outbox write."""

    def __init__(
        self,
        session: Session,
        *,
        policy: CustomerSegmentationPolicy | None = None,
    ) -> None:
        self._session = session
        self._repository = EventRepository(session)
        self.policy = policy or CustomerSegmentationPolicy.from_environment()

    def process_purchase(
        self,
        event: PurchaseCompletedEvent,
        *,
        trace_id: str,
    ) -> PurchaseEventResult:
        canonical_payload = json.dumps(
            event.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        existing = self._repository.get_inbox(event.event_id)
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise EventIdempotencyConflictError(
                    f"Event ID '{event.event_id}' was reused with a different payload"
                )
            if existing.status == "COMPLETED" and existing.result_json:
                return PurchaseEventResult.model_validate_json(
                    existing.result_json
                ).model_copy(update={"replayed": True})
            raise EventAlreadyProcessingError(
                f"Event '{event.event_id}' is already being processed"
            )

        received_at = utc_now()
        inbox = EventInbox(
            event_id=event.event_id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            payload_json=canonical_payload,
            payload_hash=payload_hash,
            status="PROCESSING",
            attempt_count=1,
            received_at=received_at,
            updated_at=received_at,
        )
        self._repository.add_inbox(inbox)

        customer = self._session.get(Customer, event.customer_id)
        if customer is None:
            raise EventCustomerNotFoundError(event.customer_id)
        if self._session.get(Order, event.data.order_id) is not None:
            raise EventIdempotencyConflictError(
                f"Order '{event.data.order_id}' already exists"
            )

        order, total = self._build_order(event)
        self._session.add(order)
        self._session.flush()

        purchase_count = OrderHistoryService(
            self._session
        ).count_completed_purchases(
            event.customer_id,
            as_of=event.occurred_at,
            window_days=self.policy.purchase_window_days,
        )
        segmentation = CustomerSegmentationService(
            self._session,
            policy=self.policy,
        ).apply_purchase_policy(
            customer=customer,
            purchase_count=purchase_count,
            source_event_id=event.event_id,
            trace_id=trace_id,
            changed_at=event.occurred_at,
        )
        loyalty = self._session.get(Loyalty, event.customer_id)
        result = PurchaseEventResult(
            event_id=event.event_id,
            customer_id=event.customer_id,
            order_id=event.data.order_id,
            purchase_count_90d=purchase_count,
            profile_version=segmentation.profile_version,
            transition=segmentation.transition,
            points_balance=loyalty.points_balance if loyalty else None,
            outbox_ids=segmentation.outbox_ids,
        )
        inbox.status = "COMPLETED"
        inbox.result_json = result.model_dump_json()
        inbox.updated_at = utc_now()
        return result

    def get_status(self, event_id: str) -> EventStatusResponse | None:
        inbox = self._repository.get_inbox(event_id.strip())
        if inbox is None:
            return None
        result = (
            PurchaseEventResult.model_validate_json(inbox.result_json)
            if inbox.result_json
            else None
        )
        return EventStatusResponse(
            event_id=inbox.event_id,
            event_type=inbox.event_type,
            status=inbox.status,
            attempt_count=inbox.attempt_count,
            result=result,
            error_code=inbox.error_code,
            received_at=inbox.received_at,
            updated_at=inbox.updated_at,
        )

    def _build_order(
        self, event: PurchaseCompletedEvent
    ) -> tuple[Order, float]:
        items: list[OrderItem] = []
        total = 0.0
        for line in event.data.items:
            product = self._session.get(Product, line.sku)
            if product is None or not product.active:
                raise EventProductNotFoundError(line.sku)
            product_dto = ProductDTO.model_validate(product)
            unit_price = product_dto.current_price_gbp or 0.0
            line_total = round(unit_price * line.quantity, 2)
            total += line_total
            items.append(
                OrderItem(
                    order_item_id=line.order_item_id,
                    order_id=event.data.order_id,
                    sku=line.sku,
                    quantity=line.quantity,
                    unit_price_gbp=unit_price,
                    size=line.size,
                    color=line.color or product.color,
                    line_total_gbp=line_total,
                )
            )
        total = round(total, 2)
        return (
            Order(
                order_id=event.data.order_id,
                customer_id=event.customer_id,
                order_datetime=event.occurred_at,
                channel=event.data.channel,
                subtotal_gbp=total,
                shipping_gbp=0.0,
                total_gbp=total,
                payment_type="DEMO",
                status=event.data.status,
                source_event_id=event.event_id,
                items=items,
            ),
            total,
        )


__all__ = [
    "CommerceEventError",
    "CommerceEventService",
    "EventAlreadyProcessingError",
    "EventCustomerNotFoundError",
    "EventIdempotencyConflictError",
    "EventProductNotFoundError",
]
