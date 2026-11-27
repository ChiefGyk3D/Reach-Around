# SPDX-License-Identifier: Apache-2.0
"""The fediverse-first block (kit v2, slice 5, #630).

Mastodon, Bluesky and Pixelfed publish their follower and post counts and the
interaction counts of each public post. The owner types what the public profile
shows, with the profile link; the build works the per-post medians out from the
typed post counts (spec 6.3, decision 10: median first, the mean only beside it
and only when the owner turns it on). Nothing is fetched: a collector for public
post data has its own design issue and lands separately.

Every number here is hand-kept and says so. A count is "as reported by the
server" (spec 14.1, R4): a server can hide or filter counts, so the card links
to the public profile and a sponsor can look for themselves. A value that was not
typed is never zero; the card says it is not provided. What each platform does not
expose at all is stated per platform (``NOT_PROVIDED``), never estimated.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictBool,
    StrictInt,
    field_validator,
)

from reach_around.config.blocks import MAX_COUNT, platform_name
from reach_around.config.measures import Measure, narrow
from reach_around.config.trust import API, HAND_KEPT, Trust

FEDIVERSE_PLATFORMS: tuple[str, ...] = ("mastodon", "bluesky", "pixelfed")
MAX_POSTS = 200
MAX_INTERACTIONS = 10**9
WINDOW_DAYS = 30

# The registry keys this block adds, in print order: the counts, the medians,
# then the means (the means exist only when the owner turns them on).
COUNT_KEYS: tuple[str, ...] = ("posts_total",)
MEDIAN_KEYS: tuple[str, ...] = (
    "median_boosts_30d",
    "median_favorites_30d",
    "median_replies_30d",
)
MEAN_KEYS: tuple[str, ...] = (
    "mean_boosts_30d",
    "mean_favorites_30d",
    "mean_replies_30d",
)
# Keys whose absence the card states as "not provided".
STATED_KEYS: tuple[str, ...] = ("followers", *COUNT_KEYS, *MEDIAN_KEYS, *MEAN_KEYS)

_FIELDS = ("boosts", "favorites", "replies")

# What each platform calls the three interactions of a post.
NOUNS: Mapping[str, tuple[str, str, str]] = {
    "mastodon": ("boosts", "favorites", "replies"),
    "bluesky": ("reposts", "likes", "replies"),
    "pixelfed": ("shares", "likes", "comments"),
}

# What each platform does not expose to anyone, so the page says so rather than
# leaving a gap (spec 6.3; the repo's research notes for Mastodon and Bluesky, and
# Pixelfed shows no insights to a sponsor at all).
NOT_PROVIDED: Mapping[str, tuple[str, ...]] = {
    "mastodon": ("impressions", "reach", "demographics"),
    "bluesky": ("impressions", "reach", "demographics"),
    "pixelfed": ("impressions", "reach", "demographics", "any insights"),
}

SERVER_NOTE = "Counts are as reported by the server."


def median_of(values: Sequence[int]) -> int:
    """The median as a whole number, halves rounded up."""
    if not values:
        raise ValueError("a median needs at least one value; there are no values")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid] + 1) // 2


def mean_of(values: Sequence[int]) -> int:
    """The mean as a whole number, halves rounded up."""
    if not values:
        raise ValueError("a mean needs at least one value; there are no values")
    return (2 * sum(values) + len(values)) // (2 * len(values))


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


Count = Annotated[StrictInt, Field(ge=0, le=MAX_INTERACTIONS)]


class FediversePost(_Model):
    """The public counts of one post in the 30 days ending on ``as_of``. All
    three are typed: a post with no replies has 0 replies, a real zero."""

    boosts: Count
    favorites: Count
    replies: Count


class FediverseEntry(_Model):
    """What one public profile shows, typed by the owner on ``as_of``.

    ``profile_url`` is the re-check link every number of the card carries.
    ``followers`` and ``posts`` are the profile's own counts. ``interactions`` is
    one row per public post in the 30 days ending on ``as_of``. Anything left out
    prints "not provided", never zero.
    """

    platform: Literal["mastodon", "bluesky", "pixelfed"]
    profile_url: str
    as_of: date
    followers: Annotated[StrictInt, Field(ge=0, le=MAX_COUNT)] | None = None
    posts: Annotated[StrictInt, Field(ge=0, le=MAX_COUNT)] | None = None
    interactions: list[FediversePost] = Field(
        default_factory=list, max_length=MAX_POSTS
    )
    show_mean: StrictBool = False
    # True only for an entry the collector fed (#940). A private attribute, so
    # the config cannot set it: a typed entry is never labeled API.
    _collected: bool = PrivateAttr(default=False)

    @property
    def collected(self) -> bool:
        return self._collected

    @field_validator("profile_url")
    @classmethod
    def _https_profile(cls, value: str) -> str:
        from reach_around.config.models import check_https_url

        return check_https_url(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_is_a_day(cls, value: object) -> object:
        from reach_around.config.kit import _iso_date

        return _iso_date(value)


def entries_problem(entries: Iterable[FediverseEntry]) -> str | None:
    """A message when a platform is listed twice, else ``None``."""
    seen: set[str] = set()
    for item in entries:
        if item.platform in seen:
            return f"the fediverse entry for {item.platform!r} is listed more than once"
        seen.add(item.platform)
    return None


def label_of(platform: str, key: str) -> str:
    """The printed label of a fediverse key on a platform, in its own words."""
    from reach_around.config.measures import REGISTRY

    if key in MEDIAN_KEYS + MEAN_KEYS:
        noun = NOUNS[platform][
            MEDIAN_KEYS.index(key) if key in MEDIAN_KEYS else MEAN_KEYS.index(key)
        ]
        word = "Median" if key in MEDIAN_KEYS else "Mean"
        return f"{word} {noun} per post (30 days)"
    return REGISTRY[key].label


def _trust(key: str, collected: bool = False) -> Trust:
    if collected:
        if key in MEDIAN_KEYS + MEAN_KEYS:
            return Trust(
                API, "Worked out from per-post counts the collector read from the API"
            )
        return Trust(API, "Read from the platform's public API")
    if key in MEDIAN_KEYS + MEAN_KEYS:
        return Trust(HAND_KEPT, "Worked out from per-post counts typed by the owner")
    return Trust(HAND_KEPT, "Typed by the owner from the public profile")


def fediverse_measures(entries: Iterable[FediverseEntry]) -> list[Measure]:
    """Each entry's measures: followers, posts, the medians and, when enabled, the
    means. A value that was not typed produces no measure (never a zero)."""
    from reach_around.config.measures import REGISTRY, source_level

    source = source_level("fediverse")
    out: list[Measure] = []
    for item in entries:
        period = (item.as_of - timedelta(days=WINDOW_DAYS - 1), item.as_of)

        def make(
            key: str,
            value: int,
            item: FediverseEntry = item,
            period: tuple[date, date] = period,
        ) -> Measure:
            reg = REGISTRY[key]
            windowed = key in MEDIAN_KEYS + MEAN_KEYS
            return Measure(
                platform=item.platform,
                key=key,
                label=label_of(item.platform, key),
                unit=reg.unit,
                value=value,
                as_of=item.as_of,
                visibility=narrow(reg.visibility, source),
                block="fediverse",
                trust=_trust(key, item.collected),
                period=period if windowed else None,
                formula=reg.formula,
            )

        if item.followers is not None:
            out.append(make("followers", item.followers))
        if item.posts is not None:
            out.append(make("posts_total", item.posts))
        if item.interactions:
            columns = [
                [getattr(post, name) for post in item.interactions] for name in _FIELDS
            ]
            out.extend(
                make(key, median_of(column))
                for key, column in zip(MEDIAN_KEYS, columns, strict=True)
            )
            if item.show_mean:
                out.extend(
                    make(key, mean_of(column))
                    for key, column in zip(MEAN_KEYS, columns, strict=True)
                )
    return out


def not_provided_text(platform: str) -> str:
    """The registry-known absences of a platform, as one sentence."""
    return (
        f"Not provided by {platform_name(platform)}: "
        + ", ".join(NOT_PROVIDED[platform])
        + "."
    )
