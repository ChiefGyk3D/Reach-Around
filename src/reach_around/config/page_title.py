# SPDX-License-Identifier: Apache-2.0
"""Page title text rules and the fixed three-placeholder template.

The substitution is a single regex pass over a closed set of tokens. It is
deliberately not ``str.format``: nothing but ``{tab}``, ``{creator}`` and
``{site}`` is ever expanded, and substituted values are not scanned again.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field, StrictStr

MAX_TITLE = 80
MAX_TEMPLATE = 200
DEFAULT_TEMPLATE = "{tab} - {creator}"
PLACEHOLDERS = ("tab", "creator", "site")

_TOKEN = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


def _check_plain(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty or only whitespace")
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    for ch in value:
        category = unicodedata.category(ch)
        if category[0] == "C" or category in ("Zl", "Zp"):
            raise ValueError("must be plain text: no control characters or newlines")
    return value


def _check_template(value: str) -> str:
    _check_plain(value)
    leftover = _TOKEN.sub("", value)
    if "{" in leftover or "}" in leftover:
        raise ValueError(
            "only the placeholders {tab}, {creator} and {site} are allowed; "
            "any other brace is refused"
        )
    return value


PageTitle = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_TITLE),
    AfterValidator(_check_plain),
]
TitleTemplate = Annotated[
    StrictStr,
    Field(min_length=1, max_length=MAX_TEMPLATE),
    AfterValidator(_check_template),
]


def render_template(template: str, *, tab: str, creator: str, site: str) -> str:
    """Expand the three placeholders in one pass; values are never re-scanned."""
    values = {"tab": tab, "creator": creator, "site": site}
    return _TOKEN.sub(lambda m: values[m.group(1)], template)
