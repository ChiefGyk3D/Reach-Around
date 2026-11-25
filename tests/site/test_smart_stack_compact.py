# SPDX-License-Identifier: Apache-2.0
"""The compact tile grid of the smart stack (#873): what the page contains."""

from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_design import _hex_to_rgb, contrast

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
HERE = Path(__file__).parent
GOLDEN = HERE / "golden_smart_stack_compact" / "index.html"
EXAMPLE = HERE.parents[1] / "examples" / "instance" / "site-link-grid.yaml"

# group id -> (intent, title, columns or None, [(link title, icon)])
GROUPS: list[tuple[str, str, int | None, list[tuple[str, str]]]] = [
    (
        "watch",
        "Live now",
        None,
        [("Twitch", "twitch"), ("Kick", "kick"), ("YouTube Live", "youtube")],
    ),
    (
        "videos",
        "Videos",
        None,
        [("YouTube", "youtube"), ("TikTok", "tiktok"), ("Instagram", "instagram")],
    ),
    (
        "follow",
        "Follow",
        None,
        [
            ("Bluesky", "bluesky"),
            ("Mastodon", "mastodon"),
            ("X", "x"),
            ("Threads", "threads"),
            ("Facebook", "facebook"),
        ],
    ),
    (
        "community",
        "Chat",
        None,
        [("Discord", "discord"), ("Matrix", "matrix"), ("Telegram", "telegram")],
    ),
    (
        "support",
        "Support",
        3,
        [
            ("Patreon", "patreon"),
            ("Ko-fi", "kofi"),
            ("Stream tip", "streamelements"),
            ("Merch", "merch"),
            ("Crypto", "bitcoin"),
        ],
    ),
    (
        "gear",
        "Gear I Use",
        None,
        [("Radio", "radio"), ("Security", "shield"), ("Creator", "tools")],
    ),
    (
        "more",
        "More",
        None,
        [
            ("Website", "website"),
            ("GitHub", "github"),
            ("Email", "email"),
            ("RSS", "rss"),
        ],
    ),
]
LINKS = sum(len(g[3]) for g in GROUPS)


def raw(**page: Any) -> dict[str, Any]:
    tabs: list[dict[str, Any]] = []
    for intent, title, columns, links in GROUPS:
        tab: dict[str, Any] = {
            "id": intent,
            "title": title,
            "intent": intent,
            "blocks": [
                {
                    "id": f"{intent}-{n}",
                    "title": name,
                    "url": f"https://example.invalid/{intent}/{n}",
                    "icon": icon,
                    "subtitle": "A line that tiles never print",
                }
                for n, (name, icon) in enumerate(links, 1)
            ],
        }
        if columns:
            tab["columns"] = columns
        tabs.append(tab)
    return {
        "version": 1,
        "creator": {"name": "Example Creator"},
        "features": {"click_tracking": True},
        "page": {
            "layout": "stack",
            "density": "compact",
            "status": {"text": "Live Friday at 8 PM Eastern"},
            "watch": ["watch-1", "watch-2"],
            **page,
        },
        "tabs": tabs,
    }


def build(tmp_path: Path, data: dict[str, Any] | None = None) -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data or raw()), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def smart_css(out: Path) -> str:
    path = next((out / "static").glob("smart-compact.*.css"))
    return path.read_text(encoding="utf-8")


def group(html: str, intent: str) -> str:
    start = html.index(f'id="group-{intent}"')
    nxt = html.find('<section class="smart-group ', start)
    return html[start : nxt if nxt != -1 else len(html)]


def test_every_link_is_one_tile_with_an_icon_and_a_text_label(tmp_path: Path) -> None:
    _out, html = build(tmp_path)
    tiles = re.findall(r'<a class="link[^"]*smart-tile[^"]*"[^>]*>.*?</a>', html, re.S)
    assert len(tiles) == LINKS == 26
    for tile in tiles:
        assert 'class="link-chip"' in tile and 'aria-hidden="true"' in tile
        assert re.search(r'<span class="link-title">[^<]+</span>', tile)
        assert "link-subtitle" not in tile
    assert "A line that tiles never print" not in html


