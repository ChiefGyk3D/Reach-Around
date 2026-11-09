# SPDX-License-Identifier: Apache-2.0
"""Before and after Reach Around (#375): which side of the cutover a dated number is on.

The cutover is a UTC calendar day (``config.cutover_date``). A number dated that
day or later is "after"; one dated earlier is "before". While the cutover is
unset or still in the future there is nothing to separate, so the answer is
``UNKNOWN`` and no page prints any extra text.
"""

from __future__ import annotations

from datetime import date

BEFORE = "before"
AFTER = "after"
UNKNOWN = "unknown"
SPANS = "spans"

BEFORE_TEXT = "before Reach Around"
AFTER_TEXT = "after"


def _active(cutover: date | None, today: date | None) -> bool:
    if cutover is None:
        return False
    return today is None or cutover <= today


def classify_era(
    as_of: date | None, cutover: date | None, today: date | None = None
) -> str:
    """``before``, ``after`` or ``unknown`` (no date, no cutover, cutover ahead)."""
    if as_of is None or cutover is None or not _active(cutover, today):
        return UNKNOWN
    return AFTER if as_of >= cutover else BEFORE


def era_text(era: str) -> str:
    """Short visible text for an era; empty for ``unknown``."""
    return {BEFORE: BEFORE_TEXT, AFTER: AFTER_TEXT}.get(era, "")


def classify_window(
    since: date | None,
    until: date | None,
    cutover: date | None,
    today: date | None = None,
) -> str:
    """The same for a window of UTC days, or ``spans`` when it crosses the cutover."""
    if since is None or until is None or cutover is None:
        return UNKNOWN
    if not _active(cutover, today):
        return UNKNOWN
    if until < cutover:
        return BEFORE
    if since >= cutover:
        return AFTER
    return SPANS


def record_marker(era: str | None, note: str | None) -> str:
    """The text a history record's own ``note`` (else ``era``) asks to show.

    Plain text only: the caller passes it through the template's escaping, and
    it is used nowhere else.
    """
    for value in (note, era):
        if value:
            return value
    return ""
