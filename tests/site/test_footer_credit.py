# SPDX-License-Identifier: Apache-2.0
"""The footer credit: on by default, opt-out, custom text, no tracking."""

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
REPO = "https://github.com/ChiefGyk3D/Reach-Around"
PAGES = ("index.html", "more/index.html", "privacy/index.html")


def config(footer: dict[str, Any] | None = None, **extra: Any) -> SiteConfig:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "start",
                "title": "Start",
                "blocks": [{"id": "a", "title": "A", "url": "https://e.com/"}],
            },
            {
                "id": "more",
                "title": "More",
                "blocks": [{"id": "b", "title": "B", "url": "https://e.com/b"}],
            },
        ],
        **extra,
    }
    if footer is not None:
        data["footer"] = footer
    return SiteConfig.model_validate(data)


def pages(cfg: SiteConfig, out: Path) -> dict[str, str]:
    build_site(cfg, out, today=TODAY, assets_dir=ASSETS)
    return {p: (out / p).read_text(encoding="utf-8") for p in PAGES}


def credit_tags(html: str) -> list[str]:
    return re.findall(r'<a [^>]*href="' + re.escape(REPO) + r'"[^>]*>', html)


def test_credit_shown_by_default_on_every_page(tmp_path: Path) -> None:
    built = pages(config(), tmp_path)
    for name, html in built.items():
        assert len(credit_tags(html)) == 1, name
        assert ">Made with Reach Around</a>" in html, name
        assert html.index("<footer>") < html.index(REPO) < html.index("</footer>")


def test_credit_link_is_plain_and_untracked(tmp_path: Path) -> None:
    for name, html in pages(config(), tmp_path).items():
        (tag,) = credit_tags(html)
        assert 'rel="noopener noreferrer"' in tag, name
        for banned in ("ping", "data-link-id", "onclick", "target", "data-"):
            assert banned not in tag, (name, banned)
        footer = html[html.index("<footer>") : html.index("</footer>")]
        assert "<script" not in footer


def test_credit_has_no_tracking_even_with_click_tracking_on(tmp_path: Path) -> None:
    cfg = config(features={"click_tracking": True})
    html = pages(cfg, tmp_path)["index.html"]
    assert "ping=" in html  # ordinary links are tracked...
    (tag,) = credit_tags(html)
    assert "ping" not in tag and "data-link-id" not in tag  # ...the credit is not


def test_opt_out_renders_nothing(tmp_path: Path) -> None:
    for name, html in pages(config({"credit": False}), tmp_path).items():
        assert REPO not in html, name
        assert "Made with Reach Around" not in html, name
        assert "<footer>" in html  # the rest of the footer is untouched
        assert "Privacy</a>" in html


def test_custom_text_is_used(tmp_path: Path) -> None:
    built = pages(config({"credit_text": "Built on Reach Around"}), tmp_path)
    for name, html in built.items():
        assert ">Built on Reach Around</a>" in html, name
        assert "Made with Reach Around" not in html, name


def test_custom_text_is_escaped(tmp_path: Path) -> None:
    built = pages(config({"credit_text": "A <b>&\"'"}), tmp_path)
    for html in built.values():
        assert "<b>" not in html
        assert "A &lt;b&gt;&amp;&#34;&#39;</a>" in html


def test_credit_text_ignored_when_credit_off(tmp_path: Path) -> None:
    built = pages(config({"credit": False, "credit_text": "Custom"}), tmp_path)
    assert all("Custom" not in html for html in built.values())


def test_credit_makes_no_third_party_request(tmp_path: Path) -> None:
    html = pages(config(), tmp_path)["index.html"]
    assert not re.search(r"<(img|link|script)[^>]*github\.com", html)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x" * 61,
        "a\nb",
        "a\tb",
        "a\x00b",
        "a​b",  # zero-width space
        "a‍b",  # zero-width joiner
        "a﻿b",  # BOM / zero-width no-break
        "a‮b",  # right-to-left override
        "a⁦b",  # isolate
        "a b",  # line separator
        " lead",
        "trail ",
        "   ",
        5,
    ],
)
def test_hostile_credit_text_rejected(text: Any) -> None:
    with pytest.raises(ValidationError):
        config({"credit_text": text})


def test_credit_text_limits_accepted() -> None:
    assert config({"credit_text": "x" * 60}).footer.credit_text == "x" * 60
    assert config({"credit_text": "Hecho con Reach Around"}).footer.credit is True


def test_footer_section_refuses_unknown_keys_and_non_bools() -> None:
    with pytest.raises(ValidationError):
        config({"link": "https://evil.example/"})
    with pytest.raises(ValidationError):
        config({"credit": "yes"})


def test_default_footer_config() -> None:
    cfg = config()
    assert cfg.footer.credit is True
    assert cfg.footer.credit_text is None


def test_commented_example_keys_validate_when_uncommented() -> None:
    import yaml

    lines = (ASSETS / "site.yaml").read_text().splitlines()
    start = lines.index("# footer:")
    block = []
    for line in lines[start:]:
        if not line.startswith("#"):
            break
        block.append(line[2:])
    assert len(block) == 3
    raw = yaml.safe_load("\n".join(block))
    assert config(raw["footer"]).footer.credit is False
