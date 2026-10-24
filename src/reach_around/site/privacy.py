# SPDX-License-Identifier: Apache-2.0
"""Build the privacy disclosure from the features that are switched on.

The disclosure is data derived from the config, never hand-written per site,
so it cannot say less than the site actually does.
"""

from __future__ import annotations

from dataclasses import dataclass

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
                    "A count of link clicks: which link was clicked and the "
                    "visitor's country only, with no IP address stored."
                ),
                why="To see which links are useful. The counts are anonymous.",
                kept=f"{config.privacy.retention_days} days",
                sensitive=False,
            )
        )
    if config.analytics.page_views:
        items.append(_page_views())
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


def _page_views() -> Disclosure:
    return Disclosure(
        what=(
            "A count of page views. Each view records the page, the referring "
            "site's name only (never the full address), a campaign label if "
            "the link you followed carried one (the short utm_source label: at "
            "most 30 characters of lower-case letters, digits and hyphens), and "
            "your window width as one of four sizes. From your request the "
            "server works out and keeps only: your browser and operating "
            "system, the kind of device, your language, and your country, "
            "region and city "
            "(as far as the counter is set to record them). It also marks the "
            "view as likely a person or likely automated, and keeps a daily "
            "code made from your address that changes every day and cannot be "
            "turned back into the address, so one person is counted once a "
            "day. Your IP address is never stored, and nothing is stored in your "
            "browser."
        ),
        why=(
            "To see which pages are read and where visitors come from. Nothing "
            "is sent if your browser sends Global Privacy Control or Do Not "
            "Track."
        ),
        kept=(
            "Each view record is deleted after a few days (7 by default); the "
            "daily code is deleted by the daily cleanup; only anonymous totals "
            "are kept longer"
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
