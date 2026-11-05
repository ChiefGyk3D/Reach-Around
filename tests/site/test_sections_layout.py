# SPDX-License-Identifier: Apache-2.0
"""The optional single-page ``sections`` layout: tabs 2..n render as native details.

Everything here is synthetic. Wallet addresses are made up and belong to no
wallet.
"""

from __future__ import annotations

import re
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.theme import Theme
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
VARIANT = ROOT / "examples" / "instance-variants" / "sections" / "site.yaml"
THEMES = ROOT / "examples" / "themes"
STATIC = resources.files("reach_around.site") / "static"
SITE_CSS = (STATIC / "site.css").read_text()
DOC = (ROOT / "docs" / "config.md").read_text()
TODAY = date(2027, 3, 10)
ADDR = "bc1qexampleexampleexampleexampleexample"

SCRIPT_TAG = re.compile(r"<script\b[^>]*>")
INLINE_STYLE = re.compile(r"<[^>]*\sstyle\s*=", re.IGNORECASE)
INLINE_HANDLER = re.compile(r"<[^>]*\son[a-z]+\s*=", re.IGNORECASE)
DETAILS = re.compile(r"<details\b([^>]*)>")


def site(layout: str | None = "sections", **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "tabs": [
            {
                "id": "socials",
                "title": "Socials",
                "blocks": [
                    {"id": "home", "title": "Home", "url": "https://example.com/"}
                ],
            },
            {
                "id": "support",
                "title": "Support",
                "blocks": [
                    {"id": "tip", "title": "Tip", "url": "https://tip.example.com/"},
                    {
                        "id": "more",
                        "title": "More ways",
                        "links": [
                            {"id": "shop", "title": "Shop", "url": "https://s.example/"}
                        ],
                    },
                ],
            },
            {
                "id": "crypto",
                "title": "Send crypto",
                "blocks": [
                    {
                        "id": "wallets",
                        "title": "Wallets",
                        "coins": [{"coin": "btc", "address": ADDR}],
                    }
                ],
            },
            {
                "id": "recs",
                "title": "Product recs",
                "blocks": [
                    {
                        "id": "mic",
                        "title": "Mic",
                        "url": "https://shop.example.com/mic",
                        "affiliate": True,
                    }
                ],
            },
            {
                "id": "contact",
                "title": "Contact",
                "blocks": [{"id": "note", "markdown": "Write any time."}],
            },
        ],
    }
    if layout is not None:
        data["page_layout"] = layout
    data.update(over)
    return data


def build(tmp_path: Path, data: dict[str, Any]) -> Path:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    return out


def sections(html: str) -> list[tuple[str, bool]]:
    """(id, open) of each top-level tab section, in page order."""
    found = []
    for attrs in DETAILS.findall(html):
        if 'class="tab-section"' in attrs:
            ident = re.search(r'id="([^"]+)"', attrs)
            assert ident, attrs
            found.append((ident.group(1), " open" in attrs))
    return found


def test_tabs_stays_the_default(tmp_path: Path) -> None:
    out = build(tmp_path, site(layout=None))
    assert (out / "support" / "index.html").is_file()
    assert sections((out / "index.html").read_text()) == []


def test_sections_writes_one_page_without_tab_pages(tmp_path: Path) -> None:
    out = build(tmp_path, site())
    pages = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html"))
    assert pages == ["404.html", "index.html", "privacy/index.html"]
    html = (out / "index.html").read_text()
    assert 'class="tabs"' not in html


def test_other_tabs_are_details_and_only_the_first_is_open(tmp_path: Path) -> None:
    html = (build(tmp_path, site()) / "index.html").read_text()
    assert sections(html) == [
        ("support", True),
        ("crypto", False),
        ("recs", False),
        ("contact", False),
    ]
    for title in ("Support", "Send crypto", "Product recs", "Contact"):
        assert f'<h2 class="tab-section-title">{title}</h2>' in html
    # The first tab is the page body, not a collapsible.
    assert html.index('class="blocks') < html.index('class="tab-section"')
    assert "Socials</h2>" not in html


