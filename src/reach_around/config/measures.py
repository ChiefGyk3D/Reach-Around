# SPDX-License-Identifier: Apache-2.0
"""The kit's measure model, its registry and the compile shim (kit v2, slice 3, #628).

A *measure* is one value with one unit, one date and one trust label (spec 6.1).
The three places the kit takes numbers from today (``kit.metrics``,
``kit.analytics`` and ``kit.engagement``) are compiled into measures here, once,
and the renderer reads measures only. Nothing about those old fields changes for
the owner: a config that worked before builds the same pages.

The registry holds every key the kit knows: its unit, a definition text, a
formula when the value is worked out, who may see it by default, and a changelog
line. A config picks keys; it does not rewrite the definition of a known one.

Visibility. A measure is never wider than the display policy of the field it came
from (``KIT_FIELD_POLICY``), whatever the registry says: the registry default can
only narrow. A gated measure is therefore never in the public projection.

Units. The nine units of the spec are fixed, plus ``count`` for the owner-named
analytics tiles the old config carries (a label such as "Profile visits"), whose
unit the config never recorded.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Any, Literal

from reach_around.config.engagement import (
    AVERAGE_VIEWS_FORMULA,
    ENGAGEMENT_FORMULA,
    average_views,
    engagement_tenths,
)
from reach_around.config.trust import (
    Trust,
    analytics_trust,
    engagement_trust,
    metric_trust,
)

if TYPE_CHECKING:
    from reach_around.config.kit import (
        EngagementRow,
        KitConfig,
        PlatformAnalytics,
        ResolvedMetric,
    )
    from reach_around.config.numbers import Numbers

MeasureVisibility = Literal["public", "gated", "private"]
Block = Literal["headline", "tile", "derived"]

UNITS: tuple[str, ...] = (
    "follows",
    "viewers",
    "hours",
    "views",
    "interactions",
    "downloads",
    "emails",
    "clicks",
    "percent",
    "count",
)

_SLICE = "Added in kit v2 slice 3 (#628)"
_SLICE6 = "Added in kit v2 slice 6 (#631)"


@dataclass(frozen=True)
class MeasureDef:
    """One registry entry."""

    key: str
    label: str
    unit: str
    definition: str
    changelog: str
    visibility: MeasureVisibility
    computed: bool = False
    formula: str = ""
    # A percent value is an integer in tenths; only a change may be negative.
    signed: bool = False


REGISTRY: Mapping[str, MeasureDef] = {
    entry.key: entry
    for entry in (
        MeasureDef(
            key="followers",
            label="Followers",
            unit="follows",
            definition=(
                "The number of accounts that follow the platform account, as the "
                "platform reports it."
            ),
            changelog=f"{_SLICE}: followers, the headline count of a platform.",
            visibility="public",
        ),
        MeasureDef(
            key="members",
            label="Members",
            unit="follows",
            definition=(
                "The number of members of a chat room or server, as the platform "
                "reports it. A member is not a follower, so it never enters the "
                "audience range."
            ),
            changelog=f"{_SLICE}: members, the headline count of a chat platform.",
            visibility="public",
        ),
        MeasureDef(
            key="followers_change_30d",
            label="Followers, last 30 days",
            unit="percent",
            definition=(
                "The change in followers over the last 30 days, in percent, as the "
                "owner typed it from the platform's own analytics. It can be negative."
            ),
            changelog=f"{_SLICE}: followers_change_30d, the typed 30-day change.",
            visibility="public",
            signed=True,
        ),
        MeasureDef(
            key="analytics_stat",
            label="Platform analytics count",
            unit="count",
            definition=(
                "A count the owner typed from a platform's analytics, named by its "
                "own label; the label says what is counted."
            ),
            changelog=f"{_SLICE}: analytics_stat, a typed analytics tile.",
            visibility="public",
        ),
        MeasureDef(
            key="views",
            label="Views",
            unit="views",
            definition=(
                "The number of times the platform counted a video as viewed in "
                "the period, as the platform's own analytics export shows it. "
                "Added up by the importer from the daily rows of the export."
            ),
            changelog=f"{_SLICE6}: views, from a platform analytics export.",
            visibility="public",
        ),
        MeasureDef(
            key="watch_hours",
            label="Watch time, hours",
            unit="hours",
            definition=(
                "The total time viewers spent watching in the period, in whole "
                "hours, as the platform's own analytics export shows it. Added "
                "up by the importer from the daily rows and rounded half up."
            ),
            changelog=f"{_SLICE6}: watch_hours, from a platform analytics export.",
            visibility="public",
        ),
        MeasureDef(
            key="avg_views_per_item",
            label="Average views per post",
            unit="views",
            definition=(
                "Views divided by posts in the period, worked out by the build "
                "from figures the owner typed."
            ),
            changelog=f"{_SLICE}: avg_views_per_item, worked out from typed figures.",
            visibility="gated",
            computed=True,
            formula=AVERAGE_VIEWS_FORMULA,
        ),
        MeasureDef(
            key="engagement_rate",
            label="Engagement rate",
            unit="percent",
            definition=(
                "Interactions divided by views in the period, as a percent, worked "
                "out by the build from figures the owner typed."
            ),
            changelog=f"{_SLICE}: engagement_rate, worked out from typed figures.",
            visibility="gated",
            computed=True,
            formula=ENGAGEMENT_FORMULA,
        ),
    )
}


def registry_problems(registry: Mapping[str, MeasureDef]) -> list[str]:
    """What is wrong with a registry (empty when it is sound)."""
    found: list[str] = []
    for key, entry in registry.items():
        if entry.key != key:
            found.append(f"{key}: the key field says {entry.key!r}")
        if entry.unit not in UNITS:
            found.append(f"{key}: unit {entry.unit!r} is not a known unit")
        if entry.visibility not in _RANK:
            found.append(f"{key}: visibility must be public, gated or private")
        if len(entry.definition.strip()) < 20:
            found.append(f"{key}: the definition text is missing or too short")
        if not entry.changelog.strip():
            found.append(f"{key}: the changelog line is missing")
        if entry.computed and not entry.formula:
            found.append(f"{key}: a computed measure prints its formula")
        for text in (entry.label, entry.definition, entry.changelog, entry.formula):
            if chr(0x2014) in text or chr(0x2013) in text:
                found.append(f"{key}: no dash punctuation in text")
                break
    return found


# --- groups and default sets (spec 7.1) ---------------------------------------

# The groups the page sorts platforms into. Live platforms share one set, so
# Twitch and Kick always show the same fields in the same order.
GROUPS: Mapping[str, tuple[str, ...]] = {
    "live": ("twitch", "kick"),
    "video": ("youtube", "tiktok", "instagram", "threads"),
    "open": ("mastodon", "bluesky", "pixelfed"),
    "community": ("discord", "matrix"),
}

# What a section with ``show: default`` prints, per group, in this order. A key a
# platform has no number for is left out of the card, never zeroed. Later slices
# add keys (live viewers, fediverse medians) to these sets.
DEFAULT_SETS: Mapping[str, tuple[str, ...]] = {
    "live": (
        "followers",
        "followers_change_30d",
        "analytics_stat",
        "avg_views_per_item",
        "engagement_rate",
    ),
    "video": (
        "followers",
        "followers_change_30d",
        "analytics_stat",
        "avg_views_per_item",
        "engagement_rate",
    ),
    "open": (
        "followers",
        "followers_change_30d",
        "analytics_stat",
        "avg_views_per_item",
        "engagement_rate",
    ),
    "community": ("members",),
    "other": (
        "followers",
        "followers_change_30d",
        "analytics_stat",
        "avg_views_per_item",
        "engagement_rate",
    ),
}


def group_of(platform: str) -> str:
    """The group of a platform slug; ``other`` for one no group names."""
    for group, slugs in GROUPS.items():
        if platform in slugs:
            return group
    return "other"


def default_keys(platform: str) -> tuple[str, ...]:
    """The registry keys a section shows when its ``show`` is ``default``."""
    return DEFAULT_SETS[group_of(platform)]


# --- the measure ---------------------------------------------------------------


@dataclass(frozen=True)
class Measure:
    """One value, one unit, one date, one trust label.

    ``value`` is a whole number, or ``None`` for "not available" (never zero for
    missing). A ``percent`` value is an integer in tenths. ``trust`` is ``None``
    only when the measure was compiled without a config (the PDF text check does
    this); the renderer always compiles with one. ``block`` is the part of the
    page the measure prints in until platform sections replace those parts.
    """

    platform: str
    key: str
    label: str
    unit: str
    value: int | None
    as_of: date | None
    visibility: MeasureVisibility
    block: Block
    in_summary: bool = False
    trust: Trust | None = None
    period: tuple[date, date] | None = None
    formula: str = ""
    note: str = ""
    provenance: str = ""
    source_note: str = ""


def change_tenths(percent: float) -> int:
    """A typed percent change as an integer in tenths, rounded the way the page
    always printed it (``f"{percent:+.1f}"``: the exact value, ties to even)."""
    return int(Decimal(percent).quantize(Decimal("0.1"), ROUND_HALF_EVEN) * 10)


_RANK = {"public": 0, "gated": 1, "private": 2}


def narrow(registry: MeasureVisibility, source: MeasureVisibility) -> MeasureVisibility:
    """The narrower of the registry default and the source field's policy. A
    private source stays private: a measure is never wider than its source."""
    return registry if _RANK[registry] >= _RANK[source] else source


def source_level(field: str) -> MeasureVisibility:
    """Who may see the numbers of one config field: the narrower of the rich
    policy table and the visibility-only table, so changing either one narrows
    the measures (the two tables are one rule and may never disagree upward)."""
    from reach_around.config import kit

    return narrow(kit.KIT_FIELD_POLICY[field].visibility, kit.DISPLAY_POLICY[field])


def _entry(key: str) -> MeasureDef:
    return REGISTRY[key]


def numbers_measures(
    resolved: Iterable[ResolvedMetric], api: frozenset[str] | None = None
) -> list[Measure]:
    """The headline numbers. ``api`` is the set of platforms with a collector;
    ``None`` compiles them without a trust label (see ``Measure``)."""
    from reach_around.config.kit import (
        COMMUNITY_PLATFORMS,
        counts_in_summary,
    )

    source = source_level("metrics")
    out: list[Measure] = []
    for m in resolved:
        key = "members" if m.platform in COMMUNITY_PLATFORMS else "followers"
        entry = _entry(key)
        trust: Trust | None = None
        if api is not None:
            trust = metric_trust(
                m.platform, m.source, m.estimate, api, m.origin, m.as_of
            )
        out.append(
            Measure(
                platform=m.platform,
                key=key,
                label=m.label,
                unit=entry.unit,
                value=m.value,
                as_of=m.as_of,
                visibility=narrow(entry.visibility, source),
                block="headline",
                in_summary=counts_in_summary(m),
                trust=trust,
            )
        )
    return out


def analytics_measures(rows: Iterable[PlatformAnalytics]) -> list[Measure]:
    """Each analytics row: followers, the typed 30-day change and each tile."""
    source = source_level("analytics")
    out: list[Measure] = []
    for row in rows:
        trust = analytics_trust(row.synced_with)

        def make(
            key: str, label: str, value: int, row: Any = row, trust: Trust = trust
        ) -> Measure:
            entry = _entry(key)
            return Measure(
                platform=row.platform,
                key=key,
                label=label,
                unit=entry.unit,
                value=value,
                as_of=row.as_of,
                visibility=narrow(entry.visibility, source),
                block="tile",
                trust=trust,
                source_note=row.synced_with,
            )

        out.append(make("followers", "Followers", row.followers))
        out.append(
            make(
                "followers_change_30d",
                _entry("followers_change_30d").label,
                change_tenths(row.followers_30_day_percent),
            )
        )
        out.extend(make("analytics_stat", s.label, s.value) for s in row.stats)
    return out


def engagement_measures(entries: Iterable[EngagementRow]) -> list[Measure]:
    """Each engagement row: average views and the engagement rate, two measures
    whose value is ``None`` when an input is missing."""
    source = source_level("engagement")
    out: list[Measure] = []
    for entry in entries:
        has_figures = any(
            v is not None for v in (entry.views, entry.posts, entry.interactions)
        )
        period = (
            (entry.period_start, entry.period_end)
            if has_figures and entry.period_start and entry.period_end
            else None
        )
        as_of = entry.as_of if has_figures else None
        provenance = "Owner-supplied" if entry.owner_supplied else ""
        for key, value, note in (
            ("avg_views_per_item", average_views(entry.views, entry.posts), ""),
            (
                "engagement_rate",
                engagement_tenths(entry.interactions, entry.views),
                f"Interactions counted: {entry.counts}" if entry.counts else "",
            ),
        ):
            reg = _entry(key)
            out.append(
                Measure(
                    platform=entry.platform,
                    key=key,
                    label=reg.label,
                    unit=reg.unit,
                    value=value,
                    as_of=as_of,
                    visibility=narrow(reg.visibility, source),
                    block="derived",
                    trust=engagement_trust(),
                    period=period,
                    formula=reg.formula,
                    note=note,
                    provenance=provenance,
                )
            )
    return out


def compile_measures(
    kit: KitConfig, numbers: Numbers | None, api: frozenset[str] | None = None
) -> list[Measure]:
    """Every measure the kit config holds, in page order: headline numbers,
    analytics, engagement. The one place the old fields are read."""
    from reach_around.config.kit import resolve_metrics

    return [
        *numbers_measures(resolve_metrics(kit, numbers), api),
        *analytics_measures(kit.analytics),
        *engagement_measures(kit.engagement),
    ]


def visible(measures: Sequence[Measure], allowed: frozenset[str]) -> list[Measure]:
    """The measures an audience may see (``allowed`` is its set of levels). A
    private measure is never returned, whatever ``allowed`` holds."""
    return [
        m for m in measures if m.visibility != "private" and m.visibility in allowed
    ]
