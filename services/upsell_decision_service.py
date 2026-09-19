"""Durable Upsell decisions and idempotent customer-response state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from models.entities import (
    UpsellDecisionEventRecord,
    UpsellDecisionRecordEntity,
)
from services._date_utils import utc_now
from tools.runtime import get_runtime


class UpsellDecisionNotFoundError(LookupError):
    """Raised without disclosing decisions owned by another customer."""


class UpsellDecisionConflictError(RuntimeError):
    """Raised when a decision is resolved or the request is in flight."""


class IdempotencyConflictError(RuntimeError):
    """Raised when a key is reused for a different request payload."""


@dataclass(frozen=True)
class UpsellDecisionRecord:
    decision_id: str
    customer_id: str
    session_id: str
    offer_type: str
    trigger_type: str
    status: str
    result: dict[str, Any]
    source_event_id: Optional[str]
    trace_id: Optional[str]
    created_at: datetime


class UpsellDecisionService:
    """Persist offers and explicit UI outcomes across application restarts."""

    _TERMINAL_STATUS = {
        "OFFER_ACCEPTED": "ACCEPTED",
        "OFFER_DECLINED": "DECLINED",
        "OFFER_DISMISSED": "DISMISSED",
    }

    def record_decision(
        self,
        *,
        customer_id: str,
        session_id: str,
        result: dict[str, Any],
        source_event_id: str | None = None,
        trace_id: str | None = None,
    ) -> UpsellDecisionRecord | None:
        """Upsert only actionable offers; suppressed results are not pending."""

        offer = result.get("offer")
        trigger = result.get("trigger")
        decision_id = result.get("decision_id")
        if (
            result.get("status") != "OFFER_AVAILABLE"
            or not isinstance(offer, dict)
            or not isinstance(trigger, dict)
            or not isinstance(decision_id, str)
        ):
            return None

        timestamp = utc_now()
        result_json = json.dumps(result, sort_keys=True, default=str)
        with get_runtime().session(write=True) as session:
            entity = session.get(UpsellDecisionRecordEntity, decision_id)
            if entity is None and source_event_id:
                entity = session.scalars(
                    select(UpsellDecisionRecordEntity).where(
                        UpsellDecisionRecordEntity.source_event_id
                        == source_event_id
                    )
                ).one_or_none()
            if entity is None:
                entity = UpsellDecisionRecordEntity(
                    decision_id=decision_id,
                    customer_id=customer_id,
                    session_id=session_id,
                    offer_type=str(offer["offer_type"]),
                    trigger_type=str(trigger["trigger_type"]),
                    status="PENDING",
                    result_json=result_json,
                    source_event_id=source_event_id,
                    trace_id=trace_id,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                session.add(entity)
                session.flush()
            else:
                if entity.customer_id != customer_id:
                    raise UpsellDecisionConflictError(
                        "The decision identity does not match its customer"
                    )
                if source_event_id and entity.source_event_id is None:
                    entity.source_event_id = source_event_id
                if trace_id and entity.trace_id is None:
                    entity.trace_id = trace_id
                entity.result_json = result_json
                entity.updated_at = timestamp
            return self._to_record(entity)

    def find_by_source_event(
        self, source_event_id: str
    ) -> UpsellDecisionRecord | None:
        with get_runtime().session() as session:
            entity = session.scalars(
                select(UpsellDecisionRecordEntity).where(
                    UpsellDecisionRecordEntity.source_event_id
                    == source_event_id
                )
            ).one_or_none()
            return self._to_record(entity) if entity is not None else None

    def list_pending(self, customer_id: str) -> list[UpsellDecisionRecord]:
        with get_runtime().session() as session:
            statement = (
                select(UpsellDecisionRecordEntity)
                .where(
                    UpsellDecisionRecordEntity.customer_id == customer_id,
                    UpsellDecisionRecordEntity.status == "PENDING",
                )
                .order_by(
                    UpsellDecisionRecordEntity.created_at.desc(),
                    UpsellDecisionRecordEntity.decision_id.desc(),
                )
            )
            return [
                self._to_record(entity)
                for entity in session.scalars(statement).all()
            ]

    def claim_decision_event(
        self,
        *,
        decision_id: str,
        customer_id: str,
        session_id: str,
        event_type: str,
        idempotency_key: str,
    ) -> tuple[UpsellDecisionRecord, Optional[dict[str, Any]]]:
        fingerprint = "|".join(
            (decision_id, customer_id, session_id, event_type)
        )
        timestamp = utc_now()
        with get_runtime().session(write=True) as session:
            entity = session.get(UpsellDecisionRecordEntity, decision_id)
            if (
                entity is None
                or entity.customer_id != customer_id
                or entity.session_id != session_id
            ):
                raise UpsellDecisionNotFoundError(decision_id)

            existing = session.get(UpsellDecisionEventRecord, idempotency_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflictError(idempotency_key)
                if existing.status == "COMPLETED" and existing.response_json:
                    return self._to_record(entity), json.loads(
                        existing.response_json
                    )
                if existing.status == "PROCESSING":
                    raise UpsellDecisionConflictError(
                        "The same request is already being processed"
                    )
                existing.status = "PROCESSING"
                existing.updated_at = timestamp
            else:
                if entity.status != "PENDING":
                    raise UpsellDecisionConflictError(
                        "The upsell decision is already resolved"
                    )
                session.add(
                    UpsellDecisionEventRecord(
                        idempotency_key=idempotency_key,
                        decision_id=decision_id,
                        fingerprint=fingerprint,
                        event_type=event_type,
                        status="PROCESSING",
                        created_at=timestamp,
                        updated_at=timestamp,
                    )
                )
            return self._to_record(entity), None

    def complete_decision_event(
        self,
        *,
        decision_id: str,
        idempotency_key: str,
        event_type: str,
        response: dict[str, Any],
    ) -> None:
        timestamp = utc_now()
        with get_runtime().session(write=True) as session:
            event = session.get(UpsellDecisionEventRecord, idempotency_key)
            decision = session.get(UpsellDecisionRecordEntity, decision_id)
            if event is None or decision is None:
                raise UpsellDecisionNotFoundError(decision_id)
            event.status = "COMPLETED"
            event.response_json = json.dumps(
                response, sort_keys=True, default=str
            )
            event.updated_at = timestamp
            decision.status = self._TERMINAL_STATUS[event_type]
            decision.updated_at = timestamp
            decision.resolved_at = timestamp

    def abort_decision_event(self, idempotency_key: str) -> None:
        with get_runtime().session(write=True) as session:
            event = session.get(UpsellDecisionEventRecord, idempotency_key)
            if event is not None and event.status == "PROCESSING":
                event.status = "FAILED"
                event.updated_at = utc_now()

    @staticmethod
    def _to_record(entity: UpsellDecisionRecordEntity) -> UpsellDecisionRecord:
        return UpsellDecisionRecord(
            decision_id=entity.decision_id,
            customer_id=entity.customer_id,
            session_id=entity.session_id,
            offer_type=entity.offer_type,
            trigger_type=entity.trigger_type,
            status=entity.status,
            result=json.loads(entity.result_json),
            source_event_id=entity.source_event_id,
            trace_id=entity.trace_id,
            created_at=entity.created_at,
        )


__all__ = [
    "IdempotencyConflictError",
    "UpsellDecisionConflictError",
    "UpsellDecisionNotFoundError",
    "UpsellDecisionRecord",
    "UpsellDecisionService",
]
