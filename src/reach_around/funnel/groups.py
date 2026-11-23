# SPDX-License-Identifier: Apache-2.0
"""Group-level rollup of the click tables (#678).

A group is a tab of the site config. A route class is the destination kind the
Worker stored with the click (``link`` or ``social_icon``). The rollup reads the
per-link click counts the report already holds; it adds no collection, no
column and no person-level data.

Privacy rule. A group total beside the link table would let a reader subtract
the printed link rows and recover the sum of the hidden ones. So a row prints
only the sum of the link rows the report prints itself, never a figure the link
tables withhold. A group whose whole total is under the floor prints
"Under 5" (the same words as the kit), and a group that holds the floor or more
but prints no link row of its own is ``withheld``. It is label suppression like
the rest of the report, not k-anonymity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from reach_around.config.blocks import Platforms
from reach_around.config.models import Link, Section, SiteConfig
from reach_around.config.profile_card import MEDIA_KIT_ID
from reach_around.funnel.floor import UNDER_FLOOR

UNGROUPED = "(no group)"
ROUTE_CLASSES = ("link", "social_icon")


def group_map(config: SiteConfig) -> dict[str, str]:
    """Click id to group (tab id) for every link, section link and platform icon."""
    mapping: dict[str, str] = {}
    for tab in config.tabs:
        for block in tab.blocks:
            if isinstance(block, Link):
                mapping[block.id] = tab.id
            elif isinstance(block, Section):
                mapping.update((link.id, tab.id) for link in block.links)
            elif isinstance(block, Platforms):
                mapping.update((ident, tab.id) for ident in block.item_ids)
    card = config.profile_card
    if card is not None and card.button is not None:
        mapping.setdefault(MEDIA_KIT_ID, UNGROUPED)
    return mapping


def rollup(
    groups: Mapping[str, str],
    totals: Mapping[str, Mapping[str, int]],
    printed: Mapping[str, Iterable[Mapping[str, Any]]],
    floor: int,
    present: bool,
) -> dict[str, Any]:
    """Rows per (group, route class). ``totals`` holds every click count by
    route class and link id; ``printed`` the link rows the report prints."""
    if not present:
        return {"state": "not_in_export", "rows": []}
    rows: list[dict[str, Any]] = []
    for route_class in ROUTE_CLASSES:
        total: dict[str, int] = {}
        shown: dict[str, int] = {}
        for ident, n in totals.get(route_class, {}).items():
            group = groups.get(ident, UNGROUPED)
            total[group] = total.get(group, 0) + n
        for row in printed.get(route_class, ()):
            group = groups.get(row["label"], UNGROUPED)
            shown[group] = shown.get(group, 0) + int(row["views"])
        for group, count in total.items():
            if count <= 0:
                continue
            if count < floor:
                rows.append(_row(group, route_class, None, "under_floor"))
            elif shown.get(group):
                rows.append(_row(group, route_class, shown[group], "ok"))
            else:
                rows.append(_row(group, route_class, None, "withheld"))
    rows.sort(
        key=lambda r: (-(r["clicks"] or 0), r["group"], r["route_class"]),
    )
    return {"state": "ok", "rows": rows}


def _row(
    group: str, route_class: str, clicks: int | None, status: str
) -> dict[str, Any]:
    return {
        "group": group,
        "route_class": route_class,
        "clicks": clicks,
        "status": status,
    }


def text_lines(section: Mapping[str, Any]) -> list[str]:
    lines = ["", "Link clicks by group (sum of the printed link rows)"]
    if section["state"] == "not_in_export":
        lines.append("  not in this export")
        return lines
    if not section["rows"]:
        lines.append("  no data in this period")
        return lines
    width = max(len(r["group"]) for r in section["rows"])
    for r in section["rows"]:
        if r["status"] == "under_floor":
            shown = UNDER_FLOOR
        elif r["status"] == "withheld":
            shown = "withheld"
        else:
            shown = str(r["clicks"])
        lines.append(f"  {r['group']:<{width}}  {r['route_class']:<11} {shown:>8}")
    return lines


def csv_rows(section: Mapping[str, Any]) -> list[list[Any]]:
    if section["state"] != "ok":
        return [["", "", "", section["state"]]]
    return [
        [
            r["group"],
            r["route_class"],
            "" if r["clicks"] is None else r["clicks"],
            r["status"],
        ]
        for r in section["rows"]
    ]
