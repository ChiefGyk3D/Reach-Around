# SPDX-License-Identifier: Apache-2.0
"""Group styles of the smart stack: buttons, rows and icons (#672)."""

from __future__ import annotations

import copy
import re
from datetime import date
from pathlib import Path
from typing import Any

from test_design import _mix, _token_sets, contrast
from test_smart_stack import raw as stack_raw

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TODAY = date(2027, 3, 10)
HERE = Path(__file__).parent
GOLDEN_ROWS = HERE / "golden_smart_stack_rows" / "index.html"
GOLDEN_ICONS = HERE / "golden_smart_stack_icons" / "index.html"
SOCIALS: list[dict[str, Any]] = [
    {"name": "Twitch", "url": "https://example.com/t", "icon": "twitch"},
    {
        "name": "Mastodon",
        "url": "https://example.com/@me",
        "icon": "mastodon",
        "rel_me": True,
    },
    {"name": "Kick", "url": "https://example.com/k", "icon": "kick"},
    {"name": "Newsletter", "url": "https://example.com/n", "icon": "no-such-icon"},
]


def raw(*, rows: tuple[str, ...] = (), icons: bool = False) -> dict[str, Any]:
    data = stack_raw()
    for tab in data["tabs"]:
        if tab["intent"] in rows:
            tab["style"] = "rows"
    if icons:
        data["creator"]["socials"] = SOCIALS
        follow = next(t for t in data["tabs"] if t["intent"] == "follow")
        follow.update(style="icons", blocks=[])
    return data


def build(tmp_path: Path, data: dict[str, Any]) -> tuple[Path, str]:
    out = tmp_path / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    return out, (out / "index.html").read_text(encoding="utf-8")


def smart_css(out: Path) -> str:
    return next((out / "static").glob("smart-stack.*.css")).read_text(encoding="utf-8")


def group(html: str, intent: str) -> str:
    start = html.index(f'id="group-{intent}"')
    return html[start : html.index("</section>", start)]


def watch(html: str) -> str:
    match = re.search(r'<div class="smart-watch".*?</div>', html, re.DOTALL)
    assert match
    return match.group(0)


# --- buttons: the default is today's output ---------------------------------


def test_the_default_word_changes_nothing(tmp_path: Path) -> None:
    _a, plain = build(tmp_path / "a", stack_raw())
    data = stack_raw()
    for tab in data["tabs"]:
        tab["style"] = "buttons"
    _b, named = build(tmp_path / "b", data)
    assert named == plain
    assert "smart-style" not in plain and "smart-icons" not in plain


# --- rows --------------------------------------------------------------------


def test_rows_keep_the_link_markup_and_only_add_a_group_class(tmp_path: Path) -> None:
    _a, plain = build(tmp_path / "a", raw())
    _b, rows = build(tmp_path / "b", raw(rows=("videos",)))
    section = group(rows, "videos")
    assert '<div class="smart-group-links smart-style-rows">' in section
    assert "smart-style" not in group(rows, "follow")
    assert rows.replace(" smart-style-rows", "") == plain
    for number in (1, 2):
        assert f'ping="/e/c/videos-{number}" data-link-id="videos-{number}"' in section
    assert section.count('rel="noopener noreferrer"') == 2


def test_rows_leave_the_watch_pair_alone(tmp_path: Path) -> None:
    _a, plain = build(tmp_path / "a", raw())
    every = ("videos", "follow", "community", "support", "work", "shop", "blog")
    _b, rows = build(tmp_path / "b", raw(rows=every))
    assert watch(rows) == watch(plain)


def swapped(data: dict[str, Any]) -> dict[str, Any]:
    other = copy.deepcopy(data)
    for link in other["tabs"][0]["blocks"]:
        if link["icon"] == "twitch":
            link.update(icon="kick", title="Watch on Kick")
        else:
            link.update(icon="twitch", title="Watch on Twitch")
    return other


def blind(text: str, first: str, second: str) -> str:
    """Name the two platforms by position, so only the names can differ."""
    return re.sub(second, "P2", re.sub(first, "P1", text, flags=re.I), flags=re.I)


