# SPDX-License-Identifier: Apache-2.0
"""Build the privacy disclosure from the features that are switched on.

The disclosure is data derived from the config, never hand-written per site,
so it cannot say less than the site actually does.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from reach_around.config.analytics import AGGREGATE_KEEP_DAYS, MAX_RAW_KEEP_DAYS
from reach_around.config.blocks import Live
from reach_around.config.models import SiteConfig
from reach_around.config.privacy import (
    DEFAULT_GEO_LEVEL,
    MONTHLY_KEEP_MONTHS,
    RAW_CLICK_KEEP_DAYS,
    missing_notice_fields,
)

# The wording below is owner-approved copy (issue #598), used verbatim. Only the
# {...} placeholders are filled from the config. Do not reword it here; change
# the approved copy first.
CLICK_WHAT = (
    "When you click a link, the site stores the link, its source label, and the "
    "hour. Labels outside the creator’s fixed list count as “other.” "
    "Missing or unusable labels count as “none.” The record has no IP "
    "address or country. Separate daily totals count clicks by source and by "
    "country. Source and country are never combined or linked to a page view or "
    "person. The site honors Global Privacy Control and Do Not Track for link "
    "clicks. If your browser sends either signal, the click is not counted or "
    "stored, and the site's own script sends nothing. If scripts are blocked or "
    "fail, your browser may still send a small request to the site. The server "
    "throws it away without storing anything about it. The link still opens "
    "normally."
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
    "enabled: {LOCATION}. It marks the view as likely human or "
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
# The hard cap is the Worker's rule for records its totals have not yet counted:
# min(3 x the keep window, the 90 day ceiling) (worker/src/viewretention.ts).
VIEWS_KEPT = (
    "View records are normally deleted after {RAW_KEEP_DAYS} days. If the site "
    "has not yet added a day's records to its daily totals, the records are "
    "kept longer so the totals are not lost. After {RAW_HARD_CAP_DAYS} days, "
    "they are due for deletion no matter what. They are removed during the "
    "next cleanup runs, which can occasionally be delayed for a short time. "
    "The daily code is deleted at the end of each day. Abuse counters are "
    "deleted within about two hours. Anonymous totals are deleted after "
    "{AGGREGATE_KEEP_DAYS} days. Records do not identify anyone, so records "
    "for a specific person cannot be deleted.{CITY_KEPT}"
)
# TODO-copy (#778): the three location sentences below and CITY_KEPT are
# visitor-facing; the owner has them authored by a general model later. The
# facts they state are fixed by the Worker: a stored view holds the country and
# at most the region; at level city the city is counted only in a daily total,
# with small counts combined into the region, and that total follows the view
# records' deletion schedule (worker/src/viewretention.ts).
LOCATION_BY_LEVEL = {
    "country": "country only",
    "region": "country and region",
    "city": (
        "country and region with each view, and city only in a daily total that "
        "is not linked to any other detail of the view"
    ),
}
CITY_KEPT = " City totals are deleted on the same schedule as view records."
# The Worker's multiplier for unrolled raw rows and its ceiling (kept equal by a test).
RAW_HARD_CAP_FACTOR = 3

LIVE_WHAT = (
    "When the live block is on, the link page and privacy page ask the site’s "
    "server whether the creator is live. They ask when the page opens and every "
    "minute while it stays open. The answer contains each channel’s live "
    "status and a few times. No cookies or identifiers are sent or kept."
)
LIVE_WHY = "These checks show whether the creator’s channels are live."
LIVE_KEPT = "The live checks keep no cookies or identifiers."

# PLAYER_* and ALERTS_* are the corrected copy (second copy file, 2026-11-19);
# it replaces the first file's versions.
PLAYER_WHAT = (
    "When the live block is on, the site can show a Twitch or Kick stream "
    "player. The player loads only after you click to play. The platform then "
    "sees your IP address."
)
PLAYER_WHY = "The player lets you watch the stream on this site."
PLAYER_KEPT = (
    "The platform’s own rules apply. The site stores nothing about the player "
    "or your IP address."
)

ALERTS_WHAT = (
    "When alerts are on, the site can notify the creator when someone unlocks "
    "the kit or a new lead is stored. A notice has fixed wording, the UTC time, "
    "and a count. It may include your full or masked email address. It may "
    "leave the address out and say “a visitor.” It never includes an IP "
    "address, browser identification, cookie, token, country, or code derived "
    "from you."
)
ALERTS_WHY = (
    "The notice tells the creator that a kit was unlocked or a new lead was stored."
)
ALERTS_KEPT = (
    "Copies in the creator’s chat service or mailbox are not deleted "
    "automatically by this site. The creator deletes them there."
)


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
                # The Worker purges raw click rows at one fixed window (a test
                # pins RAW_CLICK_KEEP_DAYS to it), never at a configured one.
                kept=CLICK_KEPT.replace("{RETENTION_DAYS}", str(RAW_CLICK_KEEP_DAYS)),
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
        what=VIEWS_WHAT.replace("{SITES}", sites).replace(
            "{LOCATION}", LOCATION_BY_LEVEL[analytics.geo_level]
        ),
        why=VIEWS_WHY,
        kept=VIEWS_KEPT.replace(
            "{CITY_KEPT}", CITY_KEPT if analytics.geo_level == "city" else ""
        )
        .replace("{RAW_KEEP_DAYS}", str(analytics.raw_keep_days))
        .replace(
            "{RAW_HARD_CAP_DAYS}",
            str(min(RAW_HARD_CAP_FACTOR * analytics.raw_keep_days, MAX_RAW_KEEP_DAYS)),
        )
        .replace("{AGGREGATE_KEEP_DAYS}", str(AGGREGATE_KEEP_DAYS)),
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


# --- the fixed notice (issues #777 and #780) ----------------------------------
#
# NOTICE_TEMPLATE (BLOCK A) and GATE_NOTICE_TEMPLATE (BLOCK B) are owner-approved
# copy, authored by Codex and used verbatim. Only the {placeholders} are filled,
# from the config or from the same constants the Worker tests pin. Do not reword
# them here; change the approved copy first. The notice cannot be turned off.
NOTICE_TEMPLATE = """\
## Who we are

