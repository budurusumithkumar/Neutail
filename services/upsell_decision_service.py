"""Process-local UI decision and idempotency state for the demo."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Optional


class UpsellDecisionNotFoundError(LookupError):
    """Raised without disclosing decisions owned by another customer."""


class UpsellDecisionConflictError(RuntimeError):
    """Raised when a decision is already resolved or a request is in flight."""


class IdempotencyConflictError(RuntimeError):
    """Raised when a key is reused for a different request payload."""


@dataclass(frozen=True)
class UpsellDecisionRecord:
    decision_id: str
    customer_id: str
    session_id: str
    offer_type: str
    trigger_type: str


@dataclass
class _IdempotencyEntry:
    fingerprint: str
    response: Optional[dict[str, Any]] = None


class UpsellDecisionService:
    """Retain offer ownership and explicit UI responses for one process."""

    def __init__(self) -> None:
        self._decisions: dict[str, UpsellDecisionRecord] = {}
        self._resolved: dict[str, str] = {}
        self._decision_events: dict[str, _IdempotencyEntry] = {}
        self._engagement_events: dict[str, _IdempotencyEntry] = {}
        self._lock = RLock()

    def record_decision(
        self,
        *,
        customer_id: str,
        session_id: str,
        result: dict[str, Any],
    ) -> None:
        """Retain only actionable offers; suppressed decisions cannot resolve."""

        offer = result.get("offer")
        trigger = result.get("trigger")
        decision_id = result.get("decision_id")
        if (
            result.get("status") != "OFFER_AVAILABLE"
            or not isinstance(offer, dict)
            or not isinstance(trigger, dict)
            or not isinstance(decision_id, str)
        ):
            return
        record = UpsellDecisionRecord(
            decision_id=decision_id,
            customer_id=customer_id,
            session_id=session_id,
            offer_type=str(offer["offer_type"]),
            trigger_type=str(trigger["trigger_type"]),
        )
        with self._lock:
            self._decisions.setdefault(decision_id, record)

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
        with self._lock:
            record = self._decisions.get(decision_id)
            if record is None or record.customer_id != customer_id:
                raise UpsellDecisionNotFoundError(decision_id)
            if record.session_id != session_id:
                raise UpsellDecisionNotFoundError(decision_id)

            existing = self._decision_events.get(idempotency_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflictError(idempotency_key)
                if existing.response is None:
                    raise UpsellDecisionConflictError(
                        "The same request is already being processed"
                    )
                return record, dict(existing.response)

            if decision_id in self._resolved:
                raise UpsellDecisionConflictError(
                    "The upsell decision is already resolved"
                )
            self._decision_events[idempotency_key] = _IdempotencyEntry(
                fingerprint=fingerprint
            )
            return record, None

    def complete_decision_event(
        self,
        *,
        decision_id: str,
        idempotency_key: str,
        event_type: str,
        response: dict[str, Any],
    ) -> None:
        with self._lock:
            entry = self._decision_events[idempotency_key]
            entry.response = dict(response)
            self._resolved[decision_id] = event_type

    def abort_decision_event(self, idempotency_key: str) -> None:
        with self._lock:
            entry = self._decision_events.get(idempotency_key)
            if entry is not None and entry.response is None:
                del self._decision_events[idempotency_key]

    def claim_engagement_event(
        self, *, idempotency_key: str, fingerprint: str
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            existing = self._engagement_events.get(idempotency_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflictError(idempotency_key)
                if existing.response is None:
                    raise UpsellDecisionConflictError(
                        "The same request is already being processed"
                    )
                return dict(existing.response)
            self._engagement_events[idempotency_key] = _IdempotencyEntry(
                fingerprint=fingerprint
            )
            return None

    def complete_engagement_event(
        self, *, idempotency_key: str, response: dict[str, Any]
    ) -> None:
        with self._lock:
            self._engagement_events[idempotency_key].response = dict(response)

    def abort_engagement_event(self, idempotency_key: str) -> None:
        with self._lock:
            entry = self._engagement_events.get(idempotency_key)
            if entry is not None and entry.response is None:
                del self._engagement_events[idempotency_key]


__all__ = [
    "IdempotencyConflictError",
    "UpsellDecisionConflictError",
    "UpsellDecisionNotFoundError",
    "UpsellDecisionRecord",
    "UpsellDecisionService",
]
