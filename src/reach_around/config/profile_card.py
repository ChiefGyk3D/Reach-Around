# SPDX-License-Identifier: Apache-2.0
"""Text rules for the optional profile card (tags and button label).

The card model itself lives in ``reach_around.config.models`` next to
``check_https_url``, which the button URL needs.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field, StrictStr

MAX_TAGS = 8
MAX_TAG = 30
MAX_LABEL = 40
MEDIA_KIT_ID = "profile-media-kit"


def _check_chip_text(value: str) -> str:
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


ProfileTag = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_TAG),
    AfterValidator(_check_chip_text),
]
ButtonLabel = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_LABEL),
    AfterValidator(_check_chip_text),
]