{controller_name} is the controller for this site. For privacy requests, contact {contact}.

## What this site measures

This site measures page views and link clicks in aggregate. Measurement is designed so the site cannot tell who you are.

- No cookies are set for measurement.
- Your IP address is not stored.
- A daily hash helps avoid counting one visit twice and limit abuse. It rotates and exists only for that UTC day.
- If your browser sends Global Privacy Control or Do Not Track, the site does not record the view or click.

Raw records store location at most to the level of {geo_level}. If city-level totals are enabled, they appear only in daily aggregate counters and are hidden below a minimum count.

## Media-kit unlock cookie

When you unlock the media kit, the gate sets one cookie. It is HttpOnly, secure, and limited to this host. It lasts 24 hours. It is signed so the site can recognize that you unlocked the kit. It contains no analytics.

## How long records are kept

- Raw view records are kept for {raw_view_days} days by default. They may remain until they are rolled into daily totals, up to {raw_view_max_days} days.
- Click records are kept for {click_days} days.
- Daily totals are kept for {daily_totals_days} days.
- Monthly totals are kept for {monthly_totals_months} months.

## Why information is processed

In GDPR terms, the legal basis for measurement is legitimate interest. The site owner has a legitimate interest in understanding which pages and links are used, keeping the site working, and limiting abuse. Consent is not used for measurement because the measurements do not identify you.

For the media-kit gate, the legal basis is the site owner’s legitimate interest in sending the kit you requested and replying to you.

## Who receives information

Cloudflare provides hosting, database services, rate limiting, and the bot check. The site owner’s chosen alert services, {alert_providers}, receive notices when someone unlocks the media kit or sends an inquiry.

No one else receives visitor data. The site owner does not sell or share visitor data, including the lead list.

Cloudflare is a US company. Cloudflare states that it relies on the EU-US Data Privacy Framework and standard contractual clauses as a fallback. {dpa_note}

## Bot checks

The beacon bot check protects page-view and click measurement. It never runs for visitors whose browsers send Global Privacy Control or Do Not Track.

The media-kit gate form uses Cloudflare Turnstile when you use the form. This user-requested step needs a bot check. Turnstile receives your IP address, browser fingerprint signals, and user agent to distinguish people from bots.

The site may also use a small proof-of-work puzzle. It runs in your browser and needs nothing from you. Neither type of bot check is used to track you. {turnstile_storage_note}

## Your rights

You may ask what identifying information is held about you, request correction or deletion, object to processing, or ask for a copy. Where applicable, you may also complain to your data protection authority.

Measurement records cannot be matched to a person. Access and deletion requests can therefore be fulfilled only for information that identifies you, such as a media-kit lead. On a deletion request your contact details are deleted; short-lived anonymous operational records expire on their own. Privacy requests receive a reply within one month.

## Children

The media-kit gate is intended for business contacts. If you are a minor and entered an address, contact {contact} and ask for it to be deleted.

## About this notice

