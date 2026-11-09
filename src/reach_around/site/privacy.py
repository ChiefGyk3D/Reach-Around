# SPDX-License-Identifier: Apache-2.0
"""Build the privacy disclosure from the features that are switched on.

The disclosure is data derived from the config, never hand-written per site,
so it cannot say less than the site actually does.
"""

from __future__ import annotations

from dataclasses import dataclass

from reach_around.config.analytics import AGGREGATE_KEEP_DAYS
from reach_around.config.models import SiteConfig


@dataclass(frozen=True)
class Disclosure:
    """One thing the site collects, in plain words."""

    what: str
    why: str
    kept: str
    sensitive: bool


def collected_items(config: SiteConfig) -> list[Disclosure]:
    """List everything the enabled features collect."""
    items: list[Disclosure] = []
    if config.features.click_tracking:
        items.append(
            Disclosure(
                what=(
                    "A count of link clicks: which link was clicked, the "
                    "visitor's country, and the short source label the link "
                    "you came in on carried (from a fixed list), with the "
                    "time rounded to the hour, with no IP address stored. "
                    "Totals of these counts per day and per month, which hold "
                    "no time finer than a day, are kept for up to 25 months."
                ),
                why="To see which links are useful. The counts are anonymous.",
                kept=f"{config.privacy.retention_days} days",
                sensitive=False,
            )
        )
    if config.analytics.page_views:
        items.append(_page_views(config))
    if config.features.kit_gate:
        items.append(
            Disclosure(
                what=(
                    "The email address you enter to unlock the kit, so the "
                    "creator can send it to you."
                ),
                why="To deliver the kit and to contact you about it.",
                kept=f"{config.privacy.lead_retention_days} days",
                sensitive=False,
            )
        )
        items.append(
            Disclosure(
                what="A cookie that remembers the kit is unlocked in this browser.",
                why="So you do not have to enter your email again on a later visit.",
                kept="In your browser until it expires or you delete it",
                sensitive=False,
            )
        )
    return items


def _page_views(config: SiteConfig) -> Disclosure:
    analytics = config.analytics
    if analytics.other_sites:
        sites = (
            "The same counter also counts "
            + ", ".join(analytics.other_sites)
            + ". The counts for each site are kept separately, and the daily "
            "code below is different on each site, so the sites cannot be "
            "linked."
        )
    else:
        sites = (
            "This counter counts this site only. If it ever counts another "
            "site, the counts are kept separately and this page names it."
        )
    return Disclosure(
        what=(
            "A count of page views. Each view records the page, the referring "
            "site's name only (never the full address), a campaign label if "
            "the link you followed carried one (the short utm_source label: at "
            "most 30 characters of lower-case letters, digits and hyphens), a "
            "source label if the link carried one (the short s= label: the "
            "same form, and anything not on this site's own list is counted "
            "only as other), and your window width as one of four sizes. From "
            "your request the server works out and keeps only: your browser and "
            "its version, your operating system, the kind of device, your language, "
            "and "
            "your country, region and city (as far as the counter is set to "
            "record them). It also marks the view as likely a person or likely "
            "automated, with a short reason, and works out a network type when "
            "it can (for example: looks like a hosting provider, a VPN or "
            "proxy, or Tor). That work is done on our own server from lists we "
            "hold, so no address is sent to anyone else. The time of a view is "
            "kept to the hour only. It also keeps a daily code made from your "
            "address and the name of the site that changes every day and "
            "cannot be turned back into the address, so one person is counted "
            "once a day on that site. " + sites + " Your IP address is never "
            "stored, your raw browser identification is not stored, and "
            "nothing is stored in your browser."
        ),
        why=(
            "To see which pages are read and where visitors come from. Nothing "
            "is sent if your browser sends Global Privacy Control or Do Not "
            "Track. Totals for small groups are merged into 'other'. Nothing "
            "in these records identifies a person, so we cannot find and "
            "delete the views of one person on request; the individual "
            f"records are deleted automatically after {analytics.raw_keep_days} "
            "days."
        ),
        kept=(
            f"Each view record is deleted after {analytics.raw_keep_days} days; "
            "the daily code is deleted at the end of the same day; only "
            f"anonymous totals are kept longer, for up to {AGGREGATE_KEEP_DAYS} "
            "days"
        ),
        sensitive=False,
    )


DEFAULT_INTRO = (
    "This page lists everything this site collects, why, and for how long. "
    "It is generated from the features the site uses."
)


def intro_text(config: SiteConfig) -> str:
    """The creator's own wording if set, else the neutral default."""
    return config.privacy.intro or DEFAULT_INTRO


def sets_cookies(config: SiteConfig) -> bool:
    """True when an enabled feature stores a cookie in the visitor's browser."""
    return config.features.kit_gate
