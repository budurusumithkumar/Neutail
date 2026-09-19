"""SQLite transactional-outbox simulation of the Neu.Tail context bus."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from langsmith import trace

from models.entities import OutboxDelivery
from models.upsell import UpsellResult, UpsellTrigger
from repositories.event_repository import EventRepository
from services._date_utils import utc_now
from services.upsell_decision_service import UpsellDecisionService
from tools.runtime import get_runtime


ContextBusHandler = Callable[["ContextBusEvent"], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ContextBusEvent:
    outbox_id: str
    event_type: str
    aggregate_id: str
    payload: dict[str, Any]
    trace_id: str
    causation_id: str
    correlation_id: str


class ContextBusDispatchError(RuntimeError):
    """Raised when an immediate bus delivery cannot complete safely."""


class ContextBusDispatcher:
    """Deliver outbox events to named, idempotent in-process subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[
            str, list[tuple[str, ContextBusHandler]]
        ] = {}
        self._dispatch_lock = asyncio.Lock()

    def subscribe(
        self,
        event_type: str,
        subscriber_name: str,
        handler: ContextBusHandler,
    ) -> None:
        subscribers = self._subscribers.setdefault(event_type, [])
        if any(name == subscriber_name for name, _handler in subscribers):
            raise ValueError(
                f"Subscriber '{subscriber_name}' is already registered for "
                f"'{event_type}'"
            )
        subscribers.append((subscriber_name, handler))

    async def dispatch(
        self,
        outbox_ids: Iterable[str] | None = None,
        *,
        limit: int = 100,
        raise_errors: bool = True,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Deliver selected or pending events and return subscriber responses."""

        async with self._dispatch_lock:
            events = self._load_events(outbox_ids, limit=limit)
            results: dict[str, dict[str, dict[str, Any]]] = {}
            for event in events:
                try:
                    results[event.outbox_id] = await self._dispatch_event(event)
                except Exception as exc:
                    if raise_errors:
                        raise ContextBusDispatchError(
                            f"Context-bus delivery failed for {event.outbox_id}"
                        ) from exc
            return results

    async def run(
        self,
        stop_event: asyncio.Event,
        *,
        interval_seconds: float = 2.0,
    ) -> None:
        """Recover pending events until application shutdown."""

        while not stop_event.is_set():
            await self.dispatch(raise_errors=False)
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=interval_seconds
                )
            except TimeoutError:
                continue

    def _load_events(
        self, outbox_ids: Iterable[str] | None, *, limit: int
    ) -> list[ContextBusEvent]:
        with get_runtime().session() as session:
            repository = EventRepository(session)
            if outbox_ids is None:
                entities = repository.pending_outbox(limit=limit)
            else:
                entities = [
                    entity
                    for outbox_id in dict.fromkeys(outbox_ids)
                    if (entity := repository.get_outbox(outbox_id)) is not None
                ]
            return [
                ContextBusEvent(
                    outbox_id=entity.outbox_id,
                    event_type=entity.event_type,
                    aggregate_id=entity.aggregate_id,
                    payload=json.loads(entity.payload_json),
                    trace_id=entity.trace_id,
                    causation_id=entity.causation_id,
                    correlation_id=entity.correlation_id,
                )
                for entity in entities
            ]

    async def _dispatch_event(
        self, event: ContextBusEvent
    ) -> dict[str, dict[str, Any]]:
        subscribers = self._subscribers.get(event.event_type, [])
        if not subscribers:
            raise ContextBusDispatchError(
                f"No subscriber is registered for '{event.event_type}'"
            )

        self._record_event_attempt(event.outbox_id)
        responses: dict[str, dict[str, Any]] = {}
        for subscriber_name, handler in subscribers:
            completed = self._completed_delivery(
                event.outbox_id, subscriber_name
            )
            if completed is not None:
                responses[subscriber_name] = completed
                continue

            self._claim_delivery(event.outbox_id, subscriber_name)
            with trace(
                name=f"context_bus.{subscriber_name}",
                run_type="chain",
                inputs={
                    "outbox_id": event.outbox_id,
                    "event_type": event.event_type,
                    "aggregate_id": event.aggregate_id,
                },
                tags=["context-bus", "outbox", subscriber_name],
                metadata={
                    "trace_id": event.trace_id,
                    "causation_id": event.causation_id,
                    "correlation_id": event.correlation_id,
                },
            ) as run:
                try:
                    response = await handler(event)
                except Exception as exc:
                    self._fail_delivery(
                        event.outbox_id, subscriber_name, type(exc).__name__
                    )
                    run.end(error=type(exc).__name__)
                    raise
                self._complete_delivery(
                    event.outbox_id, subscriber_name, response
                )
                run.end(outputs={"delivered": True})
                responses[subscriber_name] = response

        self._mark_published(
            event.outbox_id,
            expected_subscribers=[name for name, _handler in subscribers],
        )
        return responses

    @staticmethod
    def _record_event_attempt(outbox_id: str) -> None:
        with get_runtime().session(write=True) as session:
            event = EventRepository(session).get_outbox(outbox_id)
            if event is not None and event.status != "PUBLISHED":
                event.attempt_count = (event.attempt_count or 0) + 1

    @staticmethod
    def _completed_delivery(
        outbox_id: str, subscriber_name: str
    ) -> dict[str, Any] | None:
        with get_runtime().session() as session:
            delivery = EventRepository(session).get_delivery(
                outbox_id, subscriber_name
            )
            if (
                delivery is None
                or delivery.status != "COMPLETED"
                or delivery.response_json is None
            ):
                return None
            return json.loads(delivery.response_json)

    @staticmethod
    def _claim_delivery(outbox_id: str, subscriber_name: str) -> None:
        timestamp = utc_now()
        with get_runtime().session(write=True) as session:
            repository = EventRepository(session)
            delivery = repository.get_delivery(outbox_id, subscriber_name)
            if delivery is None:
                repository.add_delivery(
                    OutboxDelivery(
                        outbox_id=outbox_id,
                        subscriber_name=subscriber_name,
                        status="PROCESSING",
                        attempt_count=1,
                        updated_at=timestamp,
                    )
                )
            else:
                delivery.status = "PROCESSING"
                delivery.attempt_count = (delivery.attempt_count or 0) + 1
                delivery.last_error = None
                delivery.updated_at = timestamp

    @staticmethod
    def _complete_delivery(
        outbox_id: str,
        subscriber_name: str,
        response: dict[str, Any],
    ) -> None:
        with get_runtime().session(write=True) as session:
            delivery = EventRepository(session).get_delivery(
                outbox_id, subscriber_name
            )
            if delivery is None:
                raise ContextBusDispatchError("Delivery claim disappeared")
            delivery.status = "COMPLETED"
            delivery.response_json = json.dumps(
                response, sort_keys=True, default=str
            )
            delivery.last_error = None
            delivery.updated_at = utc_now()

    @staticmethod
    def _fail_delivery(
        outbox_id: str, subscriber_name: str, error_name: str
    ) -> None:
        with get_runtime().session(write=True) as session:
            delivery = EventRepository(session).get_delivery(
                outbox_id, subscriber_name
            )
            if delivery is not None:
                delivery.status = "FAILED"
                delivery.last_error = error_name
                delivery.updated_at = utc_now()

    @staticmethod
    def _mark_published(
        outbox_id: str, *, expected_subscribers: list[str]
    ) -> None:
        with get_runtime().session(write=True) as session:
            repository = EventRepository(session)
            event = repository.get_outbox(outbox_id)
            if event is None:
                raise ContextBusDispatchError("Outbox event disappeared")
            completed = all(
                (
                    delivery := repository.get_delivery(
                        outbox_id, subscriber_name
                    )
                )
                is not None
                and delivery.status == "COMPLETED"
                for subscriber_name in expected_subscribers
            )
            if completed:
                event.status = "PUBLISHED"
                event.published_at = utc_now()


class ProfileContextSubscriber:
    """Invalidate stale process-local profile and session projections."""

    name = "profile_context_projection"

    def __init__(self, *, profile_agent: Any, session_service: Any) -> None:
        self.profile_agent = profile_agent
        self.session_service = session_service

    async def __call__(self, event: ContextBusEvent) -> dict[str, Any]:
        customer_id = str(event.payload["customer_id"])
        await self.profile_agent.clear_customer(customer_id)
        invalidated = self.session_service.invalidate_customer_context(
            customer_id
        )
        return {
            "customer_id": customer_id,
            "invalidated_sessions": invalidated,
            "profile_cache_invalidated": True,
        }


class HighProductEngagementSubscriber:
    """Route a durable engagement signal to the governed Upsell Agent."""

    name = "upsell_high_engagement"

    def __init__(self, *, orchestrator: Any) -> None:
        self.orchestrator = orchestrator
        self.decisions = orchestrator.upsell_decision_service

    async def __call__(self, event: ContextBusEvent) -> dict[str, Any]:
        existing = self.decisions.find_by_source_event(event.outbox_id)
        if existing is not None:
            return {"upsell_result": existing.result}

        customer_id = str(event.payload["customer_id"])
        session_id = str(event.payload["session_id"])
        self.orchestrator.session_service.ensure_context(
            session_id,
            customer_id,
            attributes={"channel": "event", "recovered": True},
        )
        trigger = UpsellTrigger.model_validate(event.payload["trigger"])
        result: UpsellResult = await self.orchestrator.handle_upsell_trigger(
            customer_id=customer_id,
            session_id=session_id,
            trigger=trigger,
            trace_id=event.trace_id,
        )
        result_payload = result.model_dump(mode="json")
        self.decisions.record_decision(
            customer_id=customer_id,
            session_id=session_id,
            result=result_payload,
            source_event_id=event.outbox_id,
            trace_id=event.trace_id,
        )
        return {"upsell_result": result_payload}


__all__ = [
    "ContextBusDispatchError",
    "ContextBusDispatcher",
    "ContextBusEvent",
    "HighProductEngagementSubscriber",
    "ProfileContextSubscriber",
]
