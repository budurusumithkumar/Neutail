"""Typed purchase-event, processing, and demo-checkout contracts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import Field, field_validator

from models.dto import CustomerContext, DTOModel


class PurchaseEventItem(DTOModel):
    order_item_id: str = Field(min_length=1, max_length=128)
    sku: str = Field(min_length=1, max_length=128)
    quantity: int = Field(default=1, ge=1, le=20)
    size: Optional[str] = Field(default=None, max_length=32)
    color: Optional[str] = Field(default=None, max_length=64)


class PurchaseCompletedData(DTOModel):
    order_id: str = Field(min_length=1, max_length=128)
    channel: str = Field(default="WEB", min_length=1, max_length=32)
    status: Literal["COMPLETED"] = "COMPLETED"
    items: list[PurchaseEventItem] = Field(min_length=1, max_length=50)


class PurchaseCompletedEvent(DTOModel):
    event_id: str = Field(min_length=1, max_length=160)
    event_type: Literal["PURCHASE_COMPLETED"] = "PURCHASE_COMPLETED"
    occurred_at: datetime
    schema_version: Literal[1] = 1
    customer_id: str = Field(min_length=1, max_length=128)
    data: PurchaseCompletedData

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        """Store event time as naive UTC to match the SQLite demo schema."""

        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)


class SegmentTransition(DTOModel):
    previous_segment: Optional[str] = None
    new_segment: str
    previous_loyalty_status: Optional[str] = None
    new_loyalty_status: str
    changed: bool
    changed_at: datetime
    policy_version: str


class PurchaseEventResult(DTOModel):
    event_id: str
    event_type: Literal["PURCHASE_COMPLETED"] = "PURCHASE_COMPLETED"
    status: Literal["COMPLETED"] = "COMPLETED"
    replayed: bool = False
    customer_id: str
    order_id: str
    purchase_count_90d: int = Field(ge=0)
    profile_version: int = Field(ge=1)
    transition: SegmentTransition
    points_transaction_id: Optional[str] = None
    points_delta: Optional[int] = None
    points_balance: Optional[int] = None
    customer_context: Optional[CustomerContext] = None
    outbox_ids: list[str] = Field(default_factory=list)


class EventStatusResponse(DTOModel):
    event_id: str
    event_type: str
    status: Literal["PROCESSING", "COMPLETED", "FAILED"]
    attempt_count: int = Field(ge=1)
    result: Optional[PurchaseEventResult] = None
    error_code: Optional[str] = None
    received_at: datetime
    updated_at: datetime


class DemoCheckoutItem(DTOModel):
    sku: str = Field(min_length=1, max_length=128)
    quantity: int = Field(default=1, ge=1, le=20)
    size: Optional[str] = Field(default=None, max_length=32)
    color: Optional[str] = Field(default=None, max_length=64)


class DemoCheckoutRequest(DTOModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    occurred_at: datetime
    items: list[DemoCheckoutItem] = Field(min_length=1, max_length=20)


__all__ = [
    "DemoCheckoutItem",
    "DemoCheckoutRequest",
    "EventStatusResponse",
    "PurchaseCompletedData",
    "PurchaseCompletedEvent",
    "PurchaseEventItem",
    "PurchaseEventResult",
    "SegmentTransition",
]
