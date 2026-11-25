# SPDX-License-Identifier: Apache-2.0
"""The property bar ``site_nav`` (#671) on the built media kit page (#685).

The kit page is rendered through the repo's build, not the nav partial alone.
The bar must be on the public kit and the gated kit, mark the current item, keep
its tap-size rule in the stylesheet the kit links, stay off when the setting is
absent or switched off, and never reach either PDF.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from test_kit_build import build as build_kit
from test_kit_build import make_config as make_site_config

from kitpdfharness import build_instance, fake_renderer, make_config
from reach_around.config import SiteConfig
from reach_around.kit.pdf import build_kit_pdf, build_public_kit_pdf

ITEMS: list[dict[str, Any]] = [
    {"title": "Links", "url": "https://links.example.com/"},
    {"title": "Media kit", "url": "https://links.example.com/kit/", "current": True},
    {"title": "Main site", "url": "https://www.example.com/"},
]
TITLES = ("Links", "Media kit", "Main site")


def with_nav(config: SiteConfig, site_nav: dict[str, Any] | None) -> SiteConfig:
    data = config.model_dump(mode="json", exclude_none=True)
    data.pop("site_nav", None)
    if site_nav is not None:
        data["site_nav"] = site_nav
    return SiteConfig.model_validate(data)


def bars(html: str) -> list[str]:
    return re.findall(r'<nav class="site-nav[^"]*"[^>]*>.*?</nav>', html, re.S)


def kinds(html: str) -> list[str]:
    return [m for b in bars(html) for m in re.findall(r"site-nav-(bar|foot)", b)]


def kit_pages(tmp_path: Path, site_nav: dict[str, Any] | None) -> dict[str, str]:
    config = with_nav(make_site_config(), site_nav)
    out, gated = build_kit(tmp_path, config)
    return {
        "public": (out / "kit" / "index.html").read_text(encoding="utf-8"),
        "gated": next(gated.rglob("*.html")).read_text(encoding="utf-8"),
    }


def test_the_bar_is_on_the_public_kit_page(tmp_path: Path) -> None:
    html = kit_pages(tmp_path, {"items": ITEMS})["public"]
    header_bar, footer_line = bars(html)
    assert html.index(header_bar) < html.index('id="kit-title"')
    assert html.index("<footer") < html.index(footer_line)
    for bar in (header_bar, footer_line):
        assert re.findall(r'<a [^>]*href="([^"]+)"', bar) == [i["url"] for i in ITEMS]


def test_the_bar_is_on_the_gated_kit_page(tmp_path: Path) -> None:
    html = kit_pages(tmp_path, {"items": ITEMS})["gated"]
    assert len(bars(html)) == 2


def test_aria_current_marks_only_the_current_item_on_the_kit(tmp_path: Path) -> None:
    for html in kit_pages(tmp_path, {"items": ITEMS}).values():
        for bar in bars(html):
            assert bar.count("aria-current") == 1
            assert re.search(r'<a [^>]*aria-current="page"[^>]*>Media kit</a>', bar)


def test_the_kit_links_the_stylesheet_with_the_tap_size_rule(tmp_path: Path) -> None:
    config = with_nav(make_site_config(), {"items": ITEMS})
    out, _ = build_kit(tmp_path, config)
    html = (out / "kit" / "index.html").read_text(encoding="utf-8")
    hrefs = re.findall(r'<link [^>]*href="([^"]*static/site[^"]*\.css)"', html)
    assert len(hrefs) == 1
    css = out / "static" / hrefs[0].rsplit("/", 1)[1]
    text = css.read_text(encoding="utf-8")
    block = text[text.index("/* Property bar") : text.index("/* end property bar */")]
    match = re.search(r"\.site-nav a\s*\{([^}]*)\}", block)
    assert match and "min-height: var(--ra-tap)" in match.group(1)
    assert "min-width: var(--ra-tap)" in match.group(1)


def test_no_site_nav_leaves_the_kit_unchanged(tmp_path: Path) -> None:
    for html in kit_pages(tmp_path, None).values():
        assert "site-nav" not in html


def test_header_off_and_footer_off_are_each_honored_on_the_kit(tmp_path: Path) -> None:
    header_only = kit_pages(tmp_path / "a", {"items": ITEMS, "footer": False})
    footer_only = kit_pages(tmp_path / "b", {"items": ITEMS, "header": False})
    for html in header_only.values():
        assert kinds(html) == ["bar"]
    for html in footer_only.values():
        assert kinds(html) == ["foot"]


def pdf_pages(tmp_path: Path, site_nav: dict[str, Any] | None) -> list[str]:
    seen: list[str] = []

    def renderer(html: str, work: Path) -> bytes:
        seen.append(html)
        return fake_renderer(html, work)

    config, site, gated = build_instance(tmp_path, with_nav(make_config(), site_nav))
    build_public_kit_pdf(
        config,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="public.pdf",
        created=date(2027, 3, 10),
        renderer=renderer,
    )
    build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="full.pdf",
        created=date(2027, 3, 10),
        renderer=renderer,
    )
    return seen


def test_the_site_pages_carry_the_bar_but_neither_pdf_does(tmp_path: Path) -> None:
    _, site, _ = build_instance(
        tmp_path / "src", with_nav(make_config(), {"items": ITEMS})
    )
    assert "site-nav" in (site / "kit" / "index.html").read_text(encoding="utf-8")
    pages = pdf_pages(tmp_path / "run", {"items": ITEMS})
    assert len(pages) == 2, "both the public and the full PDF must render"
    for html in pages:
        assert bars(html) == []
        assert "More from this creator" not in html
        for url in (i["url"] for i in ITEMS):
            assert url not in html
