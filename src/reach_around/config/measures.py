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
    live_trust,
    metric_trust,
)

if TYPE_CHECKING:
    from reach_around.config.kit import (
        EngagementRow,
        KitConfig,
        LiveRow,
        PlatformAnalytics,
        ResolvedMetric,
    )
    from reach_around.config.numbers import Numbers

MeasureVisibility = Literal["public", "gated", "private"]
Block = Literal["headline", "tile", "derived", "live", "fediverse", "channels"]

UNITS: tuple[str, ...] = (
    "follows",
    "viewers",
    "hours",
    "seconds",
    "views",
    "interactions",
    "downloads",
    "emails",
    "clicks",
    "percent",
    "count",
)

_SLICE = "Added in kit v2 slice 3 (#628)"
_LIVE = "Added in kit v2 slice 4 (#629)"

_SLICE5 = "Added in kit v2 slice 5 (#630)"
_SLICE8 = "Added in kit v2 slice 8 (#633)"

# The formulas of the fediverse block's computed measures (spec 6.3).
MEDIAN_FORMULA = (
    "The middle value of the per-post counts over the 30 days ending on the date "
    "shown; with an even number of posts, the two middle values averaged and "
    "rounded half up."
)
MEAN_FORMULA = (
    "The per-post counts added up and divided by the number of posts over the 30 "
    "days ending on the date shown, rounded half up."
)


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
            key="avg_ccv",
            label="Average concurrent viewers",
            unit="viewers",
            definition=(
                "The average number of people watching at the same moment while "
                "the channel was live in the period, as the platform's own "
                "dashboard shows it. Typed by the owner."
            ),
            changelog=f"{_LIVE}: avg_ccv, the typed average concurrent viewers.",
            visibility="public",
        ),
        MeasureDef(
            key="peak_ccv",
            label="Peak concurrent viewers",
            unit="viewers",
            definition=(
                "The highest number of people watching at the same moment while "
                "the channel was live in the period, as the platform's own "
                "dashboard shows it. Typed by the owner."
            ),
            changelog=f"{_LIVE}: peak_ccv, the typed peak concurrent viewers.",
            visibility="public",
        ),
        MeasureDef(
            key="hours_watched",
            label="Hours watched",
            unit="hours",
            definition=(
                "The total time viewers spent watching the channel in the "
                "period, in whole hours, as the platform's own dashboard shows "
                "it. Typed by the owner."
            ),
            changelog=f"{_LIVE}: hours_watched, the typed hours watched.",
            visibility="public",
        ),
        MeasureDef(
            key="unique_chatters",
            label="Unique chatters",
            unit="viewers",
            definition=(
                "The number of different accounts that sent at least one chat "
                "message in the period, as the platform's own dashboard shows "
                "it. Typed by the owner."
            ),
            changelog=f"{_LIVE}: unique_chatters, the typed unique chatters.",
            visibility="public",
        ),
        MeasureDef(
            key="followers_gained",
            label="Followers gained",
            unit="follows",
            definition=(
                "The number of accounts that began following the channel in the "
                "period, as the platform's own dashboard shows it. Typed by the "
                "owner."
            ),
            changelog=f"{_LIVE}: followers_gained, the typed followers gained.",
            visibility="public",
        ),
        MeasureDef(
            key="hours_streamed_per_week",
            label="Hours streamed per week",
            unit="hours",
            definition=(
                "The hours the channel was live in an average week of the "
                "period, in whole hours, as the platform's own dashboard shows "
                "it. Typed by the owner."
            ),
            changelog=(
                f"{_LIVE}: hours_streamed_per_week, the typed weekly hours live."
            ),
            visibility="public",
        ),
        MeasureDef(
            key="posts_total",
            label="Public posts",
            unit="count",
            definition=(
                "The number of public posts the account has made, as the server "
                "reports it on the public profile and the owner typed it."
            ),
            changelog=f"{_SLICE5}: posts_total, the typed public post count.",
            visibility="public",
        ),
        MeasureDef(
            key="median_boosts_30d",
            label="Median boosts per post",
            unit="interactions",
            definition=(
                "The middle count of boosts across the public posts of the last 30 "
                "days, worked out by the build from per-post counts the owner typed "
                "from the public profile. Bluesky reads reposts, likes and replies; "
                "Pixelfed shares, likes and comments."
            ),
            changelog=f"{_SLICE5}: median_boosts_30d, the per-post median.",
            visibility="public",
            computed=True,
            formula=MEDIAN_FORMULA,
        ),
        MeasureDef(
            key="median_favorites_30d",
            label="Median favorites per post",
            unit="interactions",
            definition=(
                "The middle count of favorites across the public posts of the last 30 "
                "days, worked out by the build from per-post counts the owner typed "
                "from the public profile. Bluesky reads reposts, likes and replies; "
                "Pixelfed shares, likes and comments."
            ),
            changelog=f"{_SLICE5}: median_favorites_30d, the per-post median.",
            visibility="public",
            computed=True,
            formula=MEDIAN_FORMULA,
        ),
        MeasureDef(
            key="median_replies_30d",
            label="Median replies per post",
            unit="interactions",
            definition=(
                "The middle count of replies across the public posts of the last 30 "
                "days, worked out by the build from per-post counts the owner typed "
                "from the public profile. Bluesky reads reposts, likes and replies; "
                "Pixelfed shares, likes and comments."
            ),
            changelog=f"{_SLICE5}: median_replies_30d, the per-post median.",
            visibility="public",
            computed=True,
            formula=MEDIAN_FORMULA,
        ),
        MeasureDef(
            key="mean_boosts_30d",
            label="Mean boosts per post",
            unit="interactions",
            definition=(
                "The average count of boosts across the public posts of the last "
                "30 days, shown only beside the median and only when the owner "
                "turns it on; one viral post can pull it far above the median."
            ),
            changelog=f"{_SLICE5}: mean_boosts_30d, the optional per-post mean.",
            visibility="public",
            computed=True,
            formula=MEAN_FORMULA,
        ),
        MeasureDef(
            key="mean_favorites_30d",
            label="Mean favorites per post",
            unit="interactions",
            definition=(
                "The average count of favorites across the public posts of the last "
                "30 days, shown only beside the median and only when the owner "
                "turns it on; one viral post can pull it far above the median."
            ),
            changelog=f"{_SLICE5}: mean_favorites_30d, the optional per-post mean.",
            visibility="public",
            computed=True,
            formula=MEAN_FORMULA,
        ),
        MeasureDef(
            key="mean_replies_30d",
            label="Mean replies per post",
            unit="interactions",
            definition=(
                "The average count of replies across the public posts of the last "
                "30 days, shown only beside the median and only when the owner "
                "turns it on; one viral post can pull it far above the median."
            ),
            changelog=f"{_SLICE5}: mean_replies_30d, the optional per-post mean.",
            visibility="public",
            computed=True,
            formula=MEAN_FORMULA,
        ),
        MeasureDef(
            key="subscribers",
            label="Subscribers",
            unit="follows",
            definition=(
                "The number of accounts subscribed to a PeerTube channel, a Lemmy "
                "community or a newsletter, as the instance or the sending service "
                "reports it and the owner typed it. A subscriber is not a follower, so "
                "it never enters the audience range."
            ),
            changelog=f"{_SLICE8}: subscribers, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="lemmy_active_users_month",
            label="Active users per month",
            unit="count",
            definition=(
                "The number of active users the Lemmy community reports for the last "
                "month, as the instance reports it and the owner typed it."
            ),
            changelog=f"{_SLICE8}: lemmy_active_users_month, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="peertube_watch_time_30d",
            label="Watch time, hours (30 days)",
            unit="hours",
            definition=(
                "The hours watched across the channel's videos in the last 30 days, in "
                "whole hours, as the instance's video statistics report it and the "
                "owner typed it."
            ),
            changelog=f"{_SLICE8}: peertube_watch_time_30d, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="peertube_avg_watch_time",
            label="Average watch time (seconds)",
            unit="seconds",
            definition=(
                "The average time one view of the channel's videos lasted, in whole "
                "seconds, as the instance's video statistics report it and the owner "
                "typed it."
            ),
            changelog=f"{_SLICE8}: peertube_avg_watch_time, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="peertube_unique_viewers_30d",
            label="Unique viewers (30 days)",
            unit="viewers",
            definition=(
                "The number of different viewers of the channel's videos in the last "
                "30 days, as the instance's video statistics report it and the owner "
                "typed it."
            ),
            changelog=f"{_SLICE8}: peertube_unique_viewers_30d, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="downloads_30d",
            label="Downloads (30 days)",
            unit="downloads",
            definition=(
                "The number of downloads of the podcast's episodes in the last 30 "
                "days, as the podcast host counts them and the owner typed it."
            ),
            changelog=f"{_SLICE8}: downloads_30d, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="newsletter_delivered",
            label="Emails delivered (30 days)",
            unit="emails",
            definition=(
                "The number of newsletter emails delivered in the last 30 days, as the "
                "sending service reports it and the owner typed it."
            ),
            changelog=f"{_SLICE8}: newsletter_delivered, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="newsletter_clicks",
            label="Email clicks (30 days)",
            unit="clicks",
            definition=(
                "The number of clicks on links in the newsletter in the last 30 days, "
                "as the sending service reports it and the owner typed it. Clicks are "
                "shown instead of opens, which are unreliable."
            ),
            changelog=f"{_SLICE8}: newsletter_clicks, the typed figure.",
            visibility="public",
        ),
        MeasureDef(
            key="blog_views_30d",
            label="Blog views (30 days)",
            unit="views",
            definition=(
                "The number of page views of the blog in the last 30 days, as the "
                "site's own analytics report them and the owner typed it."
            ),
            changelog=f"{_SLICE8}: blog_views_30d, the typed figure.",
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


# The seven live measures, in the one order both live platforms print them (spec
# 6.3). ``followers`` is the platform's headline count; the other six are typed
# from the platform's own dashboard (``kit.live``).
LIVE_KEYS: tuple[str, ...] = (
    "followers",
    "avg_ccv",
    "peak_ccv",
    "hours_watched",
    "unique_chatters",
    "followers_gained",
    "hours_streamed_per_week",
)


# --- groups and default sets (spec 7.1) ---------------------------------------

# The groups the page sorts platforms into. Live platforms share one set, so
# Twitch and Kick always show the same fields in the same order.
GROUPS: Mapping[str, tuple[str, ...]] = {
    "live": ("twitch", "kick"),
    "video": ("youtube", "tiktok", "instagram", "threads"),
    "open": ("mastodon", "bluesky", "pixelfed", "peertube", "lemmy"),
    "audio": ("podcast", "newsletter", "blog"),
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
        "subscribers",
        "posts_total",
        "followers_change_30d",
        "median_boosts_30d",
        "median_favorites_30d",
        "median_replies_30d",
        "mean_boosts_30d",
        "mean_favorites_30d",
        "mean_replies_30d",
        "peertube_watch_time_30d",
        "peertube_avg_watch_time",
        "peertube_unique_viewers_30d",
        "lemmy_active_users_month",
        "analytics_stat",
        "avg_views_per_item",
        "engagement_rate",
    ),
    "audio": (
        "subscribers",
        "downloads_30d",
        "newsletter_delivered",
        "newsletter_clicks",
        "blog_views_30d",
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


def live_measures(rows: Iterable[LiveRow]) -> list[Measure]:
    """The typed live figures: one measure per figure the owner entered, each with
    its period and the hand-kept label. A figure left out is not a measure; it is
    never zeroed."""
    from reach_around.config.kit import KIT_FIELD_POLICY

    source = KIT_FIELD_POLICY["live"].visibility
    out: list[Measure] = []
    for row in rows:
        period = (
            (row.period_start, row.period_end)
            if row.period_start and row.period_end
            else None
        )
        for key in LIVE_KEYS[1:]:
            value = getattr(row, key)
            if value is None:
                continue
            reg = _entry(key)
            out.append(
                Measure(
                    platform=row.platform,
                    key=key,
                    label=reg.label,
                    unit=reg.unit,
                    value=value,
                    as_of=row.as_of,
                    visibility=narrow(reg.visibility, source),
                    block="live",
                    trust=live_trust(),
                    period=period,
                )
            )
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
    analytics, engagement, the fediverse and channel blocks. The one place the
    old fields are read."""
    from reach_around.config.channels import channel_measures
    from reach_around.config.fediverse import fediverse_measures
    from reach_around.config.kit import resolve_metrics

    return [
        *numbers_measures(resolve_metrics(kit, numbers), api),
        *analytics_measures(kit.analytics),
        *live_measures(kit.live),
        *engagement_measures(kit.engagement),
        *fediverse_measures(kit.fediverse),
        *channel_measures(kit.channels),
    ]


def visible(measures: Sequence[Measure], allowed: frozenset[str]) -> list[Measure]:
    """The measures an audience may see (``allowed`` is its set of levels). A
    private measure is never returned, whatever ``allowed`` holds."""
    return [
        m for m in measures if m.visibility != "private" and m.visibility in allowed
    ]
