# SPDX-License-Identifier: Apache-2.0
"""The ``analytics`` section: page-view counting, off unless asked for."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, field_validator

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


class Analytics(BaseModel):
    """Count page views through the Worker route. Off by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_views: StrictBool = False
    route: str = DEFAULT_ROUTE
    # What the privacy page says, so it matches how the Worker is set up. The
    # build cannot read the Worker's settings: keep RAW_KEEP_DAYS in sync.
    raw_keep_days: StrictInt = DEFAULT_RAW_KEEP_DAYS
    # Other sites counted by the same collector (the Worker's SITES), named on
    # the privacy page. Host names only.
    other_sites: tuple[str, ...] = ()

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
