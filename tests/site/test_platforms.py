# SPDX-License-Identifier: Apache-2.0
"""The platforms block: config rules, abbreviation, rendering, tracking, assets.

Every handle, count and URL here is synthetic.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig, load_site_config
from reach_around.config.blocks import PLATFORM_ICONS, PLATFORM_SLUGS, Platforms
from reach_around.config.loader import ConfigError
from reach_around.config.numbers import Numbers, load_numbers
from reach_around.site.build import build_site, manifest_json
from reach_around.site.icons import ICON_SLUGS
from reach_around.site.platforms import abbreviate

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "platforms" / "site.yaml"
STATIC = ROOT / "src" / "reach_around" / "site" / "static"
CSS = STATIC / "platforms.css"
TODAY = date(2027, 3, 10)
NOW = datetime(2027, 3, 10, 12, 0, tzinfo=UTC)
HOSTILE = "<script>alert(1)</script>"
CSS_TAG = '<link rel="stylesheet" href="static/platforms.css">'


def item(**over: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "platform": "twitch",
        "handle": "@example",
        "url": "https://example.com/twitch",
    }
    entry.update(over)
    return entry


def block(*items: dict[str, Any], **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"id": "socials", "items": list(items) or [item()]}
    out.update(over)
    return out


def cfg(*blocks: dict[str, Any], **top: Any) -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [{"id": "home", "title": "Home", "blocks": list(blocks)}],
            **top,
        }
    )


def build(config: SiteConfig, tmp_path: Path, numbers: Numbers | None = None) -> Path:
    out = tmp_path / "out"
    build_site(config, out, today=TODAY, numbers=numbers)
    return out


def page(config: SiteConfig, tmp_path: Path, numbers: Numbers | None = None) -> str:
    return (build(config, tmp_path, numbers) / "index.html").read_text("utf-8")


def numbers_file(tmp_path: Path, platforms: dict[str, Any]) -> Numbers:
    path = tmp_path / "numbers.json"
    path.write_text(json.dumps({"version": 1, "platforms": platforms}), "utf-8")
    return load_numbers(path, now=NOW)


# --- config ------------------------------------------------------------------


def test_the_slug_set_is_closed_and_every_slug_has_an_icon() -> None:
    assert PLATFORM_SLUGS == set(
        "twitch kick youtube tiktok instagram threads bluesky mastodon pixelfed "
        "discord matrix github patreon x linkedin rss other".split()
    )
    for slug, icon in PLATFORM_ICONS.items():
        assert icon in ICON_SLUGS, slug
    assert set(PLATFORM_ICONS) == PLATFORM_SLUGS - {"other"}


@pytest.mark.parametrize("slug", sorted(PLATFORM_SLUGS - {"other"}))
def test_each_named_platform_is_accepted(slug: str) -> None:
    cfg(block(item(platform=slug)))


def test_unknown_platform_is_refused() -> None:
    with pytest.raises(ValueError, match="platform"):
        cfg(block(item(platform="myspace")))


def test_other_needs_a_name_and_named_platforms_refuse_one() -> None:
    cfg(block(item(platform="other", name="Example Net")))
    with pytest.raises(ValueError, match="name"):
        cfg(block(item(platform="other")))
    with pytest.raises(ValueError, match="name"):
        cfg(block(item(platform="twitch", name="Twitch")))


@pytest.mark.parametrize(
    ("length", "ok"), [(0, False), (1, True), (80, True), (81, False)]
)
def test_handle_length_boundaries(length: int, ok: bool) -> None:
    if ok:
        cfg(block(item(handle="h" * length)))
    else:
        with pytest.raises(ValueError, match="handle"):
            cfg(block(item(handle="h" * length)))


@pytest.mark.parametrize(
    "bad",
    [
        "bell\x07",
        "nul\x00",
        "line\nbreak",
        "tab\there",
        "bidi‮evil",
        "isolate⁦x",
        "zero​width",
        "joiner‍x",
        "bom﻿x",
        "line sep x",
    ],
)
def test_hostile_handle_characters_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="handle"):
        cfg(block(item(handle=bad)))


def test_other_names_are_plain_and_bounded() -> None:
    cfg(block(item(platform="other", name="n" * 40)))
    with pytest.raises(ValueError, match="name"):
        cfg(block(item(platform="other", name="n" * 41)))
    with pytest.raises(ValueError, match="name"):
        cfg(block(item(platform="other", name="bad\x07")))


def test_title_and_note_are_plain_and_bounded() -> None:
    cfg(block(title="t" * 200, note="n" * 300))
    for key, limit in (("title", 200), ("note", 300)):
        with pytest.raises(ValueError, match=key):
            cfg(block(**{key: "x" * (limit + 1)}))
        with pytest.raises(ValueError, match=key):
            cfg(block(**{key: "bad\x07"}))
        with pytest.raises(ValueError, match=key):
            cfg(block(**{key: "bidi‮x"}))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/a",
        "javascript:alert(1)",
        "data:text/html,x",
        "//example.com/a",
        "https://user:pw@example.com/",
        "https://exa mple.com/",
        "https:///nohost",
        "TAB:home",
        "tab:",
        "tab:Home",
        "https://example.com/" + "a" * 2100,
        "",
    ],
)
def test_bad_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError, match="url"):
        cfg(block(item(url=url)))


def test_https_and_tab_urls_pass_and_unknown_tabs_are_named() -> None:
    cfg(block(item(url="https://example.com/a")))
    cfg(block(item(url="tab:home")), block(item(url="tab:home#top"), id="two"))
    with pytest.raises(ValueError, match="unknown tab 'nope'"):
        cfg(block(item(url="tab:nope")))


@pytest.mark.parametrize("label", ["followers", "subscribers", "members"])
def test_labels_are_a_closed_set(label: str) -> None:
    cfg(block(item(label=label, count=5)))


def test_unknown_label_is_refused() -> None:
    with pytest.raises(ValueError, match="label"):
        cfg(block(item(label="fans")))


@pytest.mark.parametrize("count", [0, 1, 10**10])
def test_counts_in_range_pass(count: int) -> None:
    cfg(block(item(count=count)))


@pytest.mark.parametrize("count", [-1, 10**10 + 1, 1.5, 3.0, "5", True, [5]])
def test_counts_must_be_strict_ints_in_range(count: Any) -> None:
    with pytest.raises(ValueError, match="count"):
        cfg(block(item(count=count)))


def test_as_of_needs_a_count_and_is_a_date_only() -> None:
    cfg(block(item(count=5, as_of="2027-03-01")))
    cfg(block(item(count=5, as_of=date(2027, 3, 1))))
    with pytest.raises(ValueError, match="as_of"):
        cfg(block(item(as_of="2027-03-01")))
    for bad in ("2027-03-01T00:00:00Z", "2027-13-40", "yesterday", 20270301, True):
        with pytest.raises(ValueError, match="as_of"):
            cfg(block(item(count=5, as_of=bad)))


@pytest.mark.parametrize(("n", "ok"), [(0, False), (1, True), (40, True), (41, False)])
def test_item_count_bounds(n: int, ok: bool) -> None:
    items = [item(handle=f"h{i}") for i in range(n)]
    if ok:
        cfg({"id": "socials", "items": items})
    else:
        with pytest.raises(ValueError, match="items"):
            cfg({"id": "socials", "items": items})


def test_layout_values() -> None:
    cfg(block(layout="list"))
    cfg(block(layout="chips"))
    with pytest.raises(ValueError, match="layout"):
        cfg(block(layout="grid"))


def test_unknown_keys_are_refused() -> None:
    with pytest.raises(ValueError, match="followers"):
        cfg(block(item(followers=5)))
    with pytest.raises(ValueError, match="color"):
        cfg(block(color="red"))


def test_items_key_picks_the_platforms_block() -> None:
    assert isinstance(cfg(block()).tabs[0].blocks[0], Platforms)


def test_errors_name_the_key(tmp_path: Path) -> None:
    path = tmp_path / "site.yaml"
    path.write_text(
        "version: 1\ncreator: {name: X}\ntabs:\n- id: t\n  title: T\n  blocks:\n"
        "  - id: s\n    items:\n"
        "    - {platform: twitch, handle: '', url: 'https://e.com/'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_site_config(path)
    assert "tabs[0].blocks[0].items[0].handle" in str(caught.value)


# --- ids ---------------------------------------------------------------------


def test_item_ids_derive_from_block_and_platform() -> None:
    config = cfg(
        block(item(), item(platform="kick", handle="k"), item(handle="second"))
    )
    first = config.tabs[0].blocks[0]
    assert isinstance(first, Platforms)
    assert first.item_ids == ["socials-twitch", "socials-kick", "socials-twitch-2"]


def test_item_ids_must_not_collide_with_other_ids() -> None:
    with pytest.raises(ValueError, match="duplicate id 'socials-twitch'"):
        cfg(block(), {"id": "socials-twitch", "title": "x", "url": "https://e.com/"})


def test_a_derived_id_longer_than_the_worker_allows_is_refused() -> None:
    cfg({"id": "a" * 30, "items": [item()]})
    with pytest.raises(ValueError, match="40"):
        cfg({"id": "a" * 36, "items": [item()]})


# --- abbreviation ------------------------------------------------------------


@pytest.mark.parametrize(
    ("n", "short"),
    [
        (0, "0"),
        (7, "7"),
        (999, "999"),
        (1000, "1k"),
        (1049, "1k"),
        (1050, "1.1k"),
        (3812, "3.8k"),
        (9999, "10k"),
        (10000, "10k"),
        (12345, "12.3k"),
        (406_800, "406.8k"),
        (999_949, "999.9k"),
        (999_950, "1M"),
        (999_999, "1M"),
        (1_000_000, "1M"),
        (1_240_000, "1.2M"),
        (12_345_678, "12.3M"),
        (999_999_999, "1B"),
        (1_500_000_000, "1.5B"),
        (10**10, "10B"),
    ],
)
def test_abbreviation_table(n: int, short: str) -> None:
    assert abbreviate(n) == short


# --- rendering ---------------------------------------------------------------


def test_list_rows_show_icon_name_handle_count_and_exact_text(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                item(count=3812, label="subscribers", as_of="2027-03-01"),
                item(
                    platform="other",
                    name="Example Net",
                    handle="me",
                    url="https://example.com/n",
                ),
                title="Find me",
                note="Counts are rounded.",
            )
        ),
        tmp_path,
    )
    assert "<h2" in html and "Find me" in html
    assert 'class="icon icon-twitch"' in html
    assert 'class="icon icon-link"' in html
    assert ">Example Net<" in html and ">@example<" in html
    assert re.search(
        r'<span class="platform-count" aria-hidden="true">3\.8k</span>', html
    )
    assert '<span class="sr-only">3,812 subscribers</span>' in html
    assert '<span class="platform-asof">as of 2027-03-01</span>' in html
    assert "platforms-list" in html and "platforms-chips" not in html
    assert "Counts are rounded." in html


def test_default_label_is_followers(tmp_path: Path) -> None:
    html = page(cfg(block(item(count=1000))), tmp_path)
    assert '<span class="sr-only">1,000 followers</span>' in html


def test_a_missing_count_renders_no_number_at_all(tmp_path: Path) -> None:
    html = page(cfg(block(item())), tmp_path)
    assert "platform-count" not in html
    assert "followers" not in html
    assert "as of" not in html
    assert ">0<" not in html


def test_a_zero_count_is_a_real_number(tmp_path: Path) -> None:
    html = page(cfg(block(item(count=0))), tmp_path)
    assert 'aria-hidden="true">0</span>' in html
    assert "0 followers" in html


def test_chips_layout_is_a_compact_strip_with_accessible_names(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                item(count=406_800),
                item(platform="kick", handle="k", url="https://example.com/k"),
                layout="chips",
            )
        ),
        tmp_path,
    )
    assert "platforms-chips" in html and "platforms-list" not in html
    assert ">406.8k<" in html
    assert "406,800 followers" in html
    assert re.search(r'class="sr-only">[^<]*Twitch[^<]*@example', html)
    assert 'class="platform-handle"' not in html


def test_the_whole_card_is_the_external_link(tmp_path: Path) -> None:
    html = page(cfg(block(item(count=5))), tmp_path)
    anchor = re.search(r'<a class="platform-link"[^>]*>', html)
    assert anchor is not None
    tag = anchor.group(0)
    assert 'href="https://example.com/twitch"' in tag
    assert 'rel="noopener noreferrer"' in tag
    assert 'ping="/e/c/socials-twitch"' in tag
    assert 'data-link-id="socials-twitch"' in tag
    inner = html.split(tag, 1)[1].split("</a>", 1)[0]
    assert "platform-name" in inner and "platform-count" in inner


def test_tracking_off_removes_ping_and_id(tmp_path: Path) -> None:
    html = page(cfg(block(), features={"click_tracking": False}), tmp_path)
    assert "ping=" not in html and "data-link-id" not in html
    assert 'rel="noopener noreferrer"' in html


def test_tab_urls_are_internal_untracked_and_not_in_the_manifest(
    tmp_path: Path,
) -> None:
    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "home",
                    "title": "Home",
                    "blocks": [block(item(url="tab:more"))],
                },
                {"id": "more", "title": "More", "blocks": []},
            ],
        }
    )
    out = tmp_path / "out"
    manifest = build_site(config, out, today=TODAY)
    html = (out / "index.html").read_text("utf-8")
    assert 'href="more/"' in html
    assert "ping=" not in html and "data-link-id" not in html
    assert "noopener" not in html.split("platform-link", 1)[1].split(">", 1)[0]
    assert "socials-twitch" not in manifest.links


def test_each_platform_click_is_in_the_manifest(tmp_path: Path) -> None:
    config = cfg(
        block(
            item(),
            item(platform="kick", handle="k", url="https://example.com/kick"),
            item(handle="again", url="https://example.com/twitch2"),
        )
    )
    manifest = build_site(config, tmp_path / "out", today=TODAY)
    assert manifest.links == {
        "socials-twitch": "https://example.com/twitch",
        "socials-kick": "https://example.com/kick",
        "socials-twitch-2": "https://example.com/twitch2",
    }
    assert "socials-kick" in manifest_json(manifest)


def test_hostile_strings_are_escaped(tmp_path: Path) -> None:
    html = page(
        cfg(
            block(
                item(platform="other", name=HOSTILE, handle=HOSTILE),
                item(handle="<b>x</b>", url='https://example.com/?a="><i>'),
                title=HOSTILE,
                note=HOSTILE,
            )
        ),
        tmp_path,
    )
    assert HOSTILE not in html
    assert "<b>x</b>" not in html
    assert "<i>" not in html
    assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") >= 4


def test_pages_stay_csp_safe(tmp_path: Path) -> None:
    html = page(cfg(block(item(count=5, as_of="2027-03-01"))), tmp_path)
    assert not re.search(r"<[^>]*\sstyle\s*=", html)
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", html)


# --- numbers overlay in the page ---------------------------------------------


def test_numbers_fill_a_missing_count_and_show_their_date(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path, {"twitch": {"count": 3812, "as_of": "2027-03-09T08:00:00Z"}}
    )
    html = page(cfg(block()), tmp_path, numbers)
    assert ">3.8k<" in html and "3,812 followers" in html
    assert "as of 2027-03-09" in html


def test_numbers_by_item_id_beat_numbers_by_slug(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path,
        {
            "twitch": {"count": 1000, "as_of": "2027-03-09T08:00:00Z"},
            "socials-twitch-2": {"count": 2000, "as_of": "2027-03-09T08:00:00Z"},
        },
    )
    html = page(cfg(block(item(), item(handle="second"))), tmp_path, numbers)
    assert "1,000 followers" in html and "2,000 followers" in html


def test_a_newer_numbers_value_wins_over_a_manual_one(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path, {"twitch": {"count": 5000, "as_of": "2027-03-09T08:00:00Z"}}
    )
    html = page(cfg(block(item(count=100, as_of="2027-03-01"))), tmp_path, numbers)
    assert "5,000 followers" in html and "100 followers" not in html


def test_an_older_numbers_value_loses_to_a_manual_one(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path, {"twitch": {"count": 5000, "as_of": "2027-02-01T08:00:00Z"}}
    )
    html = page(cfg(block(item(count=100, as_of="2027-03-01"))), tmp_path, numbers)
    assert "100 followers" in html and "5,000" not in html
    assert "as of 2027-03-01" in html


def test_a_manual_count_without_a_date_always_loses(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path, {"twitch": {"count": 5000, "as_of": "2020-01-01T00:00:00Z"}}
    )
    html = page(cfg(block(item(count=100))), tmp_path, numbers)
    assert "5,000 followers" in html


def test_numbers_for_other_platforms_leave_the_page_alone(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path, {"kick": {"count": 5000, "as_of": "2027-03-09T08:00:00Z"}}
    )
    assert page(cfg(block()), tmp_path, numbers) == page(
        cfg(block()), tmp_path / "again"
    )


def test_numbers_never_reach_the_published_site(tmp_path: Path) -> None:
    numbers = numbers_file(
        tmp_path, {"twitch": {"count": 424242, "as_of": "2027-03-09T00:00:00Z"}}
    )
    out = build(cfg(block()), tmp_path, numbers)
    assert "424,242" in (out / "index.html").read_text("utf-8")
    for path in out.rglob("*"):
        if path.is_file():
            assert b"424242" not in path.read_bytes(), path
    assert not list(out.rglob("numbers.json"))


# --- assets only where the block is -----------------------------------------


def test_pages_without_the_block_do_not_mention_the_stylesheet(tmp_path: Path) -> None:
    out = build(cfg({"id": "rule"}), tmp_path)
    assert "platforms.css" not in (out / "index.html").read_text("utf-8")
    assert not (out / "static" / "platforms.css").exists()


def test_only_pages_with_the_block_link_the_stylesheet(tmp_path: Path) -> None:
    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {"id": "home", "title": "Home", "blocks": [block()]},
                {"id": "other", "title": "Other", "blocks": [{"id": "rule"}]},
            ],
        }
    )
    out = build(config, tmp_path)
    assert (out / "static" / "platforms.css").is_file()
    assert CSS_TAG in (out / "index.html").read_text("utf-8")
    assert "platforms.css" not in (out / "other" / "index.html").read_text("utf-8")
    assert "platforms.css" not in (out / "privacy" / "index.html").read_text("utf-8")
    assert "copy.js" not in (out / "index.html").read_text("utf-8")


def test_a_block_on_a_later_tab_uses_relative_paths(tmp_path: Path) -> None:
    config = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {"id": "home", "title": "Home", "blocks": [{"id": "rule"}]},
                {"id": "socials-tab", "title": "Socials", "blocks": [block()]},
            ],
        }
    )
    out = build(config, tmp_path)
    assert '<link rel="stylesheet" href="../static/platforms.css">' in (
        out / "socials-tab" / "index.html"
    ).read_text("utf-8")


# --- the stylesheet ----------------------------------------------------------


def css_problems(css: str) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = []
    if re.findall(r"#[0-9a-fA-F]{3,8}\b", css):
        found.append("hex color")
    if re.findall(r"\b(?:rgb|rgba|hsl|hsla|oklch|lab|lch)\(", css):
        found.append("color function")
    return found


def test_css_reads_only_tokens_and_has_the_layout_rules() -> None:
    text = CSS.read_text("utf-8")
    assert text.count("{") > 8, "the guard collected too few rules"
    assert css_problems(text) == []
    assert "overflow-wrap: anywhere" in text
    assert "var(--ra-tap)" in text
    assert "min-width: 56rem" in text


def media_queries(css: str) -> list[str]:
    return [q.strip() for q in re.findall(r"@media\s*([^{]+)\{", css)]


def test_css_follows_the_responsive_conventions() -> None:
    css = CSS.read_text("utf-8")
    queries = media_queries(css)
    assert len(queries) >= 4, "the guard collected too few media queries"
    assert not [q for q in queries if "px" in q]
    for needed in (
        "(min-width: 56rem)",
        "(hover: hover)",
        "(pointer: coarse)",
        "print",
    ):
        assert needed in queries, needed
    outside = re.sub(r"@media \(hover: hover\) \{.*?\n\}", "", css, flags=re.S)
    assert ":hover" not in outside and ":hover" in css
    tracks = re.findall(r"minmax\(([^,]+),", css)
    assert tracks and all(t == "0" or t.startswith("min(") for t in tracks)


def test_responsive_guard_bites() -> None:
    assert [q for q in media_queries("@media (min-width: 768px) { a {} }") if "px" in q]
    assert ":hover" in re.sub(
        r"@media \(hover: hover\) \{.*?\n\}", "", "a:hover { x: y; }\n", flags=re.S
    )


def test_css_guard_bites() -> None:
    assert css_problems(".a { color: #fff; }") == ["hex color"]
    assert css_problems(".a { color: rgb(0 0 0); }") == ["color function"]
    assert css_problems("/* #fff */ .a { color: var(--ra-color-text); }") == []


# --- the example -------------------------------------------------------------


def test_the_example_builds_with_a_socials_home(tmp_path: Path) -> None:
    config = load_site_config(EXAMPLE)
    assert config.tabs[0].id == "socials"
    out = tmp_path / "out"
    manifest = build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
    html = (out / "index.html").read_text("utf-8")
    assert "platforms-chips" in html and "platforms-list" in html
    assert "static/platforms.css" in html
    assert len(manifest.links) >= 10
    assert (out / "static" / "platforms.css").is_file()
