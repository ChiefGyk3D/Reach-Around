# SPDX-License-Identifier: Apache-2.0
"""Footer settings: the optional "Made with Reach Around" credit."""

from __future__ import annotations

import unicodedata
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
)

MAX_CREDIT_TEXT = 60
DEFAULT_CREDIT_TEXT = "Made with Reach Around"
CREDIT_URL = "https://github.com/ChiefGyk3D/Reach-Around"


def _check_credit_text(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty or only whitespace")
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    for ch in value:
        # Any C* category covers control, format (bidi marks, zero-width
        # characters, BOM), surrogate, private-use and unassigned characters.
        category = unicodedata.category(ch)
        if category[0] == "C" or category in ("Zl", "Zp"):
            raise ValueError(
                "must be plain text: no control, bidi or zero-width characters"
            )
    return value


CreditText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_CREDIT_TEXT),
    AfterValidator(_check_credit_text),
]


class Footer(BaseModel):
    """What the footer of every page shows besides the privacy link."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    credit: StrictBool = True
    credit_text: CreditText | None = None

    @property
    def text(self) -> str:
        """The label the credit link shows."""
        return self.credit_text or DEFAULT_CREDIT_TEXT
