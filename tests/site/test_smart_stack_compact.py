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
from pydantic import ValidationError
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
    # Height and width both (the review of #879): a tile is 44 px each way.
    assert re.search(r"min-height: clamp\(var\(--ra-tap\),", rule.group(0))
    assert "min-width: var(--ra-tap)" in rule.group(0)
    # Where four to six across cannot give 44 px a tile, the group falls back.
    assert "@container (max-width: 17.75rem)" in css
    assert "@container (max-width: 14.75rem)" in css


def test_a_label_is_clamped_inside_its_tile(tmp_path: Path) -> None:
    out, _html = build(tmp_path)
    css = re.sub(r"/\*.*?\*/", "", smart_css(out), flags=re.S)
    rule = re.search(r"\.smart-compact \.smart-tile \.link-title \{[^}]*\}", css)
    assert rule
    body = rule.group(0)
    assert "-webkit-line-clamp: 2" in body and "overflow: hidden" in body
    assert "overflow-wrap: anywhere" in body
    assert re.search(r"a\.link\.smart-tile \{[^}]*overflow: hidden", css)
    # The five and six column override must not turn word breaking off again.
    assert "overflow-wrap: normal" not in css


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


# --- date-filtered and empty groups (the review of #879, round 1) -------------


def window(data: dict[str, Any], intent: str, **fields: Any) -> None:
    tab = next(t for t in data["tabs"] if t["intent"] == intent)
    for block in tab["blocks"]:
        block.update(fields)


def test_a_group_with_every_link_expired_is_dropped_not_a_crash(
    tmp_path: Path,
) -> None:
    data = raw()
    window(data, "videos", show_until=date(2027, 3, 1))
    _out, html = build(tmp_path, data)
    assert 'id="group-videos"' not in html and ">Videos<" not in html
    assert len(re.findall(r"smart-tile ", html)) == LINKS - 3
    assert 'id="group-follow"' in html and 'id="group-more"' in html


def test_a_group_with_every_link_not_yet_shown_is_dropped(tmp_path: Path) -> None:
    data = raw()
    window(data, "gear", show_from=date(2099, 1, 1))
    _out, html = build(tmp_path, data)
    assert 'id="group-gear"' not in html and "Gear I Use" not in html


def test_every_group_filtered_leaves_the_watch_group_alone(tmp_path: Path) -> None:
    data = raw()
    for intent, *_ in GROUPS[1:]:
        window(data, intent, show_from=date(2099, 1, 1))
    _out, html = build(tmp_path, data)
    assert len(re.findall(r"smart-tile ", html)) == 3
    assert re.findall(r'<h2 id="group-[a-z]+-title">([^<]+)</h2>', html) == ["Live now"]
    assert "Live Friday at 8 PM Eastern" in html


def test_a_group_down_to_one_visible_link_still_renders(tmp_path: Path) -> None:
    data = raw()
    videos = next(t for t in data["tabs"] if t["intent"] == "videos")
    for block in videos["blocks"][1:]:
        block["show_until"] = date(2027, 3, 1)
    _out, html = build(tmp_path, data)
    one = group(html, "videos")
    assert one.count("smart-tile ") == 1 and "smart-cols-1" in one
    assert "smart-span-60" in one


def test_extra_watch_links_can_be_scheduled_and_the_two_named_never(
    tmp_path: Path,
) -> None:
    data = raw()
    watch = next(t for t in data["tabs"] if t["intent"] == "watch")
    watch["blocks"][2]["show_from"] = date(2099, 1, 1)
    _out, html = build(tmp_path, data)
    live = group(html, "watch")
    assert live.count("smart-tile ") == 2 and "smart-cols-2" in live
    named = raw()
    next(t for t in named["tabs"] if t["intent"] == "watch")["blocks"][0][
        "show_from"
    ] = date(2099, 1, 1)
    with pytest.raises(ValidationError, match="cannot be scheduled"):
        SiteConfig.model_validate(named)


@pytest.mark.parametrize(
    ("fields", "shown"),
    [
        ({"show_from": TODAY}, True),
        ({"show_until": TODAY}, True),
        ({"show_from": date(2027, 3, 11)}, False),
        ({"show_until": date(2027, 3, 9)}, False),
    ],
    ids=["from-today", "until-today", "from-tomorrow", "until-yesterday"],
)
def test_window_boundaries_on_a_compact_group(
    tmp_path: Path, fields: dict[str, date], shown: bool
) -> None:
    data = raw()
    next(t for t in data["tabs"] if t["intent"] == "more")["blocks"][3].update(fields)
    _out, html = build(tmp_path, data)
    assert ("https://example.invalid/more/4" in html) is shown


def test_a_group_with_no_link_at_all_is_refused() -> None:
    data = raw()
    next(t for t in data["tabs"] if t["intent"] == "more")["blocks"] = []
    with pytest.raises(ValidationError, match="2 to 6 links"):
        SiteConfig.model_validate(data)


# --- every key that could hide a link (the review of #879, round 1) -----------

