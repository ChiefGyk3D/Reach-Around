# SPDX-License-Identifier: Apache-2.0
"""The optional affiliate disclosure shown in the page footer.

Written either as a plain string (the text, shown on pages that carry an
affiliate link) or as a mapping with ``text``, ``show`` and ``always``. The
text is plain text: no control or hidden characters, no links. The template
escapes it.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
)

MAX_DISCLOSURE_TEXT = 500
DEFAULT_DISCLOSURE_TEXT = (
    "Some links on this page may be affiliate links. Purchases made through "
    "them may earn the creator a small commission at no extra cost to the "
    "buyer, without affecting their recommendations."
)

# A scheme, a www host or an address: the disclosure never carries a link.
_LINKISH = re.compile(r"://|\bwww\.|@|\bmailto:", re.IGNORECASE)


def _check_text(value: str) -> str:
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "C" or category in ("Zl", "Zp"):
            raise ValueError(
                "must be plain text: no control, bidi or zero-width characters"
            )
    if _LINKISH.search(value):
        raise ValueError("must not contain links or addresses")
    return value


DisclosureText = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_DISCLOSURE_TEXT),
    AfterValidator(_check_text),
]


class Disclosure(BaseModel):
    """The disclosure line.

    ``show`` switches it off without deleting the text. ``always`` shows it on
    pages with no affiliate link too.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: DisclosureText | None = None
    show: StrictBool = True
    always: StrictBool = False

    @property
    def line(self) -> str:
        return self.text or DEFAULT_DISCLOSURE_TEXT

    def visible(self, has_affiliate: bool) -> bool:
        return self.show and (has_affiliate or self.always)


def _from_string(value: Any) -> Any:
    return {"text": value} if isinstance(value, str) else value


DisclosureField = Annotated[Disclosure, BeforeValidator(_from_string)]
