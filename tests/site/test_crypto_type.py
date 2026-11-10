# SPDX-License-Identifier: Apache-2.0
"""Type sizes in the crypto section (issue 513): small address, note and summary.

Reads the shipped stylesheet and checks the font-size token of three rules.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CRYPTO_CSS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "reach_around"
    / "site"
    / "static"
    / "crypto.css"
)


def font_size(css: str, selector: str) -> str | None:
    """Return the font-size value of the first top-level rule for selector."""
    rule = re.search(
        r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css
    )
    if rule is None:
        return None
    size = re.search(r"font-size:\s*([^;]+);", rule.group(1))
    return size.group(1).strip() if size else None


@pytest.mark.parametrize(
    ("selector", "token"),
    [
        (".crypto-address", "var(--ra-text-xs)"),
        (".crypto-note", "var(--ra-text-xs)"),
        (".crypto-qr summary", "var(--ra-text-sm)"),
    ],
)
def test_crypto_text_is_small(selector: str, token: str) -> None:
    assert font_size(CRYPTO_CSS.read_text("utf-8"), selector) == token


def test_font_size_helper_bites() -> None:
    css = ".a { color: red; }\n.b { font-size: 1rem; }\n"
    assert font_size(css, ".a") is None
    assert font_size(css, ".b") == "1rem"
    assert font_size(css, ".missing") is None
