"""Small idempotent schema upgrader for the checked-in SQLite demo database."""

from __future__ import annotations

from sqlalchemy import Engine, inspect

from models.entities import Base


_SQLITE_COLUMNS: dict[str, tuple[str, ...]] = {
    "customers": (
        "profile_version INTEGER NOT NULL DEFAULT 1",
        "updated_at DATETIME",
    ),
    "loyalty": (
        "version INTEGER NOT NULL DEFAULT 1",
        "updated_at DATETIME",
    ),
    "orders": ("source_event_id TEXT",),
}


def ensure_schema(engine: Engine) -> None:
    """Create workflow tables and add backward-compatible SQLite columns."""

    Base.metadata.create_all(engine)
    if engine.url.get_backend_name() != "sqlite":
        return

    with engine.begin() as connection:
        schema = inspect(connection)
        for table_name, definitions in _SQLITE_COLUMNS.items():
            existing = {column["name"] for column in schema.get_columns(table_name)}
            for definition in definitions:
                column_name = definition.split(maxsplit=1)[0]
                if column_name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN {definition}"
                    )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "idx_orders_source_event_id ON orders(source_event_id)"
        )
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_orders_customer_date_status "
            "ON orders(customer_id, order_datetime, status)"
        )


__all__ = ["ensure_schema"]
