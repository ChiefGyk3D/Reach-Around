# SPDX-License-Identifier: Apache-2.0
"""The optional ``cutover_date``: the day the site went live, a UTC calendar day.

One date, written ``YYYY-MM-DD``, with no time and no zone: it is a UTC day, and
everything that prints it says "UTC". It is public by display policy (the kit
page and the kit PDF print it). Later code asks for it only through
``cutover_date`` (the configured day or ``None``) or ``live_since`` (the day, or
``None`` while it is still in the future), never by reading the config key.

Validation: a real calendar day, 2000-01-01 or later, and no more than
``MAX_DAYS_AHEAD`` days after today's UTC day. A day in the future is allowed
(the owner may stage it), is not shown anywhere, and ``reach-around check``
warns about it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from reach_around.config.models import SiteConfig

MIN_DAY = date(2000, 1, 1)
MAX_DAYS_AHEAD = 366


def utc_today() -> date:
    return datetime.now(UTC).date()


def check_cutover_day(value: date | None) -> date | None:
    """Validator body: the range rules. ``None`` (unset) passes."""
    if value is None:
        return None
    if value < MIN_DAY:
        raise ValueError("cutover_date must be 2000-01-01 or later")
    if value > utc_today() + timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError("cutover_date is too far in the future")
    return value


def cutover_date(config: SiteConfig) -> date | None:
    """The configured cutover UTC day, or ``None`` when unset."""
    return config.cutover_date


def live_since(config: SiteConfig, today: date) -> date | None:
    """The cutover day once it has arrived (``today`` is a UTC day), else ``None``."""
    day = config.cutover_date
    return day if day is not None and day <= today else None


def cutover_label(day: date) -> str:
    return f"Live since {day.isoformat()} (UTC)"
