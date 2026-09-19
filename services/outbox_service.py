"""Small transactional-outbox dispatcher used by the local demo."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy.orm import Session

from repositories.event_repository import EventRepository
from services._date_utils import utc_now


class OutboxService:
    def __init__(self, session: Session) -> None:
        self._repository = EventRepository(session)

    def mark_published(
        self,
        outbox_ids: Iterable[str],
        *,
        published_at: datetime | None = None,
    ) -> int:
        timestamp = published_at or utc_now()
        count = 0
        for outbox_id in outbox_ids:
            event = self._repository.get_outbox(outbox_id)
            if event is None or event.status == "PUBLISHED":
                continue
            event.status = "PUBLISHED"
            event.attempt_count = (event.attempt_count or 0) + 1
            event.published_at = timestamp
            count += 1
        return count


__all__ = ["OutboxService"]
