"""SQLAlchemy engine and session-factory construction."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "database" / "neutail_demo.db"
DATABASE_URL_ENV = "NEUTAIL_DATABASE_URL"


def resolve_database_url(database_url: Optional[str] = None) -> str:
    """Resolve an explicit URL, environment override, or bundled SQLite path."""

    configured_url = database_url or os.getenv(DATABASE_URL_ENV)
    if configured_url:
        return configured_url
    return f"sqlite+pysqlite:///{DEFAULT_DATABASE_PATH}"


def create_database_engine(database_url: Optional[str] = None) -> Engine:
    """Create the application engine with SQLite integrity checks enabled."""

    engine = create_engine(
        resolve_database_url(database_url),
        pool_pre_ping=True,
    )
    if engine.url.get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(
            connection: sqlite3.Connection, _connection_record: object
        ) -> None:
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def create_session_factory(
    database_url: Optional[str] = None,
) -> tuple[Engine, sessionmaker[Session]]:
    """Create an engine and non-expiring session factory."""

    engine = create_database_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


__all__ = [
    "DATABASE_URL_ENV",
    "DEFAULT_DATABASE_PATH",
    "PROJECT_ROOT",
    "create_database_engine",
    "create_session_factory",
    "resolve_database_url",
]
