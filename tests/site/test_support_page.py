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
CRYPTO_ASSETS = ROOT / "examples" / "crypto"
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


def test_support_qr_image_is_collected(tmp_path: Path) -> None:
    coin = {"coin": "btc", "address": ADDR, "qr": "media/qr-example.png"}
    support = {**SUPPORT, "blocks": [{"id": "pay", "coins": [coin]}]}
    html = build(data(support), tmp_path, CRYPTO_ASSETS)["support/index.html"]
    assert re.search(
        r'<img class="crypto-qr-image" src="\.\./media/qr-example\.[0-9a-f]{20}\.png"',
        html,
    )


@pytest.mark.parametrize(
    "src",
    [
        "https://e.com/qr.png",
        "http://e.com/qr.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "//e.com/qr.png",
        "../qr.png",
        "media/../../qr.png",
        "/etc/qr.png",
    ],
)
def test_support_image_must_be_a_local_path(src: str) -> None:
    block = {"id": "pic", "src": src, "alt": "A picture"}
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data({**SUPPORT, "blocks": [block]}))


def test_support_image_takes_no_link_but_a_local_image_is_fine() -> None:
    block = {"id": "pic", "src": "media/qr.png", "alt": "A picture"}
    SiteConfig.model_validate(data({**SUPPORT, "blocks": [block]}))
    with pytest.raises(ValidationError, match="takes no link"):
        SiteConfig.model_validate(
            data({**SUPPORT, "blocks": [{**block, "link": "https://e.com/"}]})
        )


@pytest.mark.parametrize(
    "intro",
    [
        "a" + chr(0x202E) + "b\nc",
        "a" + chr(0x202E) + "b",
        "zero" + chr(0x200B) + "w",
        "two\nlines",
    ],
)
def test_support_intro_must_be_plain_text(intro: str) -> None:
    with pytest.raises(ValidationError, match="plain text"):
        SiteConfig.model_validate(data({**SUPPORT, "intro": intro}))
