# SPDX-License-Identifier: Apache-2.0
"""The 30 day window of the fediverse collector (design section 2, slice 1).

A pure decision: given the run instant ``now`` and a server-assigned post time,
is the post in the window? The window is ``[now - 30 days, now + skew]``, both
ends inclusive. Nothing here reads a clock; the caller passes ``now``.

Only server-assigned time fields are accepted: ``created_at`` (the Mastodon
entity) and ``indexedAt`` (Bluesky, the server's clock). The author-supplied
Bluesky ``createdAt`` can be backdated, so asking for it is an error, never a
silent fallback. A time without an offset is not trusted either (it has no
instant), and neither is anything that does not parse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal

from reach_around.config.fediverse import WINDOW_DAYS

WINDOW = timedelta(days=WINDOW_DAYS)
SKEW = timedelta(minutes=10)
CLOCK_SKEW_LIMIT = timedelta(minutes=10)

# The fields the window may read, closed.
SERVER_TIME_FIELDS: tuple[str, ...] = ("created_at", "indexedAt")

Placement = Literal["in", "before", "future", "invalid"]


def window_start(now: datetime) -> datetime:
    """The first instant of the window that ends at ``now`` (aware UTC)."""
    return _aware(now) - WINDOW


def parse_server_time(value: object) -> datetime | None:
    """An aware UTC datetime from an ISO 8601 string, else None.

    None for anything that is not a short string, does not parse, or has no
    offset (a naive time names no instant).
    """
    if not isinstance(value, str) or not 10 < len(value) <= 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def post_time(record: dict[str, object], field: str) -> datetime | None:
    """The server-assigned time of a post record, read from ``field``.

    ``field`` must be one of ``SERVER_TIME_FIELDS``; ``createdAt`` (author
    supplied) and every other name raise ``ValueError``.
    """
    if field not in SERVER_TIME_FIELDS:
        raise ValueError("not a server-assigned time field")
    return parse_server_time(record.get(field))


def classify(
    when: datetime | None, now: datetime, *, skew: timedelta = SKEW
) -> Placement:
    """Where a post time sits: ``in``, ``before`` the window, ``future`` or ``invalid``.

    ``future`` is a time more than ``skew`` after ``now``; a post that far ahead
    is dropped and counted, never accepted.
    """
    if when is None or when.tzinfo is None:
        return "invalid"
    instant = when.astimezone(UTC)
    end = _aware(now)
    if instant < end - WINDOW:
        return "before"
    if instant > end + skew:
        return "future"
    return "in"


def in_window(when: datetime | None, now: datetime, *, skew: timedelta = SKEW) -> bool:
    """True when ``when`` is inside ``[now - 30 days, now + skew]``."""
    return classify(when, now, skew=skew) == "in"


def server_clock_skewed(
    date_header: str | None, now: datetime, *, limit: timedelta = CLOCK_SKEW_LIMIT
) -> bool:
    """True when the server's clock cannot be trusted (reason ``clock_skew``).

    That is when its ``Date`` differs from ``now`` by more than ``limit``, and
    also when the header is missing or unreadable: without a usable server time
    the window cannot be trusted, so the check fails closed.
    """
    if not isinstance(date_header, str) or len(date_header) > 64:
        return True
    try:
        server = parsedate_to_datetime(date_header)
    except (TypeError, ValueError, IndexError, OverflowError):
        return True
    if server.tzinfo is None:
        server = server.replace(tzinfo=UTC)
    return abs(server - _aware(now)) > limit


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("the run instant must be timezone aware")
    return value.astimezone(UTC)
