"""Trusted commerce events and customer-authenticated demo checkout routes."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from api.auth import get_authenticated_customer_id
from models.events import (
    DemoCheckoutRequest,
    EventStatusResponse,
    PurchaseCompletedData,
    PurchaseCompletedEvent,
    PurchaseEventItem,
    PurchaseEventResult,
)
from orchestrator.purchase_event_graph import PurchaseEventGraph
from services.commerce_event_service import (
    CommerceEventService,
    EventAlreadyProcessingError,
    EventCustomerNotFoundError,
    EventIdempotencyConflictError,
    EventProductNotFoundError,
)
from tools.runtime import get_runtime


INTERNAL_EVENT_TOKEN_ENV = "NEUTAIL_INTERNAL_EVENT_TOKEN"
DEMO_CHECKOUT_ENABLED_ENV = "NEUTAIL_DEMO_CHECKOUT_ENABLED"

internal_router = APIRouter(prefix="/internal/v1/events", tags=["Internal Events"])
demo_router = APIRouter(prefix="/api/v1/demo", tags=["Demo"])


def _purchase_graph(request: Request) -> PurchaseEventGraph:
    return request.app.state.purchase_event_graph


def _require_internal_token(
    token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    configured = os.getenv(INTERNAL_EVENT_TOKEN_ENV)
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal event authentication is not configured",
        )
    if token is None or not hmac.compare_digest(token, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal event credential",
        )


def _translate_event_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (EventIdempotencyConflictError, EventAlreadyProcessingError)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, EventCustomerNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, EventProductNotFoundError):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Purchase event processing is unavailable",
    )


@internal_router.post(
    "",
    response_model=PurchaseEventResult,
    operation_id="processCommerceEvent",
    dependencies=[Depends(_require_internal_token)],
)
async def process_commerce_event(
    event: PurchaseCompletedEvent,
    request: Request,
    graph: Annotated[PurchaseEventGraph, Depends(_purchase_graph)],
) -> PurchaseEventResult:
    try:
        return await graph.execute(event, trace_id=request.state.request_id)
    except Exception as exc:
        raise _translate_event_error(exc) from exc


@internal_router.get(
    "/{event_id}",
    response_model=EventStatusResponse,
    operation_id="getCommerceEventStatus",
    dependencies=[Depends(_require_internal_token)],
)
async def get_commerce_event_status(event_id: str) -> EventStatusResponse:
    with get_runtime().session() as session:
        event = CommerceEventService(session).get_status(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event was not found")
    return event


@demo_router.post(
    "/checkout",
    response_model=PurchaseEventResult,
    operation_id="completeDemoCheckout",
)
async def complete_demo_checkout(
    payload: DemoCheckoutRequest,
    request: Request,
    customer_id: Annotated[str, Depends(get_authenticated_customer_id)],
    graph: Annotated[PurchaseEventGraph, Depends(_purchase_graph)],
) -> PurchaseEventResult:
    if os.getenv(DEMO_CHECKOUT_ENABLED_ENV, "true").casefold() != "true":
        raise HTTPException(status_code=404, detail="Demo checkout is disabled")
    digest = hashlib.sha256(
        f"{customer_id}:{payload.idempotency_key}".encode("utf-8")
    ).hexdigest()[:20]
    order_id = f"DEMO-ORD-{digest}"
    event = PurchaseCompletedEvent(
        event_id=f"demo-purchase-{digest}",
        occurred_at=payload.occurred_at,
        customer_id=customer_id,
        data=PurchaseCompletedData(
            order_id=order_id,
            items=[
                PurchaseEventItem(
                    order_item_id=f"{order_id}-ITEM-{index}",
                    sku=item.sku,
                    quantity=item.quantity,
                    size=item.size,
                    color=item.color,
                )
                for index, item in enumerate(payload.items, start=1)
            ],
        ),
    )
    try:
        return await graph.execute(event, trace_id=request.state.request_id)
    except Exception as exc:
        raise _translate_event_error(exc) from exc


__all__ = [
    "DEMO_CHECKOUT_ENABLED_ENV",
    "INTERNAL_EVENT_TOKEN_ENV",
    "complete_demo_checkout",
    "demo_router",
    "internal_router",
    "process_commerce_event",
]
