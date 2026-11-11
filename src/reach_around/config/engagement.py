# SPDX-License-Identifier: Apache-2.0
"""Engagement and average-views figures for the kit, worked out in code.

Inputs are owner-kept numbers for one platform and one UTC period (views, posts,
interactions); this module invents nothing. Two figures, each with a stated
formula:

* Average views per post = views / posts, rounded half up to a whole number.
* Engagement rate = interactions / views x 100, rounded half up to one decimal
  place, printed with a percent sign.

Both are exact integer arithmetic (no floating point), so the same inputs always
give the same text. A figure whose inputs are missing, or whose divisor is zero,
is the text ``not available``: the row is kept, never hidden (owner decision Q6,
2026-11-08). Nothing is ever added or averaged across platforms, because each
platform defines views and interactions its own way.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from reach_around.config.blocks import platform_name

if TYPE_CHECKING:
    from reach_around.config.kit import EngagementRow, KitConfig

NOT_AVAILABLE = "not available"
AVERAGE_VIEWS_MEASURE = "Average views per post"
ENGAGEMENT_MEASURE = "Engagement rate"
AVERAGE_VIEWS_FORMULA = (
    "Views divided by posts in the period, rounded half up to a whole number."
)
ENGAGEMENT_FORMULA = (
    "Interactions divided by views in the period, times 100, rounded half up "
    "to one decimal place."
)
ACROSS_PLATFORMS_NOTE = (
    "Figures are never added or averaged across platforms: each platform "
    "defines views and interactions its own way."
)


def average_views(views: int | None, posts: int | None) -> int | None:
    """``views / posts`` rounded half up, or ``None`` with no inputs or no posts."""
    if views is None or posts is None or posts <= 0:
        return None
    return (2 * views + posts) // (2 * posts)


def engagement_percent(interactions: int | None, views: int | None) -> str | None:
    """``interactions / views * 100`` rounded half up to one decimal, as text
    such as ``3.1%``; ``None`` with no inputs or no views."""
    if interactions is None or views is None or views <= 0:
        return None
    tenths = (2000 * interactions + views) // (2 * views)
    return f"{tenths // 10}.{tenths % 10}%"


FORMULAS = [
    {"measure": AVERAGE_VIEWS_MEASURE, "formula": AVERAGE_VIEWS_FORMULA},
    {"measure": ENGAGEMENT_MEASURE, "formula": ENGAGEMENT_FORMULA},
]


def engagement_rows(kit: KitConfig) -> list[dict[str, Any]]:
    """The printed rows of ``kit.engagement`` (see ``rows_for``)."""
    return rows_for(kit.engagement)


def rows_for(entries: Sequence[EngagementRow]) -> list[dict[str, Any]]:
    """The printed rows: two per platform entry (average views, engagement).
    Every value, date and period is a string, ready for a template."""
    out: list[dict[str, Any]] = []
    for entry in entries:
        average = average_views(entry.views, entry.posts)
        rate = engagement_percent(entry.interactions, entry.views)
        has_figures = any(
            v is not None for v in (entry.views, entry.posts, entry.interactions)
        )
        period = (
            f"{entry.period_start.isoformat()} to {entry.period_end.isoformat()}"
            if entry.period_start is not None and entry.period_end is not None
            else ""
        )
        as_of = entry.as_of.isoformat() if entry.as_of is not None else ""
        common = {
            "platform": platform_name(entry.platform),
            "start": entry.period_start.isoformat()
            if has_figures and entry.period_start
            else "",
            "end": entry.period_end.isoformat()
            if has_figures and entry.period_end
            else "",
            "period": period if has_figures else "",
            "as_of": as_of if has_figures else "",
            "provenance": "Owner-supplied" if entry.owner_supplied else "",
        }
        out.append(
            {
                **common,
                "measure": AVERAGE_VIEWS_MEASURE,
                "formula": AVERAGE_VIEWS_FORMULA,
                "value": NOT_AVAILABLE if average is None else f"{average:,}",
                "note": "",
            }
        )
        out.append(
            {
                **common,
                "measure": ENGAGEMENT_MEASURE,
                "formula": ENGAGEMENT_FORMULA,
                "value": NOT_AVAILABLE if rate is None else rate,
                "note": (
                    f"Interactions counted: {entry.counts}" if entry.counts else ""
                ),
            }
        )
    return out
