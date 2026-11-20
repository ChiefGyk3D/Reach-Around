# SPDX-License-Identifier: Apache-2.0
"""Text rules and limits for the property bar ``site_nav`` (#671).

The models live in ``reach_around.config.models`` next to ``check_https_url``,
which the item URL needs (like the profile card button).
"""

from __future__ import annotations

import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field, StrictStr

MAX_ITEMS = 6
MAX_TITLE = 24


def _check_title(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty or only whitespace")
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    for ch in value:
        category = unicodedata.category(ch)
        # C* covers control, format (bidi marks, zero-width) and unassigned.
        if category[0] == "C" or category in ("Zl", "Zp"):
            raise ValueError(
                "must be plain text: no control, bidirectional or invisible characters"
            )
    return value


NavTitle = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_TITLE),
    AfterValidator(_check_title),
]