# Each field of every model a compact page reads, and what it does to a link.
# refused: the validator rejects it under compact density. window: a per-link
# date filter (a legitimate one, tested above). inert: set it and every link
# still draws. A new field must be classified here or this module fails.
CLASSIFIED: dict[str, dict[str, str]] = {
    "Page": {
        "layout": "inert",
        "status": "inert",
        "watch": "inert",
        "featured": "refused",
        "live_badges": "inert",
        "density": "inert",
        "url": "inert",
    },
    "Tab": {
        "id": "inert",
        "title": "inert",
        "intent": "inert",
        "layout": "inert",
        "page_title": "inert",
        "open": "inert",
        "collapsed": "refused",
        "style": "refused",
        "spoke": "refused",
        "columns": "inert",
        "blocks": "inert",
    },
    "Section": {
        "id": "inert",
        "title": "inert",
        "subtitle": "inert",
        "collapsed": "inert",
        "layout": "inert",
        "links": "inert",
    },
    "Link": {
        "id": "inert",
        "title": "inert",
        "url": "inert",
        "subtitle": "inert",
        "icon": "inert",
        "image": "inert",
        "image_alt": "inert",
        "affiliate": "inert",
        "featured": "inert",
        "show_from": "window",
        "show_until": "window",
    },
}


def test_every_config_key_is_classified_for_hiding_links() -> None:
    from reach_around.config import models

    for name, fields in CLASSIFIED.items():
        assert set(getattr(models, name).model_fields) == set(fields), name


def test_inert_keys_leave_every_link_on_the_page(tmp_path: Path) -> None:
    data = raw(live_badges=True, url="https://example.invalid")
    videos = next(t for t in data["tabs"] if t["intent"] == "videos")
    videos["blocks"] = [
        {
            "id": "vids",
            "title": "Section",
            "subtitle": "x",
            "collapsed": True,
            "layout": "grid",
            "links": videos["blocks"],
        }
    ]
    for tab in data["tabs"]:
        tab["layout"] = "cards"
        tab["open"] = False
        tab["page_title"] = "Another title"
        if tab["intent"] != "watch":
            tab["columns"] = 1
        for block in tab["blocks"]:
            for link in block["links"] if "links" in block else [block]:
                link["featured"] = True
                link["affiliate"] = True
    _out, html = build(tmp_path, data)
    assert len(re.findall(r"smart-tile ", html)) == LINKS
    assert "<details" not in html


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["page"].update(featured={"link": "videos-1", "kicker": "New"}),
        lambda d: d["tabs"][1].update(collapsed=True),
        lambda d: d["tabs"][1].update(spoke=True),
        lambda d: d["tabs"][1].update(style="rows"),
        lambda d: d["tabs"][1].update(style="icons"),
    ],
    ids=["featured", "collapsed", "spoke", "style-rows", "style-icons"],
)
def test_options_that_hide_or_restyle_links_are_refused(mutate: Any) -> None:
    data = raw()
    mutate(data)
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


# --- hostile input through the real builder (the review of #879, round 1) -----

HOSTILE_TEXT = [
    "<script>alert(1)</script>",
    '"><img src=x onerror=alert(1)>',
    "' onmouseover='boom()",
    "&lt;b&gt; &amp; \u202e\u200b\u2066",
    "x" * 200,
]


@pytest.mark.parametrize("text", HOSTILE_TEXT)
def test_hostile_text_is_escaped_everywhere_a_compact_page_prints_it(
    tmp_path: Path, text: str
) -> None:
    data = raw()
    data["page"]["status"] = {"text": text, "schedule": text}
    data["tabs"][1]["title"] = text
    data["tabs"][1]["blocks"][0]["title"] = text
    _out, html = build(tmp_path, data)
    assert "<script>alert" not in html and "<img src=x" not in html
    assert not re.search(r"<[a-z]+[^>]*\sonerror=", html)
    assert not re.search(r"<[a-z]+[^>]*\sonmouseover=", html)
    if "<" in text:
        assert "&lt;" in html
    if text.startswith("&lt;"):
        assert "&amp;lt;b&amp;gt;" in html  # an entity is shown, not decoded


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        " javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "//example.invalid/x",
        "http://example.invalid/x",
        "vbscript:msgbox(1)",
        "https://example.invalid/a b",
        "https://example.invalid/" + "a" * 2100,
        "https://example.invalid/\u202e",
    ],
)
def test_a_hostile_url_never_reaches_a_tile(url: str) -> None:
    data = raw()
    data["tabs"][1]["blocks"][0]["url"] = url
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)


def test_quotes_and_markup_in_a_valid_url_are_escaped(tmp_path: Path) -> None:
    data = raw()
    data["tabs"][1]["blocks"][0]["url"] = (
        'https://example.invalid/a"onmouseover="x&y=<b>'
    )
    try:
        config = SiteConfig.model_validate(data)
    except ValidationError:
        return  # refused outright is as safe
    out = tmp_path / "out"
    build_site(config, out, today=TODAY)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '"onmouseover=' not in html and "<b>" not in html


def test_a_title_past_the_model_cap_is_refused() -> None:
    data = raw()
    data["tabs"][1]["blocks"][0]["title"] = "x" * 201
    with pytest.raises(ValidationError):
        SiteConfig.model_validate(data)