def test_every_group_is_named_open_and_in_config_order(tmp_path: Path) -> None:
    _out, html = build(tmp_path)
    titles = re.findall(r"<h2 id=\"group-[a-z]+-title\">([^<]+)</h2>", html)
    assert titles == [g[1] for g in GROUPS]
    assert "<details" not in html and "<summary" not in html
    assert "smart-jump" not in html and 'role="tab' not in html
    assert " hidden" not in html and "display: none" not in html
    for intent, *_ in GROUPS:
        assert f'aria-labelledby="group-{intent}-title"' in html


def test_twitch_and_kick_get_the_same_markup(tmp_path: Path) -> None:
    _out, html = build(tmp_path)
    live = group(html, "watch")
    twitch, kick = (
        re.search(rf'<a class="([^"]*)"[^>]*>.*?{name}', live, re.S)
        for name in ("Twitch", "Kick")
    )
    assert twitch and kick and twitch.group(1) == kick.group(1)
    # YouTube Live is the same treatment, not a different class.
    assert len(re.findall(r'class="link smart-tile smart-span-20"', live)) == 3


def test_links_keep_attributes_and_click_counting(tmp_path: Path) -> None:
    _out, html = build(tmp_path)
    tiles = re.findall(r'<a class="link[^"]*smart-tile[^"]*"[^>]*>', html)
    assert len(tiles) == LINKS
    assert all('rel="noopener noreferrer"' in t and "data-link-id=" in t for t in tiles)
    assert all('ping="/e/c/' in t for t in tiles)
    assert 'href="https://example.invalid/more/2"' in html


def test_columns_follow_the_link_count_and_a_short_last_row_stretches(
    tmp_path: Path,
) -> None:
    _out, html = build(tmp_path)
    assert "smart-cols-3" in group(html, "videos")
    assert "smart-cols-5" in group(html, "follow")
    assert group(html, "follow").count("smart-span-12") == 5
    assert "smart-cols-4" in group(html, "more")
    assert group(html, "more").count("smart-span-15") == 4
    # Support asks for three columns: 3 + 2, the last two stretch to half each.
    support = group(html, "support")
    assert "smart-cols-3" in support
    assert support.count("smart-span-20") == 3
    assert support.count("smart-span-30") == 2


def test_six_links_default_to_three_columns(tmp_path: Path) -> None:
    data = raw()
    more = next(t for t in data["tabs"] if t["intent"] == "more")
    more["blocks"] += [
        {"id": "more-5", "title": "Five", "url": "https://example.invalid/5"},
        {"id": "more-6", "title": "Six", "url": "https://example.invalid/6"},
    ]
    _out, html = build(tmp_path, data)
    assert "smart-cols-3" in group(html, "more")
    assert group(html, "more").count("smart-span-20") == 6


def test_the_status_line_and_a_live_badge_stay(tmp_path: Path) -> None:
    data = raw(live_badges=True)
    _out, html = build(tmp_path, data)
    assert "Live Friday at 8 PM Eastern" in html
    live = group(html, "watch")
    assert live.count("data-watch-badge") == 2  # Twitch and Kick, not the third
    assert 'data-watch-badge="twitch"' in live and 'data-watch-badge="kick"' in live
    assert "data-watch-status" in live


def test_comfortable_density_keeps_the_deck(tmp_path: Path) -> None:
    data = raw(density="comfortable")
    for tab in data["tabs"]:
        tab.pop("columns", None)
        if tab["intent"] == "watch":
            tab["blocks"] = tab["blocks"][:2]
    _out, html = build(tmp_path, data)
    assert "smart-tile" not in html and "smart-compact" not in html
    assert "smart-jump" in html


def test_compact_css_is_tokens_only_dark_only_and_rem_only(tmp_path: Path) -> None:
    out, _html = build(tmp_path)
    css = smart_css(out)
    block = re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # rules, not comments
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", block)
    assert not re.search(r"\d\s*px\b", block)
    assert "light-dark" not in block and "prefers-color-scheme" not in block
    assert "var(--ra-tap)" in block  # tap target floor


