# SPDX-License-Identifier: Apache-2.0
"""The deck arrangement of the smart stack (#677, Direction B)."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from test_smart_stack import raw as stack_raw

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
GOLDEN = Path(__file__).parent / "golden_smart_stack_deck" / "index.html"


def raw(
    *, keep: tuple[str, ...] | None = None, featured: bool = False
) -> dict[str, Any]:
    data = stack_raw()
    if keep is not None:
        data["tabs"] = [t for t in data["tabs"] if t["intent"] in ("watch", *keep)]
    if featured:
        data["page"]["featured"] = {"link": "follow-1", "kicker": "New this week"}
    return data


def build(tmp_path: Path, data: dict[str, Any]) -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def smart_css(out: Path) -> str:
    return next((out / "static").glob("smart-stack.*.css")).read_text(encoding="utf-8")


def test_status_and_watch_share_one_on_air_tile_with_a_kicker(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    tile = html[html.index('<div class="smart-onair">') :]
    tile = tile[: tile.index('<div class="smart-groups">')]
    assert '<p class="smart-kicker" aria-hidden="true">Watch</p>' in tile
    assert tile.index("smart-status") < tile.index('class="smart-watch"')
    assert len(re.findall(r'class="[^"]*\bsmart-watch-link\b', tile)) == 2


def test_twitch_and_kick_stay_one_component_twice(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    links = re.findall(r'<a class="(link[^"]*smart-watch-link[^"]*)"', html)
    assert len(links) == 2
    assert links[0] == links[1]


def test_every_group_section_carries_its_intent_class(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    for intent in ("videos", "follow", "community", "support", "work", "shop", "blog"):
        assert re.search(
            rf'<section class="smart-group smart-group-{intent}" id="group-{intent}"',
            html,
        )


def test_jump_chips_link_to_every_group_after_the_watch_tile(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    nav = re.search(
        r'<nav class="smart-jump" aria-label="([^"]+)">(.*?)</nav>', html, re.S
    )
    assert nav, "no jump nav"
    assert nav.group(1) == "Jump to a section"
    hrefs = re.findall(r'<a href="#(group-[a-z]+)">([^<]+)</a>', nav.group(2))
    assert [h for h, _t in hrefs] == [
        f"group-{i}"
        for i in ("videos", "follow", "community", "support", "work", "shop", "blog")
    ]
    assert [t for _h, t in hrefs][1] == "Follow"
    for ident, _title in hrefs:
        assert f'id="{ident}"' in html
    # Watch stays above the chips, and the chips above the groups.
    assert (
        html.index("smart-onair")
        < html.index("smart-jump")
        < html.index("smart-groups")
    )
    assert "role=" not in nav.group(0)


def test_jump_chips_come_after_the_featured_card(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw(featured=True))
    assert (
        html.index("smart-onair")
        < html.index("smart-featured")
        < html.index("smart-jump")
    )


def test_one_group_gets_no_jump_nav(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw(keep=("follow",)))
    assert "smart-jump" not in html.split("</head>")[1]


def test_only_the_stack_home_gets_the_wide_deck_column(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw())
    assert '<main id="content" class="main-deck">' in html
    data = raw()
    data["page"]["url"] = "https://links.example.com"
    data["tabs"][-1]["spoke"] = True
    out, _home = build(tmp_path / "s", data)
    spoke = (out / "blog" / "index.html").read_text(encoding="utf-8")
    assert "main-deck" not in spoke


def test_deck_css_places_grid_areas_two_columns_and_chips(tmp_path: Path) -> None:
    out, _html = build(tmp_path, raw())
    css = smart_css(out)
    assert "@media (min-width: 64rem)" in css
    assert "grid-template-columns: repeat(12, minmax(0, 1fr))" in css
    assert ".smart-group-follow" in css and ".smart-group-community" in css
    assert ".smart-group-support" in css and "var(--ra-brand-plate)" in css
    tablet = css[css.index("@media (min-width: 48rem)") :]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in tablet
    assert re.search(r"\.smart-jump\s*\{\s*display: none", tablet)
    assert re.search(
        r"@media print[^}]*\{[^@]*\.smart-jump\s*\{\s*display: none", css, re.S
    )
    assert "var(--ra-deck-gap" in css and "var(--ra-tile-radius" in css


def test_deck_css_is_rem_only_dark_only_and_has_no_color_literals(
    tmp_path: Path,
) -> None:
    out, _html = build(tmp_path, raw())
    css = re.sub(
        r"/\*.*?\*/", "", smart_css(out), flags=re.S
    )  # comments hold issue numbers
    assert "px" not in re.sub(r"29\.999rem", "", css)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"\b(white|cream|ivory|beige)\b", css, re.I)
    assert "light-dark(" not in css


def test_deck_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path, raw(featured=True))
    if update_golden:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(html, encoding="utf-8")
    assert GOLDEN.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN.read_text(encoding="utf-8")


def test_group_links_are_dark_cards_without_frames_but_buttons_keep_theirs(
    tmp_path: Path,
) -> None:
    data = raw(featured=True)
    data["theme"] = {"frame": "faithful"}
    _out, html = build(tmp_path, data)

    def anchors(intent: str) -> list[str]:
        start = html.index(f'id="group-{intent}"')
        return re.findall(
            r'<a class="([^"]+)"', html[start : html.index("</section>", start)]
        )

    for intent in ("videos", "follow", "community", "work", "shop", "blog"):
        found = anchors(intent)
        assert found, intent
        for cls in found:
            assert "smart-card" in cls and "ra-bx" not in cls, (intent, cls)
    for cls in anchors("support"):
        assert "smart-card" not in cls and "ra-bx" in cls, cls
    watch = re.findall(r'<a class="(link[^"]*smart-watch-link[^"]*)"', html)
    assert len(watch) == 2 and all(
        "ra-bx" in c and "smart-card" not in c for c in watch
    )
    featured = html[html.index('class="smart-featured"') :]
    assert "smart-card" in featured[: featured.index("</section>")]
    assert "ra-bx" not in featured[: featured.index("</section>")]
