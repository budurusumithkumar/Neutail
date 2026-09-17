"""Application service for trusted UI product-engagement ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from models.dto import BehaviorEvent
from services._date_utils import utc_now
from services.engagement_service import EngagementService
from services.product_catalog_service import ProductCatalogService


class EngagementProductUnavailableError(LookupError):
    """Raised when a UI event references an unavailable catalogue SKU."""


@dataclass(frozen=True)
class RecordedProductView:
    event_id: str
    engagement_count: int
    premium_product: bool


class EngagementEventService:
    """Resolve catalogue facts and persist one authenticated product view."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_product_view(
        self,
        *,
        customer_id: str,
        session_id: str,
        sku: str,
        metadata: dict[str, Any],
    ) -> RecordedProductView:
        event_id = f"EVT-{uuid4().hex}"
        catalogue = ProductCatalogService(self._session)
        try:
            product = catalogue.get_product(sku)
        except LookupError as exc:
            raise EngagementProductUnavailableError(sku) from exc
        if not product.active:
            raise EngagementProductUnavailableError(sku)

        raw_source = metadata.get("source", "UI")
        source = raw_source.strip() if isinstance(raw_source, str) else "UI"
        engagement = EngagementService(self._session)
        engagement.record_behavior_event(
            BehaviorEvent(
                event_id=event_id,
                customer_id=customer_id,
                event_datetime=utc_now(),
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
        return RecordedProductView(
            event_id=event_id,
            engagement_count=count,
            premium_product=(product.brand_tier or "").casefold()
            == "premium",
        )


__all__ = [
    "EngagementEventService",
    "EngagementProductUnavailableError",
    "RecordedProductView",
]
