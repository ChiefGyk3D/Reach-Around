# SPDX-License-Identifier: Apache-2.0
"""The ``analytics`` section: page-view counting, off unless asked for."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

DEFAULT_ROUTE = "/e/v/"
MAX_ROUTE_LENGTH = 64
# Plain segments only, each starting with a letter, number, _, ~ or -: no scheme,
# host, query, fragment, dot segments, empty segments or escapes. Matched with
# ``fullmatch`` so a trailing newline cannot slip through.
_ROUTE = re.compile(r"(?:/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)+/")


class Analytics(BaseModel):
    """Count page views through the Worker route. Off by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_views: StrictBool = False
    route: str = DEFAULT_ROUTE

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
