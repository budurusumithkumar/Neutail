"""Thread-safe process-local session context for the demo orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any, Optional
from uuid import uuid4

from orchestrator.models import SessionContext


class SessionIdentityMismatchError(PermissionError):
    """Raised when a session is reused for a different customer."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session '{session_id}' belongs to another customer")


class SessionContextService:
    """Load and persist multi-turn context without external infrastructure.

    State is intentionally process-local for the PoC. Values are copied at the
    boundary so graph mutations cannot silently modify the stored context.
    """

    MAX_CONVERSATION_TURNS = 20

    def __init__(self) -> None:
        self._contexts: dict[str, SessionContext] = {}
        self._lock = RLock()

    def get_context(
        self, session_id: str, customer_id: Optional[str] = None
    ) -> Optional[SessionContext]:
        normalized_session = self._required(session_id, "session_id")
        normalized_customer = (
            self._required(customer_id, "customer_id")
            if customer_id is not None
            else None
        )
        with self._lock:
            context = self._contexts.get(normalized_session)
            if context is None:
                return None
            if (
                normalized_customer is not None
                and context.customer_id != normalized_customer
            ):
                raise SessionIdentityMismatchError(normalized_session)
            return context.model_copy(deep=True)

    def get_or_create(self, session_id: str, customer_id: str) -> SessionContext:
        normalized_session = self._required(session_id, "session_id")
        normalized_customer = self._required(customer_id, "customer_id")
        with self._lock:
            context = self._contexts.get(normalized_session)
            if context is not None:
                if context.customer_id != normalized_customer:
                    raise SessionIdentityMismatchError(normalized_session)
                return context.model_copy(deep=True)
            return SessionContext(
                session_id=normalized_session,
                customer_id=normalized_customer,
            )

    def create_context(
        self,
        customer_id: str,
        *,
        attributes: Optional[dict[str, Any]] = None,
    ) -> SessionContext:
        """Create and persist a new empty session with a server-owned ID."""

        normalized_customer = self._required(customer_id, "customer_id")
        safe_attributes = dict(attributes or {})
        now = datetime.now(timezone.utc)
        with self._lock:
            while True:
                session_id = f"S{uuid4().hex}"
                if session_id not in self._contexts:
                    break
            context = SessionContext(
                session_id=session_id,
                customer_id=normalized_customer,
                attributes=safe_attributes,
                created_at=now,
                updated_at=now,
            )
            self._contexts[session_id] = context.model_copy(deep=True)
            return context.model_copy(deep=True)

    def save_context(self, context: SessionContext) -> SessionContext:
        if not isinstance(context, SessionContext):
            context = SessionContext.model_validate(context)
        stored = context.model_copy(
            deep=True,
            update={
                "conversation": context.conversation[
                    -self.MAX_CONVERSATION_TURNS :
                ],
                "updated_at": datetime.now(timezone.utc),
            },
        )
        with self._lock:
            existing = self._contexts.get(stored.session_id)
            if existing is not None and existing.customer_id != stored.customer_id:
                raise SessionIdentityMismatchError(stored.session_id)
            self._contexts[stored.session_id] = stored
        return stored.model_copy(deep=True)

    def clear_context(self, session_id: str, customer_id: str) -> bool:
        normalized_session = self._required(session_id, "session_id")
        normalized_customer = self._required(customer_id, "customer_id")
        with self._lock:
            existing = self._contexts.get(normalized_session)
            if existing is None:
                return False
            if existing.customer_id != normalized_customer:
                raise SessionIdentityMismatchError(normalized_session)
            del self._contexts[normalized_session]
            return True

    def invalidate_customer_context(self, customer_id: str) -> int:
        """Drop stale profile projections while preserving conversations."""

        normalized_customer = self._required(customer_id, "customer_id")
        invalidated = 0
        with self._lock:
            for session_id, context in list(self._contexts.items()):
                if context.customer_id != normalized_customer:
                    continue
                self._contexts[session_id] = context.model_copy(
                    deep=True,
                    update={
                        "customer_context": None,
                        "updated_at": datetime.now(timezone.utc),
                    },
                )
                invalidated += 1
        return invalidated

    @staticmethod
    def _required(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
        return value.strip()


__all__ = ["SessionContextService", "SessionIdentityMismatchError"]
