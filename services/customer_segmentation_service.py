"""Versioned customer-loyalty threshold and segment transition policy."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from agents.profiling.segment_classifier import SegmentClassifier
from models.entities import (
    Customer,
    CustomerSegmentHistory,
    Loyalty,
    OutboxEvent,
)
from models.events import SegmentTransition


PURCHASE_WINDOW_DAYS_ENV = "NEUTAIL_PURCHASE_WINDOW_DAYS"
LOYAL_PURCHASE_THRESHOLD_ENV = "NEUTAIL_LOYAL_PURCHASE_THRESHOLD"
LOYALTY_POLICY_VERSION_ENV = "NEUTAIL_LOYALTY_POLICY_VERSION"


@dataclass(frozen=True)
class CustomerSegmentationPolicy:
    purchase_window_days: int = 90
    loyal_purchase_threshold: int = 3
    version: str = "purchase-90d-v1"

    @classmethod
    def from_environment(cls) -> "CustomerSegmentationPolicy":
        try:
            window = int(os.getenv(PURCHASE_WINDOW_DAYS_ENV, "90"))
            threshold = int(os.getenv(LOYAL_PURCHASE_THRESHOLD_ENV, "3"))
        except ValueError as exc:
            raise ValueError("Purchase policy settings must be integers") from exc
        if window <= 0 or threshold <= 0:
            raise ValueError("Purchase policy settings must be positive")
        version = os.getenv(LOYALTY_POLICY_VERSION_ENV, "purchase-90d-v1").strip()
        if not version:
            raise ValueError("Loyalty policy version cannot be blank")
        return cls(window, threshold, version)


@dataclass(frozen=True)
class SegmentationOutcome:
    transition: SegmentTransition
    profile_version: int
    outbox_ids: list[str]


class CustomerSegmentationService:
    """Apply loyalty threshold and persist the derived segment atomically."""

    def __init__(
        self,
        session: Session,
        *,
        policy: CustomerSegmentationPolicy | None = None,
        classifier: SegmentClassifier | None = None,
    ) -> None:
        self._session = session
        self.policy = policy or CustomerSegmentationPolicy.from_environment()
        self.classifier = classifier or SegmentClassifier()

    def apply_purchase_policy(
        self,
        *,
        customer: Customer,
        purchase_count: int,
        source_event_id: str,
        trace_id: str,
        changed_at: datetime,
    ) -> SegmentationOutcome:
        previous_loyalty = self.classifier.normalize_loyalty(
            customer.loyalty_status
        )
        new_loyalty = previous_loyalty
        if (
            previous_loyalty == "New"
            and purchase_count >= self.policy.loyal_purchase_threshold
        ):
            new_loyalty = "Loyal"

        previous_segment = customer.segment
        new_segment = self.classifier.classify_values(
            customer.affluence_band,
            new_loyalty,
        ).value
        changed = (
            previous_segment != new_segment
            or previous_loyalty != new_loyalty
        )

        customer.loyalty_status = new_loyalty
        customer.segment = new_segment
        customer.profile_version = max(customer.profile_version or 1, 1) + 1
        customer.updated_at = changed_at

        loyalty = self._session.get(Loyalty, customer.customer_id)
        if loyalty is not None:
            loyalty.version = max(loyalty.version or 1, 1) + 1
            loyalty.updated_at = changed_at

        outbox_ids: list[str] = []
        if changed:
            history_id = f"SEGH-{uuid4().hex}"
            self._session.add(
                CustomerSegmentHistory(
                    segment_history_id=history_id,
                    customer_id=customer.customer_id,
                    previous_segment=previous_segment,
                    new_segment=new_segment,
                    previous_loyalty_status=previous_loyalty,
                    new_loyalty_status=new_loyalty,
                    affluence_band=self.classifier.normalize_affluence(
                        customer.affluence_band
                    ),
                    purchase_count_90d=purchase_count,
                    policy_version=self.policy.version,
                    source_event_id=source_event_id,
                    changed_at=changed_at,
                )
            )
            outbox_id = f"OUT-{uuid4().hex}"
            outbox_ids.append(outbox_id)
            payload = {
                "customer_id": customer.customer_id,
                "previous_segment": previous_segment,
                "new_segment": new_segment,
                "previous_loyalty_status": previous_loyalty,
                "new_loyalty_status": new_loyalty,
                "purchase_count_90d": purchase_count,
                "profile_version": customer.profile_version,
                "policy_version": self.policy.version,
                "source_event_id": source_event_id,
                "changed_at": changed_at.isoformat(),
            }
            self._session.add(
                OutboxEvent(
                    outbox_id=outbox_id,
                    aggregate_type="CUSTOMER",
                    aggregate_id=customer.customer_id,
                    event_type="CUSTOMER_SEGMENT_CHANGED",
                    schema_version=1,
                    payload_json=json.dumps(payload, sort_keys=True),
                    trace_id=trace_id,
                    causation_id=source_event_id,
                    correlation_id=trace_id,
                    status="PENDING",
                    attempt_count=0,
                    created_at=changed_at,
                )
            )

        return SegmentationOutcome(
            transition=SegmentTransition(
                previous_segment=previous_segment,
                new_segment=new_segment,
                previous_loyalty_status=previous_loyalty,
                new_loyalty_status=new_loyalty,
                changed=changed,
                changed_at=changed_at,
                policy_version=self.policy.version,
            ),
            profile_version=customer.profile_version,
            outbox_ids=outbox_ids,
        )


__all__ = [
    "CustomerSegmentationPolicy",
    "CustomerSegmentationService",
    "LOYAL_PURCHASE_THRESHOLD_ENV",
    "LOYALTY_POLICY_VERSION_ENV",
    "PURCHASE_WINDOW_DAYS_ENV",
    "SegmentationOutcome",
]
