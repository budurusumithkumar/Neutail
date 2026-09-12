"""Customer behavior and styling-service engagement capability."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from models.dto import BehaviorEvent, BehaviorSummary, ServiceEngagement
from models.entities import ClickstreamEvent
from models.entities import ServiceEngagement as ServiceEngagementEntity
from services._date_utils import sqlite_datetime, utc_now, validate_positive_int


class EngagementService:
    """Expose behavioral facts and persist explicit service interactions."""

    SUMMARY_DAYS = 30
    HIGH_INTENT_EVENT_TYPES = frozenset(
        {"ADD_TO_CART", "CHECKOUT_START", "PURCHASE"}
    )

    def __init__(
        self,
        session: Session,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._session = session
        self._clock = clock or utc_now

    def get_recent_behavior(
        self, customer_id: str, days: int = 30
    ) -> list[BehaviorEvent]:
        """Return recent clickstream behavior, newest first."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        validate_positive_int(days, "days")
        cutoff = self._clock() - timedelta(days=days)
        statement = (
            select(ClickstreamEvent)
            .where(
                ClickstreamEvent.customer_id == normalized_customer_id,
                func.datetime(ClickstreamEvent.event_datetime)
                >= sqlite_datetime(cutoff),
                func.datetime(ClickstreamEvent.event_datetime)
                <= sqlite_datetime(self._clock()),
            )
            .order_by(
                ClickstreamEvent.event_datetime.desc(),
                ClickstreamEvent.event_id.desc(),
            )
        )
        return [
            BehaviorEvent.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def get_behavior_summary(
        self, customer_id: str, session_id: Optional[str] = None
    ) -> BehaviorSummary:
        """Aggregate engagement signals for one session or the last 30 days."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        filters = [ClickstreamEvent.customer_id == normalized_customer_id]
        if session_id is not None:
            normalized_session_id = self._normalize_identifier(
                session_id, "session_id"
            )
            filters.append(ClickstreamEvent.session_id == normalized_session_id)
        else:
            filters.append(
                func.datetime(ClickstreamEvent.event_datetime)
                >= sqlite_datetime(
                    self._clock() - timedelta(days=self.SUMMARY_DAYS)
                )
            )
            filters.append(
                func.datetime(ClickstreamEvent.event_datetime)
                <= sqlite_datetime(self._clock())
            )

        high_intent_case = case(
            (ClickstreamEvent.event_type.in_(self.HIGH_INTENT_EVENT_TYPES), 1),
            else_=0,
        )
        statement = select(
            func.count(func.distinct(ClickstreamEvent.session_id)),
            func.coalesce(
                func.sum(case((ClickstreamEvent.event_type == "VIEW_PRODUCT", 1), else_=0)),
                0,
            ),
            func.coalesce(
                func.sum(case((ClickstreamEvent.event_type == "SEARCH", 1), else_=0)),
                0,
            ),
            func.coalesce(func.avg(ClickstreamEvent.dwell_seconds), 0.0),
            func.coalesce(func.sum(high_intent_case), 0),
            func.count(ClickstreamEvent.event_id),
        ).where(*filters)
        row = self._session.execute(statement).one()

        session_count = int(row[0] or 0)
        product_views = int(row[1] or 0)
        searches = int(row[2] or 0)
        avg_dwell_seconds = round(float(row[3] or 0), 2)
        high_intent_events = int(row[4] or 0)
        event_count = int(row[5] or 0)

        volume_component = min(event_count / 20, 1.0) * 0.25
        intent_points = product_views + searches * 1.25 + high_intent_events * 3
        intent_component = min(intent_points / 30, 1.0) * 0.55
        dwell_component = min(avg_dwell_seconds / 120, 1.0) * 0.20
        engagement_score = round(
            min(volume_component + intent_component + dwell_component, 1.0),
            3,
        )

        return BehaviorSummary(
            session_count=session_count,
            product_views=product_views,
            searches=searches,
            avg_dwell_seconds=avg_dwell_seconds,
            high_intent_events=high_intent_events,
            engagement_score=engagement_score,
        )

    def get_service_engagement(
        self, customer_id: str, days: int = 90
    ) -> list[ServiceEngagement]:
        """Return recent styling and service interactions, newest first."""

        normalized_customer_id = self._normalize_identifier(
            customer_id, "customer_id"
        )
        validate_positive_int(days, "days")
        cutoff = self._clock() - timedelta(days=days)
        statement = (
            select(ServiceEngagementEntity)
            .where(
                ServiceEngagementEntity.customer_id == normalized_customer_id,
                func.datetime(ServiceEngagementEntity.event_datetime)
                >= sqlite_datetime(cutoff),
                func.datetime(ServiceEngagementEntity.event_datetime)
                <= sqlite_datetime(self._clock()),
            )
            .order_by(
                ServiceEngagementEntity.event_datetime.desc(),
                ServiceEngagementEntity.engagement_id.desc(),
            )
        )
        return [
            ServiceEngagement.model_validate(entity)
            for entity in self._session.scalars(statement).all()
        ]

    def record_service_event(
        self, event: ServiceEngagement
    ) -> ServiceEngagement:
        """Stage and flush a service event in the caller-owned transaction."""

        if not isinstance(event, ServiceEngagement):
            event = ServiceEngagement.model_validate(event)
        entity = ServiceEngagementEntity(**event.model_dump())
        self._session.add(entity)
        self._session.flush()
        return ServiceEngagement.model_validate(entity)

    @staticmethod
    def _normalize_identifier(value: Any, field_name: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise ValueError(f"{field_name} must be a non-empty string")


__all__ = ["EngagementService"]
