# SPDX-License-Identifier: Apache-2.0
"""Page layout variants render accessible, local, responsive review pages."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig
from reach_around.site.build import build_site, build_variants

TODAY = date(2027, 3, 10)


def raw(layout: str) -> dict[str, Any]:
    tabs: list[dict[str, Any]] = [
        {
            "id": "watch",
            "title": "Watch",
            "intent": "watch",
            "blocks": [
                {
                    "id": "watch-kick",
                    "title": "Watch on Kick",
                    "url": "https://example.com/kick",
                    "icon": "kick",
                },
                {
                    "id": "watch-twitch",
                    "title": "Watch on Twitch",
                    "url": "https://example.com/twitch",
                    "icon": "twitch",
                },
            ],
        }
    ]
    for intent, title in (
        ("videos", "Latest videos"),
        ("follow", "Follow"),
        ("community", "Community"),
        ("support", "Support"),
        ("work", "Work with me"),
        ("shop", "Shop"),
        ("blog", "Blog"),
    ):
        tabs.append(
            {
                "id": intent,
                "title": title,
                "intent": intent,
                "blocks": [
                    {
                        "id": f"{intent}-{number}",
                        "title": f"{title} link {number}",
                        "url": f"https://example.com/{intent}/{number}",
                        "icon": "link",
                    }
                    for number in (1, 2)
                ],
            }
        )
    return {
        "version": 1,
        "creator": {"name": "Example Creator", "header": "centered"},
        "features": {"click_tracking": False},
        "page": {
            "layout": layout,
            "status": {
                "text": "Live Friday at 8 PM Eastern",
                "schedule": "Next stream: November 13",
            },
            "watch": ["watch-kick", "watch-twitch"],
        },
        "tabs": tabs,
    }


def build(tmp_path: Path, layout: str) -> tuple[Path, str]:
    out = tmp_path / layout
    build_site(SiteConfig.model_validate(raw(layout)), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def static_text(out: Path, stem: str, suffix: str) -> str:
    path = next((out / "static").glob(f"{stem}.*.{suffix}"))
    return path.read_text(encoding="utf-8")


def test_classic_default_and_explicit_classic_build_byte_identically(
    tmp_path: Path,
) -> None:
    original = raw("classic")
    original.pop("page")
    for tab in original["tabs"]:
        tab.pop("intent")
    explicit = {**original, "page": {"layout": "classic"}}
    default_out = tmp_path / "default"
    explicit_out = tmp_path / "explicit"
    build_site(SiteConfig.model_validate(original), default_out, today=TODAY)
    build_site(SiteConfig.model_validate(explicit), explicit_out, today=TODAY)
    default_files = {
        path.relative_to(default_out): path.read_bytes()
        for path in default_out.rglob("*")
        if path.is_file()
    }
    explicit_files = {
        path.relative_to(explicit_out): path.read_bytes()
        for path in explicit_out.rglob("*")
        if path.is_file()
    }
    assert explicit_files == default_files
    assert not list((default_out / "static").glob("variants.*"))


@pytest.mark.parametrize("layout", ["stack", "chips", "bento", "tabs"])
def test_every_variant_is_one_grouped_page_with_status_and_equal_watch_order(
    tmp_path: Path, layout: str
) -> None:
    out, html = build(tmp_path, layout)
    assert f'class="variant variant-{layout}"' in html
    assert "Live Friday at 8 PM Eastern" in html
    assert "Next stream: November 13" in html
    assert html.index("Watch on Kick") < html.index("Watch on Twitch")
    assert len(re.findall(r'class="[^"]*\bvariant-watch-link\b', html)) == 2
    assert "data-live-badge-hook hidden" in html
    positions = [
        html.index(f'id="group-{intent}"')
        for intent in (
            "videos",
            "follow",
            "community",
            "support",
            "work",
            "shop",
            "blog",
        )
    ]
    assert positions == sorted(positions)
    assert "Latest videos" in html
    assert not (out / "videos" / "index.html").exists()
    assert list((out / "static").glob("variants.*.css"))


def test_chips_has_plain_in_page_links_in_group_order(tmp_path: Path) -> None:
    _out, html = build(tmp_path, "chips")
    nav = re.search(r'<nav class="variant-chip-nav".*?</nav>', html, re.S)
    assert nav is not None
    hrefs = re.findall(r'href="#group-([a-z]+)"', nav.group())
    assert hrefs == ["videos", "follow", "community", "support", "work", "shop", "blog"]
    assert "position: sticky" in static_text(_out, "variants", "css")
    assert "env(safe-area-inset-top, 0px)" in static_text(_out, "variants", "css")


def test_bento_uses_real_labeled_links_and_one_large_status_tile(
    tmp_path: Path,
) -> None:
    _out, html = build(tmp_path, "bento")
    assert html.count('class="variant-status variant-status-large') == 1
    assert "variant-tile" in html
    assert "Latest videos link 1" in html
    bento = re.search(r'<div class="variant-bento".*?</div>\s*</div>', html, re.S)
    assert bento is not None
    assert "aria-label=" not in bento.group()


def test_tabs_fallback_shows_every_heading_and_script_enhances_accessibly(
    tmp_path: Path,
) -> None:
    out, html = build(tmp_path, "tabs")
    assert html.count('role="tab"') == 5
    assert 'role="tablist" aria-label="Link groups"' in html
    assert 'role="tabpanel"' in html
    panels = re.search(r'<div class="variant-tab-panels".*?</div>\s*</div>', html, re.S)
    assert panels is not None
    assert not re.search(r"\shidden(?:\s|>)", panels.group())
    for heading in ("Watch", "Community", "Support", "Work with me", "Shop"):
        assert re.search(rf"<h2[^>]*>{re.escape(heading)}</h2>", html)
    script_tag = re.search(
        r'<script src="([^"]*variants\.[^"]+\.js)" defer></script>', html
    )
    assert script_tag is not None
    assert not script_tag.group(1).startswith(("http:", "https:", "//"))
    script = static_text(out, "variants", "js")
    for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
        assert key in script
    assert "aria-selected" in script and ".hidden" in script


def test_variant_css_pins_mobile_tablet_desktop_and_accessibility_rules(
    tmp_path: Path,
) -> None:
    out, _html = build(tmp_path, "bento")
    css = static_text(out, "variants", "css")
    assert "min-height: var(--ra-tap)" in css
    assert "gap: var(--ra-space-2)" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "@media (min-width: 48rem)" in css
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in css
    assert "@media (min-width: 56rem)" in css
    assert "grid-template-columns: repeat(6, minmax(0, 1fr))" in css
    assert "subgrid" not in css
    assert "prefers-reduced-motion: reduce" in css
    assert "light" not in re.sub(r"highlight", "", css).lower()


@pytest.mark.parametrize("layout", ["stack", "chips", "bento", "tabs"])
def test_variants_load_no_external_resource(tmp_path: Path, layout: str) -> None:
    _out, html = build(tmp_path, layout)
    resources = re.findall(r'<(?:script|link|img)\b[^>]+(?:src|href)="([^"]+)"', html)
    assert resources
    assert not [url for url in resources if url.startswith(("http:", "https:", "//"))]


def test_variant_does_not_request_a_remote_creator_avatar(tmp_path: Path) -> None:
    data = raw("stack")
    data["creator"]["avatar"] = "https://images.example.com/avatar.png"
    out = tmp_path / "remote-avatar"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "images.example.com" not in html


def test_side_by_side_builder_writes_every_layout_under_review_paths(
    tmp_path: Path,
) -> None:
    out = tmp_path / "review"
    out.mkdir()
    (out / "stale.txt").write_text("old", encoding="utf-8")
    manifest = build_variants(SiteConfig.model_validate(raw("stack")), out, today=TODAY)
    for layout in ("classic", "stack", "chips", "bento", "tabs"):
        page = out / "v" / layout / "index.html"
        assert page.is_file()
        html = page.read_text(encoding="utf-8")
        if layout == "classic":
            assert 'class="variant ' not in html
        else:
            assert f'class="variant variant-{layout}"' in html
    assert not (out / "stale.txt").exists()
    assert manifest.links
    assert manifest.inventory
    assert all(path.startswith("v/") for path in manifest.inventory)
