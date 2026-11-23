# SPDX-License-Identifier: Apache-2.0
"""Text rules for the support page (#683).

The support page shows payment addresses, so every field it displays other
than a coin's ``address`` is held to a strict rule: plain text, no hidden or
bidirectional characters, and nothing that looks like an address. These are
mistake-catchers, not a guarantee: an address can be spelled to slip past a
pattern, so a person still reviews every change to the page's text.
"""

from __future__ import annotations

import re
import unicodedata

# Long runs of letters and digits cover base58, bech32 and hex addresses (a
# Bitcoin legacy address is 26 or more characters, a hex address 42).
_LONG_TOKEN = re.compile(r"[A-Za-z0-9]{26,}")
_ADDRESS_PREFIX = re.compile(
    r"(?<![A-Za-z0-9])(?:bc1|tb1|ltc1|lnbc|lntb|0x)[A-Za-z0-9]{6,}", re.I
)
# Links, images, raw HTML and URI schemes (``bitcoin:``, ``https://``, ``www.``).
_MARKUP = re.compile(
    r"\]\(|<|>|://|(?<![A-Za-z0-9])www\.|[A-Za-z][A-Za-z0-9+.-]{1,30}:\S", re.I
)


def check_plain(value: str, *, allow_newline: bool = False) -> None:
    """Refuse control, format (bidi, zero width), noncharacter and separator marks."""
    for ch in value:
        if ch == "\n" and allow_newline:
            continue
        category = unicodedata.category(ch)
        if category[0] == "C" or (category[0] == "Z" and ch != " "):
            raise ValueError(
                "support text must be plain text without control, hidden or "
                "bidirectional characters"
            )


def check_no_address(value: str) -> None:
    """Refuse a token that looks like a payment address."""
    if _LONG_TOKEN.search(value) or _ADDRESS_PREFIX.search(value):
        raise ValueError(
            "support text must not contain an address-like token; addresses "
            "go in a crypto coin only"
        )


def check_no_markup(value: str) -> None:
    """Refuse markdown links and images, raw HTML and URI schemes."""
    if _MARKUP.search(value):
        raise ValueError("support text takes no links, images, HTML or URI schemes")