This is not legal advice. It describes what the software does by default. The site owner’s settings may narrow what is collected or enabled.
"""  # noqa: E501 (approved copy, one line per paragraph)

GATE_NOTICE_TEMPLATE = (
    "Your address is used only to deliver the media kit and reply to you. It is "
    "stored for {lead_days} days, then deleted. It is not verified when entered, "
    "shared, sold, or used for marketing. You may request deletion at any time at "
    "{contact}. This form uses a bot check and sets a secure 24-hour unlock "
    "cookie with no analytics. Privacy details: {privacy_link}."
)

# The one sentence of the notice that is true only when alerts are on. With
# alerts off the sentence is dropped, not filled with a made-up value.
_ALERT_SENTENCE = (
    " The site owner’s chosen alert services, {alert_providers}, receive notices "
    "when someone unlocks the media kit or sends an inquiry."
)
# With neither the gate nor alerts on, a site may leave the controller and the
# contact unset; the notice then points at the site owner.
_OWNER_FALLBACK = "the site owner"
_ALERT_NAMES = {"discord": "Discord", "matrix": "Matrix", "email": "email"}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


class NoticeError(ValueError):
    """The notice cannot be built. The message names fields, never values."""


def _fill(template: str, values: Mapping[str, str]) -> str:
    """Replace every {name} in one pass; refuse a placeholder with no value.

    One pass, so a value that contains a brace is never read as a placeholder.
    """
    unknown = sorted(set(_PLACEHOLDER.findall(template)) - values.keys())
    if unknown:
        raise NoticeError("the privacy notice has no value for: " + ", ".join(unknown))
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def _alert_providers(config: SiteConfig) -> str:
    names = [_ALERT_NAMES[c] for c in config.alerts.channels]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _geo_level(config: SiteConfig) -> str:
    # analytics.geo_level does not exist on every branch; read it when present.
    level = getattr(config.analytics, "geo_level", None)
    return level if isinstance(level, str) and level else DEFAULT_GEO_LEVEL


def _notice_values(config: SiteConfig) -> dict[str, str]:
    missing = missing_notice_fields(
        config.privacy,
        kit_gate=config.features.kit_gate,
        alerts_enabled=config.alerts.enabled,
    )
    if missing:
        raise NoticeError("the privacy notice needs " + " and ".join(missing))
    raw = config.analytics.raw_keep_days
    return {
        "controller_name": config.privacy.controller_name or _OWNER_FALLBACK,
        "contact": config.privacy.contact or _OWNER_FALLBACK,
        "geo_level": _geo_level(config),
        "raw_view_days": str(raw),
        "raw_view_max_days": str(min(RAW_HARD_CAP_FACTOR * raw, MAX_RAW_KEEP_DAYS)),
        "click_days": str(RAW_CLICK_KEEP_DAYS),
        "daily_totals_days": str(AGGREGATE_KEEP_DAYS),
        "monthly_totals_months": str(MONTHLY_KEEP_MONTHS),
        "alert_providers": _alert_providers(config),
        "turnstile_storage_note": config.privacy.turnstile_storage_note,
        "dpa_note": config.privacy.dpa_note,
        "lead_days": str(config.privacy.lead_retention_days),
    }


def notice_markdown(config: SiteConfig) -> str:
    """BLOCK A with every placeholder filled, as Markdown."""
    template = NOTICE_TEMPLATE
    if not config.alerts.enabled:
        template = template.replace(_ALERT_SENTENCE, "")
    return _fill(template, _notice_values(config))


@dataclass(frozen=True)
class NoticeBlock:
    """One part of the notice: a heading, a paragraph or a bullet list."""

    kind: Literal["h", "p", "ul"]
    text: str = ""
    items: tuple[str, ...] = ()


def notice_blocks(config: SiteConfig) -> list[NoticeBlock]:
    """The notice split for the template, which escapes every string."""
    blocks: list[NoticeBlock] = []
    for chunk in notice_markdown(config).strip().split("\n\n"):
        lines = chunk.split("\n")
        if chunk.startswith("## "):
            blocks.append(NoticeBlock("h", chunk[3:].rstrip()))
        elif all(line.startswith("- ") for line in lines):
            blocks.append(NoticeBlock("ul", items=tuple(x[2:].rstrip() for x in lines)))
        else:
            blocks.append(NoticeBlock("p", chunk.rstrip()))
    return blocks


def gate_notice_parts(config: SiteConfig) -> tuple[str, str] | None:
    """BLOCK B split around the privacy link, or None when the gate is off.

    The template renders the link between the two parts, so the link is a real
    anchor to the generated privacy page.
    """
    if not config.features.kit_gate:
        return None
    values = _notice_values(config)
    before, after = GATE_NOTICE_TEMPLATE.split("{privacy_link}")
    return _fill(before, values), _fill(after, values)
