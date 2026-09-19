"""Persistence helpers for event inbox, segment history, and outbox records."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.entities import EventInbox, OutboxEvent


class EventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_inbox(self, event_id: str) -> EventInbox | None:
        return self._session.get(EventInbox, event_id)

    def add_inbox(self, event: EventInbox) -> None:
        self._session.add(event)

    def get_outbox(self, outbox_id: str) -> OutboxEvent | None:
        return self._session.get(OutboxEvent, outbox_id)

    def pending_outbox(self, limit: int = 100) -> list[OutboxEvent]:
        statement = (
            select(OutboxEvent)
            .where(OutboxEvent.status == "PENDING")
            .order_by(OutboxEvent.created_at, OutboxEvent.outbox_id)
            .limit(limit)
        )
        return list(self._session.scalars(statement).all())


__all__ = ["EventRepository"]
