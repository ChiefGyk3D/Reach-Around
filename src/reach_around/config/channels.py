# SPDX-License-Identifier: Apache-2.0
"""PeerTube, Lemmy, podcast, newsletter and blog measures (kit v2, slice 8, #633).

These five have no collector. The owner types what the instance, the podcast host,
the sending service or the site's own analytics shows, with the date. Nothing is
fetched: a collector for any of them has its own design issue (and, where a
platform's terms apply, a recorded human terms read) before any collector code.

Every figure is hand-kept and says so. A count is "as reported by" whoever
produced it (``NOTES``): PeerTube's federated-view completeness is not verified, so
the card says "as reported by the instance" and never claims more. A newsletter
shows clicks, not opens (opens are unreliable since Apple Mail Privacy Protection).
A value that was not typed is never zero: the card says it is not provided.
Lemmy shows only the community fields the research could confirm; its post-level
fields are not read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from reach_around.config.blocks import MAX_COUNT, platform_name
from reach_around.config.measures import Measure, narrow
from reach_around.config.trust import HAND_KEPT, Trust

WINDOW_DAYS = 30

# The typed field -> the registry key it compiles to.
FIELD_KEY: Mapping[str, str] = {
    "subscribers": "subscribers",
    "active_users_month": "lemmy_active_users_month",
    "watch_time_hours": "peertube_watch_time_30d",
    "avg_watch_seconds": "peertube_avg_watch_time",
    "unique_viewers": "peertube_unique_viewers_30d",
    "downloads": "downloads_30d",
    "delivered": "newsletter_delivered",
    "clicks": "newsletter_clicks",
    "views": "blog_views_30d",
}

# The fields each platform takes, in print order. Anything else is refused.
ALLOWED: Mapping[str, tuple[str, ...]] = {
    "peertube": (
        "subscribers",
        "watch_time_hours",
        "avg_watch_seconds",
        "unique_viewers",
    ),
    "lemmy": ("subscribers", "active_users_month"),
    "podcast": ("downloads",),
    "newsletter": ("subscribers", "delivered", "clicks"),
    "blog": ("views",),
}

# The registry keys of the 30-day figures: they print their period.
WINDOWED: frozenset[str] = frozenset(
    {
        "peertube_watch_time_30d",
        "peertube_unique_viewers_30d",
        "downloads_30d",
        "newsletter_delivered",
        "newsletter_clicks",
        "blog_views_30d",
    }
)
# Counts of people keep the privacy floor ("Under 5"); the rest print as typed.
PEOPLE_KEYS: frozenset[str] = frozenset(
    {"subscribers", "lemmy_active_users_month", "peertube_unique_viewers_30d"}
)
# Public pages the owner can link: the re-check link is required where one exists.
PUBLIC_PAGE: frozenset[str] = frozenset({"peertube", "lemmy"})

# Who reports the count, said on the card. "As reported by": the page never
# claims completeness it cannot verify.
NOTES: Mapping[str, str] = {
    "peertube": "Counts are as reported by the instance.",
    "lemmy": "Counts are as reported by the instance.",
    "podcast": "Downloads are as reported by the podcast host.",
    "newsletter": (
        "Counts are as reported by the sending service. Clicks are shown, not "
        "opens: opens are unreliable since Apple Mail Privacy Protection."
    ),
    "blog": "Views are as reported by the site's own analytics.",
}

# What a platform does not give a sponsor, stated rather than left as a gap.
NOT_PROVIDED: Mapping[str, tuple[str, ...]] = {"newsletter": ("opens",)}

Count = Annotated[StrictInt, Field(ge=0, le=MAX_COUNT)]


class ChannelEntry(BaseModel):
    """What one channel shows, typed by the owner on ``as_of``.

    ``url`` is the public page the re-check link points at (required for PeerTube
    and Lemmy, which have one; optional for the rest). The 30-day figures cover the
    30 days ending on ``as_of``. A figure left out prints "not provided", never zero.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    platform: Literal["peertube", "lemmy", "podcast", "newsletter", "blog"]
    as_of: date
    url: str | None = None
    subscribers: Count | None = None
    active_users_month: Count | None = None
    watch_time_hours: Count | None = None
    avg_watch_seconds: Count | None = None
    unique_viewers: Count | None = None
    downloads: Count | None = None
    delivered: Count | None = None
    clicks: Count | None = None
    views: Count | None = None

    @field_validator("url")
    @classmethod
    def _https_page(cls, value: str | None) -> str | None:
        from reach_around.config.models import check_https_url

        return None if value is None else check_https_url(value)

    @field_validator("as_of", mode="before")
    @classmethod
    def _as_of_is_a_day(cls, value: object) -> object:
        from reach_around.config.kit import _iso_date

        return _iso_date(value)

    def model_post_init(self, __context: Any) -> None:
        allowed = ALLOWED[self.platform]
        for name in FIELD_KEY:
            if name not in allowed and getattr(self, name) is not None:
                raise ValueError(
                    f"{self.platform} does not take {name}; it takes "
                    + ", ".join(allowed)
                )
        if self.platform in PUBLIC_PAGE and self.url is None:
            raise ValueError(
                f"{self.platform} has a public page: url is required so the "
                "counts can be re-checked"
            )
        if all(getattr(self, name) is None for name in allowed):
            raise ValueError(
                f"{self.platform} needs at least one figure: " + ", ".join(allowed)
            )


def entries_problem(entries: Iterable[ChannelEntry]) -> str | None:
    """A message when a platform is listed twice, else ``None``."""
    seen: set[str] = set()
    for item in entries:
        if item.platform in seen:
            return f"the channel entry for {item.platform!r} is listed more than once"
        seen.add(item.platform)
    return None


def channel_measures(entries: Iterable[ChannelEntry]) -> list[Measure]:
    """Each entry's typed figures as measures, in the platform's print order. A
    value that was not typed produces no measure (never a zero)."""
    from reach_around.config.measures import REGISTRY, source_level

    source = source_level("channels")
    out: list[Measure] = []
    for item in entries:
        period = (item.as_of - timedelta(days=WINDOW_DAYS - 1), item.as_of)
        for name in ALLOWED[item.platform]:
            value = getattr(item, name)
            if value is None:
                continue
            key = FIELD_KEY[name]
            reg = REGISTRY[key]
            out.append(
                Measure(
                    platform=item.platform,
                    key=key,
                    label=reg.label,
                    unit=reg.unit,
                    value=value,
                    as_of=item.as_of,
                    visibility=narrow(reg.visibility, source),
                    block="channels",
                    trust=Trust(HAND_KEPT, "Typed by the owner from the source shown"),
                    period=period if key in WINDOWED else None,
                )
            )
    return out


def stated_keys(platform: str) -> tuple[str, ...]:
    """The registry keys a channel card states as "not provided" when untyped."""
    return tuple(FIELD_KEY[name] for name in ALLOWED[platform])


def not_provided_text(platform: str) -> str:
    """What the platform does not give, as one sentence ("" when it gives all)."""
    absent = NOT_PROVIDED.get(platform)
    if not absent:
        return ""
    return f"Not provided by {platform_name(platform)}: " + ", ".join(absent) + "."