def test_open_can_be_set_per_tab(tmp_path: Path) -> None:
    data = site()
    data["tabs"][1]["open"] = False
    data["tabs"][3]["open"] = True
    html = (build(tmp_path, data) / "index.html").read_text()
    assert sections(html) == [
        ("support", False),
        ("crypto", False),
        ("recs", True),
        ("contact", False),
    ]


def test_a_tab_with_nothing_visible_is_skipped_and_the_next_opens(
    tmp_path: Path,
) -> None:
    data = site()
    data["tabs"][1]["blocks"] = [
        {
            "id": "gone",
            "title": "Gone",
            "url": "https://e.example/",
            "show_until": "2027-01-01",
        }
    ]
    html = (build(tmp_path, data) / "index.html").read_text()
    assert sections(html)[0] == ("crypto", True)
    assert "support" not in dict(sections(html))


def test_primary_actions_are_visible_buttons_and_crypto_collapses(
    tmp_path: Path,
) -> None:
    html = (build(tmp_path, site()) / "index.html").read_text()
    support = html[html.index('id="support"') : html.index('id="crypto"')]
    # The tip button is a link in the open section, not inside a nested details.
    tip = support.index('href="https://tip.example.com/"')
    assert tip < support.index('<details class="section"')
    assert 'class="tab-section" id="support" open' in html
    # The secondary group collapses, and crypto sits inside a closed section.
    assert '<details class="section">' in support
    start = html.index('<details class="tab-section" id="crypto"')
    crypto = html[start : html.index('id="recs"')]
    assert 'class="crypto-address"' in crypto
    first = DETAILS.search(crypto)
    assert first is not None and " open" not in first.group(1)
    assert 'id="crypto"' in first.group(1)


def test_click_tracking_sponsored_and_csp_rules_hold(tmp_path: Path) -> None:
    data = site()
    data["features"] = {"click_tracking": True}
    out = build(tmp_path, data)
    html = (out / "index.html").read_text()
    assert 'ping="/e/c/mic"' in html and 'ping="/e/c/tip"' in html
    mic = re.search(r'<a [^>]*data-link-id="mic"[^>]*>', html)
    assert mic and 'rel="sponsored noopener noreferrer"' in mic.group(0)
    tip = re.search(r'<a [^>]*data-link-id="tip"[^>]*>', html)
    assert tip and "sponsored" not in tip.group(0)
    assert not INLINE_STYLE.search(html) and not INLINE_HANDLER.search(html)
    assert "<style" not in html
    scripts = SCRIPT_TAG.findall(html)
    assert scripts and all(" src=" in tag for tag in scripts)


def test_the_layout_adds_no_script(tmp_path: Path) -> None:
    data = site()
    del data["tabs"][2]  # no crypto block, so no copy script
    data["features"] = {"click_tracking": False}
    assert "<script" not in (build(tmp_path, data) / "index.html").read_text()


def test_tab_links_become_fragments_on_the_single_page(tmp_path: Path) -> None:
    data = site()
    data["tabs"][0]["blocks"].append(
        {"id": "jump", "title": "Jump", "url": "tab:crypto"}
    )
    data["tabs"][0]["blocks"].append(
        {"id": "jump2", "title": "Jump2", "url": "tab:support#tip"}
    )
    out = build(tmp_path, data)
    html = (out / "index.html").read_text()
    assert 'href="#crypto"' in html and 'href="#tip"' in html


def test_privacy_and_404_have_no_dead_tab_links(tmp_path: Path) -> None:
    out = build(tmp_path, site())
    for page in ("privacy/index.html", "404.html"):
        html = (out / page).read_text()
        assert 'class="tabs"' not in html, page
        assert "support/" not in html, page


def test_unknown_layout_is_refused() -> None:
    with pytest.raises(ValidationError, match="page_layout"):
        SiteConfig.model_validate(site(layout="carousel"))


def test_open_must_be_a_boolean() -> None:
    data = site()
    data["tabs"][1]["open"] = "yes please"
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


# --- accessibility guards (source level; no browser) --------------------------


