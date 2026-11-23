# SPDX-License-Identifier: Apache-2.0
"""The support page (#683): one page that carries the crypto addresses.

Every address here is synthetic.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "examples" / "instance"
ADDR = "bc1qexampleexampleexampleexampleexample"


def crypto_block(ident: str = "pay") -> dict[str, Any]:
    return {"id": ident, "coins": [{"coin": "btc", "address": ADDR}]}


def data(
    support: dict[str, Any] | None, tabs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": tabs
        or [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
            }
        ],
    }
    if support is not None:
        out["support"] = support
    return out


SUPPORT: dict[str, Any] = {
    "title": "Support the show",
    "intro": "Every bit helps.",
    "blocks": [crypto_block()],
}


def build(raw: dict[str, Any], out: Path, assets: Path = ASSETS) -> dict[str, str]:
    build_site(SiteConfig.model_validate(raw), out, today=TODAY, assets_dir=assets)
    return {
        p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
        for p in out.rglob("*.html")
    }


def test_no_support_config_writes_no_support_page(tmp_path: Path) -> None:
    assert "support/index.html" not in build(data(None), tmp_path)


def test_support_page_is_written_with_title_intro_and_address(tmp_path: Path) -> None:
    html = build(data(SUPPORT), tmp_path)["support/index.html"]
    assert "<title>Support the show" in html
    assert "<h2>Support the show</h2>" in html
    assert "Every bit helps." in html
    assert ADDR in html
    assert 'data-copy-target="pay-addr-1"' in html
    assert re.search(r'<script src="\.\./static/copy\.[0-9a-f]{20}\.js"', html)
    assert re.search(r'href="\.\./static/crypto\.[0-9a-f]{20}\.css"', html)


def test_address_appears_on_the_support_page_only(tmp_path: Path) -> None:
    pages = build(data(SUPPORT), tmp_path)
    assert [name for name, html in pages.items() if ADDR in html] == [
        "support/index.html"
    ]


def test_crypto_in_a_tab_is_refused_when_support_is_set() -> None:
    tabs = [{"id": "start", "title": "Start", "blocks": [crypto_block("tabpay")]}]
    with pytest.raises(ValidationError, match="crypto addresses live on the support"):
        SiteConfig.model_validate(data(SUPPORT, tabs))


def test_only_one_crypto_block_on_the_support_page() -> None:
    support = {**SUPPORT, "blocks": [crypto_block("one"), crypto_block("two")]}
    with pytest.raises(ValidationError, match="at most one crypto block"):
        SiteConfig.model_validate(data(support))


def test_links_are_not_support_blocks() -> None:
    support = {
        **SUPPORT,
        "blocks": [{"id": "l", "title": "L", "url": "https://e.com/l"}],
    }
    with pytest.raises(ValidationError, match="support blocks"):
        SiteConfig.model_validate(data(support))


def test_block_ids_stay_unique_across_the_site() -> None:
    support = {**SUPPORT, "blocks": [crypto_block("a")]}
    with pytest.raises(ValidationError, match="duplicate id"):
        SiteConfig.model_validate(data(support))


def test_a_tab_cannot_take_the_support_path(tmp_path: Path) -> None:
    tabs = [
        {
            "id": "start",
            "title": "Start",
            "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
        },
        {
            "id": "support",
            "title": "Support",
            "blocks": [{"id": "b", "title": "B", "url": "https://e.com/b"}],
        },
    ]
    with pytest.raises(ValueError, match="support"):
        build(data(SUPPORT, tabs), tmp_path)


def test_support_coin_qr_is_refused() -> None:
    coin = {"coin": "btc", "address": ADDR, "qr": "media/qr.png"}
    support = {**SUPPORT, "blocks": [{"id": "pay", "coins": [coin]}]}
    with pytest.raises(ValidationError, match="no QR image"):
        SiteConfig.model_validate(data(support))


def test_image_blocks_are_not_support_blocks() -> None:
    block = {"id": "pic", "src": "media/qr.png", "alt": "A picture"}
    with pytest.raises(ValidationError, match="support blocks"):
        SiteConfig.model_validate(data({**SUPPORT, "blocks": [block]}))


def test_the_page_renders_intro_then_crypto_then_other_blocks(tmp_path: Path) -> None:
    text = {"id": "words", "markdown": "Plain words after."}
    support = {**SUPPORT, "blocks": [text, crypto_block()]}
    html = build(data(support), tmp_path)["support/index.html"]
    assert (
        html.index("Every bit helps.")
        < html.index('class="block block-crypto"')
        < html.index("Plain words after.")
    )


def test_the_address_is_an_isolated_ltr_element(tmp_path: Path) -> None:
    html = build(data(SUPPORT), tmp_path)["support/index.html"]
    assert f'dir="ltr">{ADDR}</code>' in html


BIDI = chr(0x202E)
ZERO = chr(0x200B)
NONCHAR = chr(0xFFFE)
LINESEP = chr(0x2028)
HEX40 = "0x" + "a1" * 20
HEX_SHORT = "0xa1a1a1a1a1a1"
LONG = "A" * 26


def _with(block: dict[str, Any]) -> dict[str, Any]:
    return {**SUPPORT, "blocks": [crypto_block("pay"), block]}


def _field_cases() -> list[tuple[str, Any]]:
    """Each displayed field, as a function from a bad value to a config."""

    def crypto_field(name: str) -> Any:
        return lambda v: {**SUPPORT, "blocks": [{**crypto_block(), name: v}]}

    def coin_field(name: str) -> Any:
        def make(v: str) -> dict[str, Any]:
            coin = {"coin": "btc", "address": ADDR, name: v}
            if name == "name":
                coin["coin"] = "other"
            return {**SUPPORT, "blocks": [{"id": "pay", "coins": [coin]}]}

        return make

    return [
        ("title", lambda v: {**SUPPORT, "title": v}),
        ("intro", lambda v: {**SUPPORT, "intro": v}),
        ("text", lambda v: _with({"id": "t", "markdown": v})),
        ("email title", lambda v: _with({"id": "e", "address": "a@e.com", "title": v})),
        ("email note", lambda v: _with({"id": "e", "address": "a@e.com", "note": v})),
        ("crypto title", crypto_field("title")),
        ("crypto note", crypto_field("note")),
        ("coin label", coin_field("label")),
        ("coin name", coin_field("name")),
        ("coin network", coin_field("network")),
    ]


@pytest.mark.parametrize("bad", [BIDI, ZERO, NONCHAR, LINESEP])
@pytest.mark.parametrize(
    ("field", "make"), _field_cases(), ids=[n for n, _ in _field_cases()]
)
def test_every_displayed_field_is_plain_text(field: str, make: Any, bad: str) -> None:
    with pytest.raises(ValidationError, match="plain text|plain"):
        SiteConfig.model_validate(data(make("a" + bad + "b")))


@pytest.mark.parametrize(
    "bad", [HEX_SHORT, LONG, "bc1qabcdefgh", "lnbc1abcdef", "send " + LONG]
)
@pytest.mark.parametrize(
    ("field", "make"), _field_cases(), ids=[n for n, _ in _field_cases()]
)
def test_no_displayed_field_holds_an_address_like_token(
    field: str, make: Any, bad: str
) -> None:
    with pytest.raises(ValidationError, match="address-like"):
        SiteConfig.model_validate(data(make(bad)))


def test_the_coin_address_itself_is_still_allowed() -> None:
    SiteConfig.model_validate(data(SUPPORT))


def test_text_blocks_allow_paragraph_breaks_but_no_other_control() -> None:
    SiteConfig.model_validate(data(_with({"id": "t", "markdown": "One.\n\nTwo."})))
    with pytest.raises(ValidationError, match="plain text"):
        SiteConfig.model_validate(data(_with({"id": "t", "markdown": "a\rb"})))


@pytest.mark.parametrize(
    "md",
    [
        "[x](https://e.com/)",
        "![x](media/a.png)",
        "<b>x</b>",
        "see https://e.com/x",
        "bitcoin:abc",
        "mailto:a@e.com",
        "www.e.com",
    ],
)
def test_text_blocks_take_no_links_images_html_or_schemes(md: str) -> None:
    with pytest.raises(ValidationError, match="no links"):
        SiteConfig.model_validate(data(_with({"id": "t", "markdown": md})))


def test_a_full_hex_address_in_text_is_refused() -> None:
    with pytest.raises(ValidationError, match="address-like"):
        SiteConfig.model_validate(data(_with({"id": "t", "markdown": HEX40})))
