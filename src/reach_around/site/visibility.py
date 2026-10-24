# SPDX-License-Identifier: Apache-2.0
"""Schedule-aware visibility for links and sections.

``show_from`` and ``show_until`` are inclusive calendar days in the site's
time zone. The caller works out "today" with ``today_in`` and passes it in,
so everything here stays a pure function of its arguments.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from reach_around.config.models import Block, Link, Section


def is_visible(link: Link, today: date) -> bool:
    """Return whether ``link`` shows on the calendar day ``today``."""
    if link.show_from is not None and today < link.show_from:
        return False
    return link.show_until is None or today <= link.show_until


def visible_blocks(blocks: Sequence[Block], today: date) -> list[Block]:
    """Return the blocks visible on ``today`` without changing the input.

    A section keeps only its visible links and is dropped when none remain.
    Any other block type has no dates and passes through unchanged.
    """
    out: list[Block] = []
    for block in blocks:
        if isinstance(block, Link):
            if is_visible(block, today):
                out.append(block)
        elif isinstance(block, Section):
            kept = [link for link in block.links if is_visible(link, today)]
            if kept:
                out.append(block.model_copy(update={"links": kept}))
        else:
            out.append(block)
    return out


def today_in(timezone: str, now: datetime) -> date:
    """Return the calendar date of the instant ``now`` in ``timezone``."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    try:
        zone = ZoneInfo(timezone)
    except (KeyError, ValueError, OSError):
        raise ValueError(f"unknown time zone {timezone!r}") from None
    return now.astimezone(zone).date()
