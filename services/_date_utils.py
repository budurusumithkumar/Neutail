"""Shared calendar-window helpers for history services."""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current UTC time without timezone data to match SQLite values."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


def months_before(moment: datetime, months: int) -> datetime:
    """Subtract whole calendar months while clamping invalid month-end days."""

    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        raise ValueError("months must be a positive integer")

    absolute_month = moment.year * 12 + moment.month - 1 - months
    year, zero_based_month = divmod(absolute_month, 12)
    month = zero_based_month + 1
    day = min(moment.day, monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def sqlite_datetime(moment: datetime) -> str:
    """Format a datetime for comparison with SQLite's ``datetime()`` output."""

    return moment.isoformat(sep=" ", timespec="microseconds")


def validate_positive_int(value: int, field_name: str) -> int:
    """Validate a positive, non-boolean integer argument."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value
