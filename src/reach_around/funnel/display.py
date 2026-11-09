# SPDX-License-Identifier: Apache-2.0
"""Optional display fields for the report (#372, #375).

The report itself is UTC: counts are per UTC day and the window is UTC days.
``annotate`` adds two optional blocks to the finished report dictionary and
changes nothing else:

* ``display`` only when a display zone was chosen: the zone and the window's
  first and last instants read in it. Counts and day buckets stay UTC.
* ``era`` only when a cutover day is given and has arrived and the window has
  data: the cutover UTC day and whether the window is before it, after it or
  spans it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from reach_around.config.era import classify_window
from reach_around.config.zones import (
    UTC_NAME,
    check_zone,
    format_day_start,
)

ERA_WORDS = {
    "before": "before Reach Around",
    "after": "after",
    "spans": "spans the cutover (numbers from before and after are added together)",
}


def annotate(
    report: dict[str, Any],
    *,
    zone: str | None,
    cutover: date | None,
    today: date,
) -> dict[str, Any]:
    period = report["period"]
    since = date.fromisoformat(period["since"]) if period["since"] else None
    until = date.fromisoformat(period["until"]) if period["until"] else None
    if zone is not None:
        check_zone(zone)
        block: dict[str, Any] = {
            "zone": zone,
            "note": "Counts are per UTC day; the zone only changes how instants read.",
            "window_start": format_day_start(since, zone) if since else None,
            "window_end_exclusive": (
                format_day_start(until + timedelta(days=1), zone) if until else None
            ),
        }
        if zone == UTC_NAME:
            block["note"] = "Counts are per UTC day."
        report["display"] = block
    kind = classify_window(since, until, cutover, today)
    if kind != "unknown" and cutover is not None:
        report["era"] = {
            "cutover_utc": cutover.isoformat(),
            "window": kind,
            "text": ERA_WORDS[kind],
        }
    return report