def rule_body(css: str, selector: str) -> str:
    """The declarations of the first rule whose selector list contains ``selector``."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        parts = [s.strip() for s in match.group(1).split(",")]
        if selector in parts:
            return match.group(2)
    return ""


def test_summary_is_at_least_44px_and_focus_visible() -> None:
    body = rule_body(SITE_CSS, ".tab-section > summary")
    assert (
        "min-height: calc(var(--ra-tap)" in body or "min-height: var(--ra-tap)" in body
    )
    assert "cursor: pointer" in body
    focus = rule_body(SITE_CSS, "summary:focus-visible")
    assert "outline: 3px solid" in focus


def test_the_guard_bites_on_a_summary_without_a_target_size() -> None:
    assert rule_body(
        ".tab-section > summary { cursor: pointer; }", ".tab-section > summary"
    )
    stripped = ".tab-section > summary { cursor: pointer; }"
    assert "min-height" not in rule_body(stripped, ".tab-section > summary")
    assert rule_body("a { color: red; }", ".tab-section > summary") == ""


def test_the_section_title_is_a_heading_inside_the_summary(tmp_path: Path) -> None:
    html = (build(tmp_path, site()) / "index.html").read_text()
    assert html.count("<summary><h2 ") >= 4
    assert "tabindex" not in html


def test_reduced_motion_still_covers_the_new_section() -> None:
    assert ".tab-section" in SITE_CSS
    tail = SITE_CSS[SITE_CSS.index("prefers-reduced-motion") :]
    assert "::details-content" in tail


# --- the shipped example and the preset -----------------------------------------


def test_the_example_variant_uses_the_layout_with_synthetic_data(
    tmp_path: Path,
) -> None:
    config = load_site_config(VARIANT)
    assert config.page_layout == "sections"
    assert config.theme.mode == "dark"
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, protect=[VARIANT])
    html = (out / "index.html").read_text()
    assert [i for i, _ in sections(html)][0:2] == ["support", "crypto"]
    assert sections(html)[0][1] is True
    raw = VARIANT.read_text()
    for address in re.findall(r"address:\s*(\S+)", raw):
        assert "example" in address.lower(), address


def test_brand_dark_preset_carries_the_kit_palette() -> None:
    theme = Theme.model_validate(
        yaml.safe_load((THEMES / "brand-dark.yaml").read_text())
    )
    assert theme.mode == "dark"
    assert {k: v.lower() for k, v in theme.colors.items()} == {
        "accent": "#fbb03b",
        "on_accent": "#1a1a1a",
        "bg": "#0f0f0f",
        "surface": "#202020",
        "text": "#f0f0f0",
        "muted": "#b5ada2",
        "border": theme.colors["border"].lower(),
    }
    for font in (theme.fonts.body, theme.fonts.heading):
        assert font is None or "://" not in font


def test_the_sections_layout_is_documented() -> None:
    assert "page_layout" in DOC and "sections" in DOC
    assert "brand-dark" in (ROOT / "docs" / "design.md").read_text()


# --- review fixes: links from nested pages and to omitted tabs ---------------------


def _social_tab_link(data: dict[str, Any], tab: str) -> dict[str, Any]:
    data["creator"]["socials"] = [{"name": "Go", "url": f"tab:{tab}", "icon": "link"}]
    return data


def _go_href(html: str) -> str:
    match = re.search(r'<a [^>]*href="([^"]*)"[^>]*>[^<]*(?:<[^>]+>)*\s*Go', html)
    assert match, "social link not found"
    return match.group(1)


@pytest.mark.parametrize(
    ("page", "prefix"),
    [("index.html", ""), ("privacy/index.html", "../"), ("404.html", "/")],
)
def test_a_section_link_keeps_the_home_prefix_on_nested_pages(
    tmp_path: Path, page: str, prefix: str
) -> None:
    out = build(tmp_path, _social_tab_link(site(), "contact"))
    href = _go_href((out / page).read_text())
    assert href == (f"{prefix}#contact" if prefix else "#contact"), href


def test_a_link_to_an_omitted_tab_lands_on_the_page_not_a_missing_id(
    tmp_path: Path,
) -> None:
    data = _social_tab_link(site(), "recs")
    data["tabs"][3]["blocks"] = [
        {
            "id": "old",
            "title": "Old",
            "url": "https://e.example/",
            "show_until": "2027-01-01",
        }
    ]
    out = build(tmp_path, data)
    html = (out / "index.html").read_text()
    assert 'id="recs"' not in html
    assert _go_href(html) in ("./", "")
    assert _go_href((out / "privacy/index.html").read_text()) == "../"