def test_twitch_and_kick_parity_survives_every_style(tmp_path: Path) -> None:
    every = ("videos", "follow", "community", "support", "work", "shop", "blog")
    data = raw(rows=every)
    for link in data["tabs"][0]["blocks"]:
        link["title"] = "Watch on " + str(link["icon"]).title()
    _a, one = build(tmp_path / "a", data)
    _b, two = build(tmp_path / "b", swapped(data))
    assert watch(one) != watch(two)
    assert blind(watch(one), "twitch", "kick") == blind(watch(two), "kick", "twitch")
    _c, icons = build(tmp_path / "c", raw(icons=True))
    _d, plain = build(tmp_path / "d", raw())
    assert watch(icons) == watch(plain)


def test_the_parity_swap_bites_on_a_special_case(tmp_path: Path) -> None:
    data = raw(rows=("videos",))
    for link in data["tabs"][0]["blocks"]:
        link["title"] = "Watch on " + str(link["icon"]).title()
    _a, one = build(tmp_path / "a", data)
    _b, two = build(tmp_path / "b", swapped(data))
    special = watch(one).replace('data-link-id="watch-2"', 'data-link-id="watch-2" x')
    assert blind(special, "twitch", "kick") != blind(watch(two), "kick", "twitch")


def test_rows_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path, raw(rows=("videos", "community")))
    if update_golden:
        GOLDEN_ROWS.parent.mkdir(exist_ok=True)
        GOLDEN_ROWS.write_text(html, encoding="utf-8")
    assert GOLDEN_ROWS.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN_ROWS.read_text(encoding="utf-8")


# --- icons -------------------------------------------------------------------


def test_icons_come_from_creator_socials_in_order_with_accessible_names(
    tmp_path: Path,
) -> None:
    _out, html = build(tmp_path, raw(icons=True))
    section = group(html, "follow")
    assert '<ul class="socials smart-icons">' in section
    hrefs = re.findall(r'<a class="social[^"]*" href="([^"]+)"', section)
    assert hrefs == [s["url"] for s in SOCIALS]
    names = re.findall(r'<span class="sr-only">([^<]+)</span>', section)
    assert names == ["Twitch", "Mastodon", "Kick"]
    # An icon with no drawing falls back to its visible name, still a link.
    assert '<span class="social-label">Newsletter</span>' in section
    assert section.count("<a ") == len(SOCIALS)
    assert section.count('aria-hidden="true"') == 3
    assert 'rel="me noopener noreferrer"' in section
    # Fed from creator.socials only: no link entries, no click-count markup.
    assert "data-link-id" not in section and "ping=" not in section


def test_icons_show_each_social_once_not_twice(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw(icons=True))
    assert html.count('href="https://example.com/t"') == 2  # header and Follow
    assert group(html, "follow").count('href="https://example.com/t"') == 1


def test_icons_html_matches_golden(tmp_path: Path, update_golden: bool) -> None:
    _out, html = build(tmp_path, raw(icons=True))
    if update_golden:
        GOLDEN_ICONS.parent.mkdir(exist_ok=True)
        GOLDEN_ICONS.write_text(html, encoding="utf-8")
    assert GOLDEN_ICONS.is_file(), "no golden file; run pytest --update-golden"
    assert html == GOLDEN_ICONS.read_text(encoding="utf-8")


def test_icons_order_follows_creator_socials(tmp_path: Path) -> None:
    data = raw(icons=True)
    data["creator"]["socials"] = list(reversed(SOCIALS))
    _out, html = build(tmp_path, data)
    hrefs = re.findall(r'<a class="social[^"]*" href="([^"]+)"', group(html, "follow"))
    assert hrefs == [s["url"] for s in reversed(SOCIALS)]


def test_icons_can_be_collapsed_too(tmp_path: Path) -> None:
    data = raw(icons=True)
    next(t for t in data["tabs"] if t["intent"] == "follow")["collapsed"] = True
    _out, html = build(tmp_path, data)
    section = group(html, "follow")
    assert "<details" in section and 'class="socials smart-icons"' in section


# --- css: targets, reflow, print, contrast, weight, no script -----------------


def rule(css: str, selector: str) -> str:
    found = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
    assert found, f"no rule for {selector}"
    return found.group(1)


def test_rows_css_is_a_44px_single_column_that_wraps_and_prints(
    tmp_path: Path,
) -> None:
    out, _html = build(tmp_path, raw(rows=("videos",)))
    css = smart_css(out)
    body = rule(css, ".smart-group-links.smart-style-rows a.link")
    assert "min-height: var(--ra-tap)" in body
    assert "display: flex" in body
    assert "text-align: start" in body
    text = rule(css, ".smart-style-rows .link-text")
    assert "flex-wrap: wrap" in text and "min-width: 0" in text
    media = css[css.index("@media (min-width: 48rem)") :]
    assert ".smart-group-links.smart-style-rows" in media
    assert "grid-template-columns: minmax(0, 1fr)" in media.split("@media print")[0]
    printed = css[css.index("@media print") :]
    assert "smart-style-rows" in printed
    assert "px" not in re.sub(r"29\.999rem", "", css)


