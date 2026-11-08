# SPDX-License-Identifier: Apache-2.0
"""Search snippet and first-paint hints: meta description and font preloads (#358)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
PAGES = ("index.html", "more/index.html", "privacy/index.html", "404.html")
HOSTILE = '"><script>alert(1)</script>'


def config(
    creator: dict[str, Any] | None = None, theme: dict[str, Any] | None = None
) -> SiteConfig:
    def tab(ident: str, title: str) -> dict[str, Any]:
        return {
            "id": ident,
            "title": title,
            "blocks": [{"id": f"l-{ident}", "title": "L", "url": "https://e.com/"}],
        }

    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator", **(creator or {})},
        "tabs": [tab("start", "Start"), tab("more", "More")],
    }
    if theme is not None:
        data["theme"] = theme
    return SiteConfig.model_validate(data)


def build(cfg: SiteConfig, out: Path) -> dict[str, str]:
    build_site(cfg, out, today=TODAY, assets_dir=EXAMPLE.parent)
    return {
        name: (out / name).read_text(encoding="utf-8")
        for name in PAGES
        if (out / name).is_file()
    }


def descriptions(html: str) -> list[str]:
    return re.findall(r'<meta name="description" content="([^"]*)">', html)


def test_every_page_has_one_default_description(tmp_path: Path) -> None:
    pages = build(config(), tmp_path)
    assert len(pages) == len(PAGES)
    for name, html in pages.items():
        found = descriptions(html)
        assert len(found) == 1, name
        assert "Example Creator" in found[0], name


def test_description_comes_from_the_config_and_is_escaped(tmp_path: Path) -> None:
    pages = build(config({"description": "Streams, gear and notes."}), tmp_path)
    for name, html in pages.items():
        assert descriptions(html) == ["Streams, gear and notes."], name
    hostile = build(config({"description": HOSTILE}), tmp_path / "h")
    for name, html in hostile.items():
        assert "<script>alert" not in html, name
        assert len(descriptions(html)) == 1, name


@pytest.mark.parametrize("bad", ["", " pad ", "two\nlines", "x" * 161, 7])
def test_description_is_plain_bounded_text(bad: object) -> None:
    with pytest.raises(ValidationError):
        config({"description": bad})


def test_description_at_the_limit_is_accepted() -> None:
    assert config({"description": "x" * 160}).creator.description == "x" * 160


def preloads(html: str) -> list[dict[str, str]]:
    tags = re.findall(r'<link rel="preload"[^>]*>', html)
    return [dict(re.findall(r'(\w+)="([^"]*)"', tag)) for tag in tags]


def test_default_body_font_is_preloaded_from_a_real_file(tmp_path: Path) -> None:
    pages = build(config(), tmp_path)
    for name, html in pages.items():
        found = preloads(html)
        assert len(found) == 1, name
        tag = found[0]
        assert tag["as"] == "font" and tag["type"] == "font/woff2", name
        assert "crossorigin" in html.split(tag["href"])[1].split(">")[0], name
        href = tag["href"]
        target = tmp_path / href.lstrip("/") if href.startswith("/") else None
        if target is None:
            target = ((tmp_path / name).parent / href).resolve()
        assert target.is_file(), name
        assert "jost-latin-wght-normal." in tag["href"], name


def test_preload_prefix_matches_page_depth(tmp_path: Path) -> None:
    pages = build(config(), tmp_path)
    assert preloads(pages["index.html"])[0]["href"].startswith("static/fonts/")
    assert preloads(pages["more/index.html"])[0]["href"].startswith("../static/")
    assert preloads(pages["404.html"])[0]["href"].startswith("/static/")


def test_theme_font_files_are_preloaded_instead_of_the_default(tmp_path: Path) -> None:
    cfg = load_site_config(EXAMPLE)
    assert cfg.theme.fonts.heading is not None
    pages = build(cfg, tmp_path)
    found = preloads(pages["index.html"])
    hrefs = [tag["href"] for tag in found]
    assert any("orbitron-wght." in h for h in hrefs)
    assert any("jost-latin-wght-normal." in h for h in hrefs)
    ttf = [t for t in found if "orbitron-wght." in t["href"]][0]
    assert ttf["type"] == "font/ttf"


def test_a_system_body_stack_preloads_no_default_font(tmp_path: Path) -> None:
    pages = build(config(theme={"fonts": {"body": "Georgia, serif"}}), tmp_path)
    assert all(preloads(html) == [] for html in pages.values())


def test_guard_bites_on_a_page_without_the_hints() -> None:
    assert descriptions("<html></html>") == []
    assert preloads("<html></html>") == []
