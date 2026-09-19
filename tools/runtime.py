"""Shared runtime resources used by synchronous FastMCP tool adapters."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from threading import RLock
from typing import Optional

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from database.migrations import ensure_schema
from database.session import create_session_factory
from services.upsell_policy_service import UpsellPolicyState


class ToolRuntime:
    """Own database connections and cross-call deterministic policy state."""

    def __init__(self, database_url: Optional[str] = None) -> None:
        self.engine, self.session_factory = create_session_factory(database_url)
        ensure_schema(self.engine)
        self.upsell_policy_state = UpsellPolicyState()

    engine: Engine
    session_factory: sessionmaker[Session]

    @contextmanager
    def session(self, *, write: bool = False) -> Iterator[Session]:
        """Yield a short-lived session and commit only explicit write tools."""

        with self.session_factory() as session:
            try:
                yield session
                if write:
                    session.commit()
            except Exception:
                session.rollback()
                raise

    def close(self) -> None:
        """Dispose pooled connections owned by this runtime."""

        self.engine.dispose()


_runtime: Optional[ToolRuntime] = None
_runtime_lock = RLock()


def get_runtime() -> ToolRuntime:
    """Return the lazily-created process runtime."""

    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = ToolRuntime()
        return _runtime


def configure_runtime(database_url: Optional[str] = None) -> ToolRuntime:
    """Replace the runtime, primarily for startup configuration and tests."""

    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
        _runtime = ToolRuntime(database_url)
        from services.product_retrieval_service import clear_product_vector_store

        clear_product_vector_store()
        return _runtime


__all__ = ["ToolRuntime", "configure_runtime", "get_runtime"]
