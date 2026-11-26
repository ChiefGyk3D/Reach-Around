# SPDX-License-Identifier: Apache-2.0
"""The ``analytics`` section: page-view counting, off unless asked for."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from reach_around.config.hoplabel import check_hop_label

DEFAULT_ROUTE = "/e/v/"
MAX_ROUTE_LENGTH = 64
# Plain segments only, each starting with a letter, number, _, ~ or -: no scheme,
# host, query, fragment, dot segments, empty segments or escapes. Matched with
# ``fullmatch`` so a trailing newline cannot slip through.
_ROUTE = re.compile(r"(?:/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)+/")

# Raw view records live this many days unless the Worker's RAW_KEEP_DAYS says
# otherwise; 90 is the Worker's hard ceiling for everything
# (worker/src/viewretention.ts).
DEFAULT_RAW_KEEP_DAYS = 7
MAX_RAW_KEEP_DAYS = 90
# Daily totals are kept this long. Fixed by the Worker (AGGREGATE_KEEP_DAYS);
# a test keeps the two numbers equal.
AGGREGATE_KEEP_DAYS = 90
MAX_OTHER_SITES = 7
# Same shape as the Worker's host check: lower case, two or more labels, the last
# one with a letter, no scheme, port, path, wildcard or address literal.
_HOST = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]"
)

# A Turnstile site key is public. Cloudflare's own keys are 22 to 30 characters of
# letters, digits, - and _; the published test keys are 24. Matched with
# ``fullmatch`` so a trailing newline cannot slip through.
_SITE_KEY = re.compile(r"[0-9A-Za-z_-]{16,64}")


class Analytics(BaseModel):
    """Count page views through the Worker route. Off by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_views: StrictBool = False
    # Ask the Worker for a short-lived signed token and send it with click pings
    # and view beacons. Off by default; use it only with the Worker's BEACON_MODE
    # set to observe.
    beacon_token: StrictBool = False
    route: str = DEFAULT_ROUTE
    # What the privacy page says, so it matches how the Worker is set up. The
    # build cannot read the Worker's settings: keep RAW_KEEP_DAYS in sync.
    raw_keep_days: StrictInt = DEFAULT_RAW_KEEP_DAYS
    # The Worker's GEO_LEVEL (or the site's own `geo`), for the privacy page. Raw view
    # records hold at most the region (#778); at "city" the city is counted only in a
    # daily total. The default matches the Worker's. Keep it in sync.
    geo_level: Literal["country", "region", "city"] = "region"
    # Other sites counted by the same collector (the Worker's SITES), named on
    # the privacy page. Host names only.
    other_sites: tuple[str, ...] = ()
    # This site's name in the funnel's hop table (#593): the build adds
    # ``h=<hop_label>`` to a link marked ``internal: true``. One closed label,
    # the same rule the click script and the Worker apply when they read it.
    hop_label: StrictStr | None = None
    # The invisible bot check (#610), off unless asked for. On, the build adds
    # one hidden same-origin page that loads Cloudflare Turnstile, and a small
    # script that opens it in a hidden frame for visitors who send neither
    # Global Privacy Control nor Do Not Track. Needs the public site key.
    turnstile_beacon: StrictBool = False
    turnstile_site_key: StrictStr | None = None

    @model_validator(mode="after")
    def _check_the_bot_check(self) -> Analytics:
        key = self.turnstile_site_key
        if self.turnstile_beacon:
            if key is None or _SITE_KEY.fullmatch(key) is None:
                raise ValueError(
                    "turnstile_site_key must be 16 to 64 letters, digits, - or _ "
                    "when turnstile_beacon is on"
                )
        elif key is not None:
            raise ValueError(
                "turnstile_site_key is set but turnstile_beacon is off: turn "
                "turnstile_beacon on or remove the key"
            )
        return self

    @field_validator("raw_keep_days")
    @classmethod
    def _keep_in_range(cls, value: int) -> int:
        if not 1 <= value <= MAX_RAW_KEEP_DAYS:
            raise ValueError(f"raw_keep_days must be from 1 to {MAX_RAW_KEEP_DAYS}")
        return value

    @field_validator("other_sites")
    @classmethod
    def _hosts_only(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_OTHER_SITES:
            raise ValueError(f"at most {MAX_OTHER_SITES} other sites")
        seen: set[str] = set()
        for host in value:
            if len(host) > 253 or _HOST.fullmatch(host) is None:
                raise ValueError(
                    "other_sites entries must be lower-case host names such as "
                    "blog.example.test (no scheme, port, path or wildcard)"
                )
            if host in seen:
                raise ValueError("other_sites entries must be unique")
            seen.add(host)
        return value

    @field_validator("hop_label")
    @classmethod
    def _closed_label(cls, value: str | None) -> str | None:
        return None if value is None else check_hop_label(value)

    @field_validator("route")
    @classmethod
    def _same_origin_path(cls, value: str) -> str:
        if len(value) > MAX_ROUTE_LENGTH:
            raise ValueError(f"route must be at most {MAX_ROUTE_LENGTH} characters")
        if _ROUTE.fullmatch(value) is None:
            raise ValueError(
                "route must be a same-origin path of plain segments that starts "
                "and ends with '/', such as /e/v/"
            )
        return value
