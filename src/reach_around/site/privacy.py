# SPDX-License-Identifier: Apache-2.0
"""Build the privacy disclosure from the features that are switched on.

The disclosure is data derived from the config, never hand-written per site,
so it cannot say less than the site actually does.
"""

from __future__ import annotations

from dataclasses import dataclass

from reach_around.config.analytics import AGGREGATE_KEEP_DAYS
from reach_around.config.blocks import Live
from reach_around.config.models import SiteConfig

# The wording below is owner-approved copy (issue #598), used verbatim. Only the
# {...} placeholders are filled from the config. Do not reword it here; change
# the approved copy first.
CLICK_WHAT = (
    "When you click a link, the site stores the link, its source label, and the "
    "hour. Labels outside the creator’s fixed list count as “other.” "
    "Missing or unusable labels count as “none.” The record has no IP "
    "address or country. Separate daily totals count clicks by source and by "
    "country. Source and country are never combined or linked to a page view or "
    "person. Global Privacy Control and Do Not Track do not stop click counting."
)
CLICK_WHY = (
    "These records count link clicks and show daily totals by source or country."
)
CLICK_KEPT = (
    "Hourly records are deleted after {RETENTION_DAYS} days. Daily counts are "
    "deleted after 90 days. Monthly totals are kept for up to 25 months."
)

# {SITES} is a whole sentence built from the config (see _page_views), not the
# approved copy's "The counter counts views on {SITES}." (owner direction).
VIEWS_WHAT = (
    "{SITES} Each view records the page, referring site’s name, campaign and "
    "source labels, one of four window sizes, and the hour. It keeps browser and "
    "version, operating system, device kind, language, and the location detail "
    "enabled: country, region, and city. It marks the view as likely human or "
    "automated and gives a short reason. Source labels outside the site’s "
    "list count as “other.” Labels use lower-case letters, digits, and "
    "hyphens, with a 30-character limit. The server creates a daily code from "
    "the address and site name. It cannot reveal the address. Short hashed "
    "counters limit flooding. The IP address and raw browser identification are "
    "never stored. The counter stores nothing in your browser."
)
VIEWS_WHY = (
    "The daily code counts one person once per day on one site. Short counters "
    "stop one client from flooding the counter. Global Privacy Control and Do "
    "Not Track stop all view data from being sent. Small groups are combined as "
    "“other.”"
)
VIEWS_KEPT = (
    "View records are deleted after {RAW_KEEP_DAYS} days. The daily code is "
    "deleted at day’s end. Abuse counters are deleted within about two "
    "hours. Anonymous totals are deleted after {AGGREGATE_KEEP_DAYS} days. "
    "Per-person deletion is not possible because the records do not identify "
    "anyone."
)

LIVE_WHAT = (
    "When the live block is on, the link page and privacy page ask the site’s "
    "server whether the creator is live. They ask when the page opens and every "
    "minute while it stays open. The answer contains each channel’s live "
    "status and a few times. No cookies or identifiers are sent or kept."
)
LIVE_WHY = "These checks show whether the creator’s channels are live."
LIVE_KEPT = "The live checks keep no cookies or identifiers."

PLAYER_WHAT = (
    "A Twitch, Kick, or YouTube player loads only after you click to play it. "
    "The platform then sees your IP address, as it does with any embedded video. "
    "This site does not control what the platform does with it."
)
PLAYER_WHY = "The player loads the stream you chose to play."
PLAYER_KEPT = "The platform’s own rules apply. This site stores nothing."

ALERTS_WHAT = (
    "When owner alerts are on, the site can tell the creator when something "
    "happens, such as a new inquiry. The alert can include your full or masked "
    "IP address, or leave it out, based on the creator’s setting. It goes to "
    "the creator’s own chat or mailbox."
)
ALERTS_WHY = "Alerts tell the creator when an event happens."
ALERTS_KEPT = "Alerts are outside the automatic deletion periods described above."


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
                what=CLICK_WHAT,
                why=CLICK_WHY,
                kept=CLICK_KEPT.replace(
                    "{RETENTION_DAYS}", str(config.privacy.retention_days)
                ),
                sensitive=False,
            )
        )
    if config.analytics.page_views:
        items.append(_page_views(config))
    # Live, player and alerts follow the page views and precede the kit items.
    # The live block and its player facade come from the same block (a site may
    # have one), so one flag shows both. Alerts have a site-config section.
    if any(isinstance(b, Live) for tab in config.tabs for b in tab.blocks):
        items.append(Disclosure(LIVE_WHAT, LIVE_WHY, LIVE_KEPT, sensitive=False))
        items.append(Disclosure(PLAYER_WHAT, PLAYER_WHY, PLAYER_KEPT, sensitive=False))
    if config.alerts.enabled:
        items.append(Disclosure(ALERTS_WHAT, ALERTS_WHY, ALERTS_KEPT, sensitive=False))
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
        if config.kit is not None and config.kit.inquiry is not None:
            items.append(
                Disclosure(
                    what=(
                        "The inquiry form collects a name, optional email, "
                        "brand, budget, and details, then delivers them to the "
                        "owner through a private channel without storing them on "
                        "the site. Submissions are limited per visitor to prevent "
                        "abuse."
                    ),
                    why="So the creator can read and answer your inquiry.",
                    kept=(
                        "Not stored by this site. It is delivered once to the "
                        "creator, who keeps it in their own message channel."
                    ),
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
        what=VIEWS_WHAT.replace("{SITES}", sites),
        why=VIEWS_WHY,
        kept=VIEWS_KEPT.replace(
            "{RAW_KEEP_DAYS}", str(analytics.raw_keep_days)
        ).replace("{AGGREGATE_KEEP_DAYS}", str(AGGREGATE_KEEP_DAYS)),
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