def test_icons_css_keeps_a_44px_target_and_wraps_at_320(tmp_path: Path) -> None:
    out, _html = build(tmp_path, raw(icons=True))
    css = smart_css(out)
    body = rule(css, ".smart-icons")
    assert "flex-wrap: wrap" in body and "justify-content: flex-start" in body
    site = next((out / "static").glob("site.*.css")).read_text(encoding="utf-8")
    social = rule(site, ".social")
    assert "min-width: var(--ra-tap)" in social
    assert "min-height: var(--ra-tap)" in social
    printed = css[css.index("@media print") :]
    assert ".smart-icons a.social::after" in printed and "attr(href)" in printed
    # Inline link (a flex item would swallow the space), a no-break space before
    # the address, and one item per line so icons do not run together.
    assert 'content: "\\00a0("' in printed
    assert re.search(r"\.smart-icons a\.social \{[^}]*display: inline;", printed)
    assert re.search(r"\.smart-icons li \{[^}]*display: block", printed)


def test_the_tap_token_is_44px() -> None:
    site = (
        Path(__file__).parents[2] / "src/reach_around/site/static/tokens.css"
    ).read_text(encoding="utf-8")
    assert re.search(r"--ra-tap:\s*2\.75rem", site)


def test_no_style_adds_a_script_or_a_third_party_request(tmp_path: Path) -> None:
    out, html = build(tmp_path, raw(rows=("videos",), icons=True))
    out0, plain = build(tmp_path / "p", stack_raw())
    assert html.count("<script") == plain.count("<script")

    def origins(page: str) -> set[str]:
        return set(re.findall(r'(?:src|href)="((?:https?:)?//[^/"]+)', page))

    assert origins(html) - origins(plain) <= {"https://example.com"}
    assert 'src="//' not in html and 'src="http' not in html
    assert sorted(p.name for p in (out / "static").glob("*.js")) == sorted(
        p.name for p in (out0 / "static").glob("*.js")
    )


def test_dark_theme_contrast_is_aa_for_every_style() -> None:
    tokens = _token_sets()["dark"]
    bg, surface = tokens["bg"], tokens["surface"]
    text, accent, on_accent = tokens["text"], tokens["accent"], tokens["on-accent"]
    muted = _mix(text, bg, 0.72)
    subtitle = _mix(on_accent, accent, 0.85)
    # buttons: title and subtitle on the accent fill
    assert contrast(on_accent, accent) >= 4.5
    assert contrast(subtitle, accent) >= 4.5
    # rows: the same accent fill, so the same pairs (the row recolors nothing)
    # icons: the glyph is the page text on the page ground, the name is on the
    # same ground when the label fallback shows
    for ground in (bg, surface):
        assert contrast(text, ground) >= 4.5
        assert contrast(muted, ground) >= 4.5
    assert contrast(accent, bg) >= 3, "focus ring"


def test_the_row_and_icon_rules_recolor_only_with_tokens(tmp_path: Path) -> None:
    out, _html = build(tmp_path, raw(rows=("videos",), icons=True))
    css = smart_css(out)
    new = "\n".join(
        line
        for line in css.splitlines()
        if "smart-style" in line or "smart-icons" in line
    )
    assert new
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(|hsl\(", new)


def test_the_keyboard_order_is_the_document_order(tmp_path: Path) -> None:
    _out, html = build(tmp_path, raw(rows=("videos",), icons=True))
    order = [html.index(f'id="group-{i}"') for i in ("videos", "follow", "community")]
    assert order == sorted(order)
    assert "tabindex" not in group(html, "follow")
    assert "tabindex" not in group(html, "videos")


def test_a_stack_using_both_new_styles_stays_inside_the_hub_stack_budget(
    tmp_path: Path,
) -> None:
    from pageweight import measure, over_budget

    every = ("videos", "follow", "community", "support", "work", "shop", "blog")
    out, _html = build(tmp_path, raw(rows=every[:1] + every[2:], icons=True))
    weight = measure(out, out / "index.html")
    assert weight["css"] > 0
    assert over_budget("hub-stack", weight) == []
