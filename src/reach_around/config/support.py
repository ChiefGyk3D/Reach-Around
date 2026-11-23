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
# Payment handles that are not an address format: a domain-like token (an inner
# dot then two or more letters, which also covers name.eth and the like), an
# "@" token and a $cashtag.
_DOMAIN_LIKE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*\.[A-Za-z]{2,}")
_HANDLE = re.compile(r"@|\$\w")
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


def check_no_address_shape(value: str) -> None:
    """Refuse a long address-shaped token (the subset safe for fixed page text).

    This is the part of ``check_no_address`` that does not mind domains, names
    with accents or an ``@``: the fixed chrome of a page may show those.
    """
    text = unicodedata.normalize("NFKC", value)
    if _LONG_TOKEN.search(text) or _ADDRESS_PREFIX.search(text):
        raise ValueError("an address-like token")


def check_no_address(value: str) -> None:
    """Refuse a token that looks like a payment address or handle.

    The text is NFKC-normalized first (full-width and compatibility forms fold
    to ASCII), and any letter that is still not ASCII is refused, so a lookalike
    letter cannot slip past the ASCII patterns.
    """
    text = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(ch)[0] == "L" and ord(ch) > 127 for ch in text):
        raise ValueError(
            "support text must not contain an address-like token or a non-ASCII letter"
        )
    for pattern in (
        _LONG_TOKEN,
        _ADDRESS_PREFIX,
        _DOMAIN_LIKE,
        _HANDLE,
    ):
        if pattern.search(text):
            raise ValueError(
                "support text must not contain an address-like token or a "
                "non-ASCII letter; addresses go in a crypto coin only"
            )


def check_no_markup(value: str) -> None:
    """Refuse markdown links and images, raw HTML and URI schemes."""
    if _MARKUP.search(value):
        raise ValueError("support text takes no links, images, HTML or URI schemes")