def test_tiles_never_drop_below_the_tap_size(tmp_path: Path) -> None:
    out, _html = build(tmp_path)
    css = smart_css(out)
    rule = re.search(r"\.smart-compact a\.link\.smart-tile \{[^}]*\}", css)
    assert rule
    assert "min-height: var(--ra-tap)" in rule.group(0)


def test_the_page_works_without_script_for_the_tiles(tmp_path: Path) -> None:
    out, html = build(tmp_path)
    # Only click counting and the shared view script may load; no tile needs one.
    for tile in re.findall(r'<a class="link[^"]*smart-tile[^"]*"[^>]*>', html):
        assert "onclick" not in tile and "javascript:" not in tile
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)


def test_the_example_instance_grid_builds_and_matches_the_contract(
    tmp_path: Path,
) -> None:
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    config = SiteConfig.model_validate(doc)
    assert config.page.density == "compact"
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert len(re.findall(r"smart-tile", html)) >= 26
    assert "Gear I Use" in html
    assert "rednote" not in html.lower()
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "—" not in text
    assert not re.search(r"https?://(?!example\.(?:com|invalid|org)\b)", text)


def test_compact_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path)
    if update_golden:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(html, encoding="utf-8")
    assert GOLDEN.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN.read_text(encoding="utf-8")


def test_a_hostile_title_is_escaped_in_a_tile(tmp_path: Path) -> None:
    data = copy.deepcopy(raw())
    data["tabs"][1]["blocks"][0]["title"] = '<img src=x onerror="boom()">'
    _out, html = build(tmp_path, data)
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


@pytest.mark.parametrize("columns", [1, 2, 6])
def test_explicit_columns_set_the_class(tmp_path: Path, columns: int) -> None:
    data = raw()
    for tab in data["tabs"]:
        if tab["intent"] == "videos":
            tab["columns"] = columns
    _out, html = build(tmp_path, data)
    assert f"smart-cols-{columns}" in group(html, "videos")


def test_focus_order_is_the_reading_order(tmp_path: Path) -> None:
    _out, html = build(tmp_path)
    hrefs = re.findall(r'<a class="link[^"]*smart-tile[^"]*" href="([^"]+)"', html)
    expected = [
        f"https://example.invalid/{intent}/{n}"
        for intent, _t, _c, links in GROUPS
        for n in range(1, len(links) + 1)
    ]
    assert hrefs == expected
    assert html.index('class="skip-link"') < html.index("smart-tile")
    assert 'tabindex="' not in html


def test_tile_text_and_marks_meet_contrast_on_the_tile(tmp_path: Path) -> None:
    out, _html = build(tmp_path)
    tokens = (next((out / "static").glob("tokens.*.css"))).read_text(encoding="utf-8")

    def token(name: str) -> tuple[float, float, float]:
        found = re.search(rf"--ra-brand-{name}:\s*(#[0-9A-Fa-f]{{6}})", tokens)
        assert found, name
        return _hex_to_rgb(found.group(1))

    surface = token("surface")
    assert contrast(token("text"), surface) >= 4.5  # the label
    assert contrast(token("amber"), surface) >= 3.0  # the icon (a graphic)
    assert contrast(token("amber"), token("ground")) >= 4.5  # the group heading
    assert contrast(token("text"), token("ground")) >= 4.5  # the status line


def test_the_compact_stylesheet_is_only_published_for_compact_density(
    tmp_path: Path,
) -> None:
    out, html = build(tmp_path)
    assert list((out / "static").glob("smart-compact.*.css"))
    assert "smart-compact." in html
    data = raw(density="comfortable")
    for tab in data["tabs"]:
        tab.pop("columns", None)
        if tab["intent"] == "watch":
            tab["blocks"] = tab["blocks"][:2]
    plain_out, plain = build(tmp_path / "plain", data)
    assert not list((plain_out / "static").glob("smart-compact.*.css"))
    assert "smart-compact." not in plain
