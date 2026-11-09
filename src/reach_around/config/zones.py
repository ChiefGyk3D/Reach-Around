# SPDX-License-Identifier: Apache-2.0
"""Display time zones (#372): UTC is stored and reported; a zone is only for reading.

Everything the project stores, rolls up and exports is UTC, and a daily bucket is
a UTC day. A viewer or an operator may pick a zone to read timestamps in. The
choice is limited to the fixed list below (``ZONES``): the name is checked
against that tuple before it reaches ``zoneinfo`` or a template, so free text
never gets through. A name outside the list is refused with a message that does
not repeat it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC_NAME = "UTC"

# Common zones, one per region, in west-to-east order after UTC. Bounded on
# purpose: the kit page offers exactly these and the report accepts only these.
ZONES: tuple[str, ...] = (
    UTC_NAME,
    "Pacific/Honolulu",
    "America/Anchorage",
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Berlin",
    "Europe/Moscow",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Australia/Sydney",
    "Pacific/Auckland",
)


def utc_label() -> str:
    """The label every UTC date and time carries."""
    return UTC_NAME


def check_zone(name: str) -> str:
    """The zone name if it is on the list, else ``ValueError`` (never echoed)."""
    if not isinstance(name, str) or name not in ZONES:
        raise ValueError("display zone is not one of the supported zones")
    return name


def _tz(name: str) -> ZoneInfo:
    check_zone(name)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("display zone data is not available on this system") from None


def format_instant(moment: datetime, zone: str = UTC_NAME) -> str:
    """``2026-11-08 19:00 EST`` for an aware instant, in the chosen zone.

    The zone's own abbreviation (or its UTC offset where it has none) always
    ends the text, so the zone in use is always labeled.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("an instant needs a time zone (store and pass UTC)")
    check_zone(zone)
    local = moment.astimezone(UTC if zone == UTC_NAME else _tz(zone))
    label = UTC_NAME if zone == UTC_NAME else (local.tzname() or zone)
    return f"{local:%Y-%m-%d %H:%M} {label}"


def utc_day_start(day: date) -> datetime:
    """The instant a UTC calendar day begins."""
    return datetime.combine(day, time(0, 0), tzinfo=UTC)


def utc_day_start_iso(day: date) -> str:
    """The same instant as ``YYYY-MM-DDT00:00:00Z``, the form the kit page carries."""
    return f"{day.isoformat()}T00:00:00Z"


def format_day_start(day: date, zone: str = UTC_NAME) -> str:
    """When a UTC day begins, read in the chosen zone."""
    return format_instant(utc_day_start(day), zone)
