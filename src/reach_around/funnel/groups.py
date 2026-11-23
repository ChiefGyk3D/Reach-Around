# SPDX-License-Identifier: Apache-2.0
"""Group-level rollup of the click tables (#678).

A group is a tab of the site config. A route class is the destination kind the
Worker stored with the click (``link`` or ``social_icon``). The rollup reads the
per-link click counts the report already holds; it adds no collection, no
column and no person-level data.

Privacy rule. The rollup is derived ONLY from the link rows the report prints
itself. A figure computed from a hidden total (a group "under the floor", a
group "withheld") would be an oracle: run the report under two configs that
group the same hidden ids differently and the changed statuses pin each hidden
cell. So a row is the sum of the printed link rows of that group, a group with
no printed row is left out whatever its hidden total, and the whole section is
left out when either link table is withheld. It is label suppression like the
rest of the report, not k-anonymity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from reach_around.config.blocks import Platforms
from reach_around.config.models import Link, Section, SiteConfig
from reach_around.config.profile_card import MEDIA_KIT_ID

UNGROUPED = "(no group)"
ROUTE_CLASSES = ("link", "social_icon")
ROLLUP_DIGEST_VERSION = 1


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
    printed: Mapping[str, Iterable[Mapping[str, Any]]],
    unavailable: str | None,
) -> dict[str, Any]:
    """Rows per (group, route class), sums of printed link rows only.

    ``unavailable`` is a state of the link tables ("not_in_export" or
    "withheld") that already shows in the report; the section then has no rows.
    """
    if unavailable is not None:
        return {"state": unavailable, "rows": []}
    rows: list[dict[str, Any]] = []
    for route_class in ROUTE_CLASSES:
        shown: dict[str, int] = {}
        for row in printed.get(route_class, ()):
            group = groups.get(row["label"], UNGROUPED)
            shown[group] = shown.get(group, 0) + int(row["views"])
        rows += [_row(g, route_class, n) for g, n in shown.items() if n > 0]
    rows.sort(key=lambda r: (-r["clicks"], r["group"], r["route_class"]))
    return {"state": "ok", "rows": rows}


def _row(group: str, route_class: str, clicks: int) -> dict[str, Any]:
    return {
        "group": group,
        "route_class": route_class,
        "clicks": clicks,
        "status": "ok",
    }


def assignments(
    groups: Mapping[str, str], printed: Mapping[str, Iterable[Mapping[str, Any]]]
) -> list[list[str]]:
    """The effective (printed id, group, route class) triples, sorted. Ids with
    no printed row are not in it, so nothing about where a hidden id sits is
    bound by the digest."""
    return sorted(
        [row["label"], groups.get(row["label"], UNGROUPED), route_class]
        for route_class in ROUTE_CLASSES
        for row in printed.get(route_class, ())
    )


def rollup_digest(
    effective: list[list[str]],
    section: Mapping[str, Any],
    params: Mapping[str, Any],
) -> str:
    """Version 1 digest: a commitment to the PRINTED rollup only.

    It hashes the printed (id, group, route class) assignments, the group rows
    and state, and the floor, top, period, offset and kind. Nothing private
    goes in: not the ledger's core digest (a hash of the private input cells,
    which a reader could brute-force against this value), not a hidden total,
    not the group of an id with no printed row. So it does not bind the ledger
    entry; two exports that print the same rows give the same value.
    """
    blob = json.dumps(
        {
            "version": ROLLUP_DIGEST_VERSION,
            "assignments": effective,
            "rows": section["rows"],
            "state": section["state"],
            "params": dict(params),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def text_lines(section: Mapping[str, Any]) -> list[str]:
    lines = ["", "Link clicks by group (sum of the printed link rows)"]
    if section["state"] == "not_in_export":
        lines.append("  not in this export")
        return lines
    if section["state"] == "withheld":
        lines.append("  a link table is below the privacy floor; nothing shown")
        return lines
    if not section["rows"]:
        lines.append("  no printed link rows")
    else:
        width = max(len(r["group"]) for r in section["rows"])
        for r in section["rows"]:
            lines.append(
                f"  {r['group']:<{width}}  {r['route_class']:<11} {r['clicks']:>8}"
            )
    if "rollup_digest" in section:
        lines.append(
            f"  rollup digest (v{ROLLUP_DIGEST_VERSION}): {section['rollup_digest']}"
        )
    return lines


def csv_rows(section: Mapping[str, Any]) -> list[list[Any]]:
    if section["state"] != "ok":
        return [["", "", "", section["state"]]]
    return [
        [r["group"], r["route_class"], r["clicks"], r["status"]]
        for r in section["rows"]
    ]
