"""The signed-in user's timezone: timestamps are stored in UTC and shown and grouped in local time."""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from tradzlog_web.context import CURRENT_TZ


def zone(name: str | None) -> tzinfo:
    """A tz for an IANA name; UTC for empty or unknown names."""
    if not name:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def valid_timezone(name: str) -> bool:
    return name in timezone_names()


@lru_cache(maxsize=1)
def timezone_names() -> tuple[str, ...]:
    names = {name for name in available_timezones() if "/" in name and not name.startswith(("Etc/", "SystemV/", "posix/", "right/"))}
    return tuple(sorted(names | {"UTC"}))


def tz() -> tzinfo:
    return CURRENT_TZ.get()


def local(value: datetime) -> datetime:
    """``value`` in the user's timezone (naive values are taken as UTC)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz())


def local_date(value: datetime) -> date:
    return local(value).date()


def today() -> date:
    return datetime.now(tz()).date()


def from_local_input(value: datetime) -> datetime:
    """A time typed into a form (no timezone) is the user's local time; return it in UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz())
    return value.astimezone(UTC)


def fmt(value: datetime | None, pattern: str = "%Y-%m-%d %H:%M") -> str:
    return local(value).strftime(pattern) if value is not None else ""
