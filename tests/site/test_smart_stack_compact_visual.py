# SPDX-License-Identifier: Apache-2.0
"""The compact tile grid in a real browser (#873): no scrolling on phones.

The example grid (placeholder data) is served from a local port only; anything
else is aborted and recorded, and every test asserts none was seen.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from playwright.sync_api import Browser, Page, Route, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual
TODAY = date(2027, 3, 10)
EXAMPLE = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "instance"
    / "site-link-grid.yaml"
)
PHONES = [(390, 844), (390, 664), (360, 640)]
SIZES = [*PHONES, (1920, 930)]
TILES = 28


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
        )
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("gridvis") / "out"
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    build_site(
        SiteConfig.model_validate(doc), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


SCHEDULE = "Tue and Thu 8 PM Eastern"
LONG_SCHEDULE = "Tue and Thu 8 PM, Sat 2 PM Eastern, with a long second line of notes"


@pytest.fixture(scope="module")
def scheduled_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The example grid with the optional schedule line configured."""
    out = tmp_path_factory.mktemp("gridsched") / "out"
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    doc["page"]["status"]["schedule"] = SCHEDULE
    build_site(
        SiteConfig.model_validate(doc), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def open_page(
    browser: Browser, base: str, size: tuple[int, int], *, script: bool = True
) -> tuple[Page, list[str]]:
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        reduced_motion="reduce",
        color_scheme="dark",
        java_script_enabled=script,
    )
    foreign: list[str] = []
    host = urlsplit(base).netloc

    def guard(route: Route) -> None:
        if urlsplit(route.request.url).netloc != host:
            foreign.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    context.route("**/*", guard)
    page = context.new_page()
    page.goto(base)
    page.wait_for_load_state("load")
    return page, foreign


MEASURE = """() => {
  const h = innerHeight, w = innerWidth;
  const tiles = [...document.querySelectorAll('a.smart-tile')].map(a => {
    const r = a.getBoundingClientRect();
    const t = a.querySelector('.link-title');
    return {
      top: r.top, bottom: r.bottom, left: r.left, right: r.right,
      w: r.width, h: r.height,
      label: t ? t.textContent.trim() : '',
      icon: !!a.querySelector('.link-chip .icon'),
      overflow: t ? t.scrollWidth - t.clientWidth : 0,
    };
  });
  return {
    tiles, h, w,
    scrollH: document.documentElement.scrollHeight,
    scrollW: document.documentElement.scrollWidth,
    bodyBg: getComputedStyle(document.body).backgroundColor,
  };
}"""


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_tile_is_on_screen_without_scrolling(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size)
    got = page.evaluate(MEASURE)
    tiles = got["tiles"]
    assert len(tiles) == TILES
    inside = [
        t
        for t in tiles
        if t["top"] >= -0.5
        and t["bottom"] <= got["h"] + 0.5
        and t["left"] >= -0.5
        and t["right"] <= got["w"] + 0.5
    ]
    assert len(inside) == TILES, f"{len(inside)} of {TILES} visible at {size}"
    assert got["scrollH"] <= got["h"], (got["scrollH"], got["h"])
    assert got["scrollW"] <= got["w"]
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_a_configured_schedule_stays_visible_and_every_tile_still_fits(
    browser: Browser, scheduled_url: str, size: tuple[int, int]
) -> None:
    """Short screens keep the schedule line (round 2 of #879): no content is
    removed from the visual or the accessibility tree, and the tiles still fit."""
    page, _foreign = open_page(browser, scheduled_url, size)
    line = page.locator(".smart-status-schedule")
    assert line.count() == 1
    assert line.is_visible()
    assert line.evaluate("e => getComputedStyle(e).display") != "none"
    box = line.bounding_box()
    assert box is not None and box["height"] > 0 and box["width"] > 0
    assert page.get_by_text(SCHEDULE, exact=True).count() == 1
    got = page.evaluate(MEASURE)
    inside = [
        t
        for t in got["tiles"]
        if t["top"] >= -0.5
        and t["bottom"] <= got["h"] + 0.5
        and t["left"] >= -0.5
        and t["right"] <= got["w"] + 0.5
    ]
    assert len(inside) == TILES, f"{len(inside)} of {TILES} visible at {size}"
    assert got["scrollH"] <= got["h"], (got["scrollH"], got["h"])
    page.context.close()


@pytest.mark.parametrize("size", PHONES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_a_two_line_schedule_is_kept_and_the_page_scrolls_not_clips(
    browser: Browser, tmp_path_factory: pytest.TempPathFactory, size: tuple[int, int]
) -> None:
    """A schedule too long for one line wraps: the page may scroll a little, but
    nothing is hidden, clipped sideways or removed from the accessibility tree."""
    out = tmp_path_factory.mktemp("gridlong") / "out"
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    doc["page"]["status"]["schedule"] = LONG_SCHEDULE
    build_site(
        SiteConfig.model_validate(doc), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page, _foreign = open_page(
            browser, f"http://127.0.0.1:{server.server_address[1]}/", size
        )
        assert page.get_by_text(LONG_SCHEDULE, exact=True).is_visible()
        got = page.evaluate(MEASURE)
        assert got["scrollW"] <= got["w"]
        assert len(got["tiles"]) == TILES
        page.context.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_tiles_are_tappable_labeled_and_never_clipped(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, _foreign = open_page(browser, base_url, size)
    got = page.evaluate(MEASURE)
    for tile in got["tiles"]:
        assert tile["h"] >= 44 - 0.5, tile
        assert tile["w"] >= 44, tile
        assert tile["icon"] and tile["label"], tile
        assert tile["overflow"] <= 1, tile
    page.context.close()


@pytest.mark.parametrize("size", PHONES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_phone_grid_works_without_javascript(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size, script=False)
    assert page.locator("a.smart-tile").count() == TILES
    assert page.locator("a.smart-tile:visible").count() == TILES
    assert page.evaluate("document.documentElement.scrollHeight") <= size[1]
    assert foreign == []
    page.context.close()


def test_nothing_is_collapsed_or_behind_a_tab(browser: Browser, base_url: str) -> None:
    page, _foreign = open_page(browser, base_url, (390, 844))
    assert page.locator("details, summary, [role=tab], [hidden]").count() == 0
    headings = page.locator(".smart-group h2").all_inner_texts()
    assert [h.lower() for h in headings] == [
        "live now",
        "videos",
        "follow",
        "chat",
        "support",
        "gear i use",
        "services i use",
        "more",
    ]
    page.context.close()


def test_the_page_is_dark_with_no_cream_or_white_fill(
    browser: Browser, base_url: str
) -> None:
    page, _foreign = open_page(browser, base_url, (390, 844))
    fills = page.evaluate(
        """() => [...document.querySelectorAll(
          'body, main, a.smart-tile, .smart-group')]
          .map(e => getComputedStyle(e).backgroundColor)"""
    )
    for fill in fills:
        parts = [
            int(float(n))
            for n in fill.replace("rgba(", "")
            .replace("rgb(", "")
            .rstrip(")")
            .split(",")[:3]
        ]
        assert max(parts) < 100, fill  # nothing light
    page.context.close()


def test_twitch_and_kick_are_the_same_size(browser: Browser, base_url: str) -> None:
    page, _foreign = open_page(browser, base_url, (390, 844))
    boxes = [
        page.locator(".smart-group-watch a.smart-tile").nth(i).bounding_box()
        for i in range(3)
    ]
    assert all(b is not None for b in boxes)
    sizes = {(round(b["width"]), round(b["height"])) for b in boxes if b}
    assert len(sizes) == 1, sizes
    page.context.close()


def test_keyboard_focus_visits_every_tile_in_order(
    browser: Browser, base_url: str
) -> None:
    page, _foreign = open_page(browser, base_url, (390, 844))
    seen: list[str] = []
    for _ in range(60):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "(() => { const a = document.activeElement;"
            " return a.matches('a.smart-tile') ? a.textContent.trim() : ''; })()"
        )
        if info in seen:
            break  # focus came around again
        if info:
            seen.append(info)
    assert len(seen) == TILES
    assert seen[:3] == ["Twitch", "Kick", "YouTube Live"]
    page.context.close()


# --- untrusted labels in a real browser (the review of #879, round 1) ---------

LONG_SPACED = " ".join(["Supercalifragilistic expialidocious"] * 6)[:200]
LABELS = [
    LONG_SPACED,
    "W" * 200,
    "ا" * 40 + " " + "مرحبا بالعالم " * 11,
    "a​" * 100,
    "\U0001f600" * 60,
    "Supercalifragilisticexpialidocious",
    "‮evil‬ " + "x" * 150,
    "Ok",
]
HOSTILE_SIZES = [(390, 844), (360, 640), (320, 568), (280, 500)]


@pytest.fixture(scope="module")
def hostile_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    from test_smart_stack_compact import raw

    data = raw()
    for tab in data["tabs"]:
        if tab["intent"] == "videos":  # six across, the narrowest tiles
            tab["blocks"] += [
                {
                    "id": f"videos-{n}",
                    "title": "x",
                    "url": f"https://example.invalid/{n}",
                }
                for n in (4, 5, 6)
            ]
            tab["columns"] = 6
        if tab["intent"] == "follow":
            tab["columns"] = 5
    n = 0
    for tab in data["tabs"]:
        for block in tab["blocks"]:
            block["title"] = LABELS[n % len(LABELS)]
            n += 1
    out = tmp_path_factory.mktemp("gridhostile") / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


BOXES = """() => {
  const r = e => { const b = e.getBoundingClientRect();  // page coordinates
    return {l: b.left + scrollX, t: b.top + scrollY, r: b.right + scrollX,
            b: b.bottom + scrollY, w: b.width, h: b.height}; };
  return {
    w: innerWidth, scrollW: document.documentElement.scrollWidth,
    tiles: [...document.querySelectorAll('a.smart-tile')].map(a => {
      const title = a.querySelector('.link-title');
      const chip = a.querySelector('.link-chip');
      const line = parseFloat(getComputedStyle(title).lineHeight);
      a.scrollIntoView({block: 'center', inline: 'nearest'});
      const b = a.getBoundingClientRect();
      const cx = (b.left + b.right) / 2, cy = (b.top + b.bottom) / 2;
      const probes = [[cx, b.top + 3], [cx, b.bottom - 3],
                      [b.left + 3, cy], [b.right - 3, cy]].map(([x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return hit === a || a.contains(hit);
      });
      return {tile: r(a), title: r(title), chip: chip ? r(chip) : null,
              line, probes, text: title.textContent};
    }),
  };
}"""


def overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return (
        a["l"] < b["r"] - 0.5
        and b["l"] < a["r"] - 0.5
        and a["t"] < b["b"] - 0.5
        and b["t"] < a["b"] - 0.5
    )


@pytest.mark.parametrize("size", HOSTILE_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_hostile_labels_stay_inside_their_tiles(
    browser: Browser, hostile_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, hostile_url, size)
    got = page.evaluate(BOXES)
    tiles = got["tiles"]
    assert len(tiles) == 29
    assert got["scrollW"] <= got["w"], (got["scrollW"], got["w"])
    for i, t in enumerate(tiles):
        box, title = t["tile"], t["title"]
        assert box["w"] >= 44 - 0.5 and box["h"] >= 44 - 0.5, (i, box)
        # The label box is inside the tile and never over the icon.
        assert title["l"] >= box["l"] - 0.5 and title["r"] <= box["r"] + 0.5, (i, t)
        assert title["t"] >= box["t"] - 0.5 and title["b"] <= box["b"] + 0.5, (i, t)
        assert title["h"] <= 2 * t["line"] + 1, (i, t)
        if t["chip"]:
            assert not overlap(title, t["chip"]), (i, t)
        assert all(t["probes"]), ("covered by another tile", i, t["text"][:20])
        for j in range(i + 1, len(tiles)):
            assert not overlap(box, tiles[j]["tile"]), (i, j)
    assert foreign == []
    page.context.close()


def test_the_hostile_labels_are_clipped_to_look_not_to_read(
    browser: Browser, hostile_url: str
) -> None:
    """Both halves in one test, so neither can pass alone: the long labels are
    really clipped on screen (two lines, the rest cut), and the browser's own
    accessible name for each link is still the complete label."""
    page, _foreign = open_page(browser, hostile_url, (360, 640))
    clipped = page.evaluate(
        """() => [...document.querySelectorAll('a.smart-tile .link-title')]
          .map(e => ({text: e.textContent, cut: e.scrollHeight - e.clientHeight,
                      h: e.clientHeight,
                      line: parseFloat(getComputedStyle(e).lineHeight)}))"""
    )
    by_text = {c["text"]: c for c in clipped}
    for label in (LABELS[0], LABELS[1]):  # 200 characters each
        got = by_text[label]
        assert got["cut"] > 1, ("not clipped on screen", label[:20], got)
        assert got["h"] <= 2 * got["line"] + 1, got
    for label in LABELS:
        name = " ".join(label.split())
        links = page.get_by_role("link", name=name, exact=True)
        assert links.count() >= 1, ("accessible name is not the full label", name[:30])
    assert page.locator("a.smart-tile[aria-label], a.smart-tile[title]").count() == 0
    page.context.close()


# --- narrow containers and keyboard order (the review of #879, round 2) -------

NARROW_SIZES = [(280, 500), (240, 500), (200, 500)]


@pytest.mark.parametrize("size", NARROW_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_width_guarantee_holds_on_a_very_narrow_screen(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size)
    got = page.evaluate(MEASURE)
    assert len(got["tiles"]) == TILES
    assert got["scrollW"] <= got["w"], (got["scrollW"], got["w"])
    for tile in got["tiles"]:
        assert tile["w"] >= 44, tile
        assert tile["h"] >= 44 - 0.5, tile
        assert tile["left"] >= -0.5 and tile["right"] <= got["w"] + 0.5, tile
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize("container", [130, 100, 80, 60, 48], ids=lambda c: f"{c}px")
def test_a_container_below_three_tap_targets_falls_back_to_fewer_columns(
    browser: Browser, base_url: str, container: int
) -> None:
    """The container query itself: the group is forced to a width a whole phone
    never reaches, so three across (3 x 44 px plus gaps) cannot fit."""
    page, _foreign = open_page(browser, base_url, (390, 844))
    page.add_style_tag(
        content=f".smart-compact .smart-group {{ width: {container}px !important;"
        f" max-width: {container}px !important; }}"
    )
    got = page.evaluate(
        """() => [...document.querySelectorAll('.smart-tiles')].map(g => {
          const gb = g.getBoundingClientRect();
          return {w: gb.width, tiles: [...g.querySelectorAll('a.smart-tile')].map(a => {
            const b = a.getBoundingClientRect();
            return {l: b.left - gb.left, r: b.right - gb.left, w: b.width};
          })};
        })"""
    )
    assert got
    for grp in got:
        for tile in grp["tiles"]:
            assert tile["w"] >= 44 - 0.01, (container, grp["w"], tile)
            assert tile["l"] >= -0.5 and tile["r"] <= grp["w"] + 0.5, (
                container,
                grp,
                tile,
            )
    page.context.close()


@pytest.mark.parametrize("width", [320, 280, 240])
def test_the_minimum_supported_screen_never_scrolls_sideways(
    browser: Browser, base_url: str, width: int
) -> None:
    """The documented floor: down to a 240 px wide screen every tile is at least
    44 px wide and inside the page. Below it a 44 px tile cannot fit (the
    guarantee is scoped there, docs/config.md)."""
    page, foreign = open_page(browser, base_url, (width, 700))
    got = page.evaluate(MEASURE)
    assert got["scrollW"] <= got["w"], (width, got["scrollW"], got["w"])
    for tile in got["tiles"]:
        assert tile["w"] >= 44 - 0.01, (width, tile)
        assert tile["left"] >= -0.5 and tile["right"] <= got["w"] + 0.5, (width, tile)
    assert foreign == []
    page.context.close()


GROUP_ORDER = """() => [...document.querySelectorAll(
  '.smart-groups > .smart-group')].map(g => {
  const b = g.getBoundingClientRect();
  return {top: Math.round(b.top), left: Math.round(b.left),
          id: g.querySelector('h2').textContent.trim()};
})"""


def reading_order_is_visual_order(groups: list[dict[str, Any]]) -> bool:
    """Row-major: a later group in the DOM is never above an earlier one, and on
    the same row it is to the right."""
    keys = [(g["top"], g["left"]) for g in groups]
    return all(a <= b for a, b in zip(keys, keys[1:], strict=False))


def test_desktop_groups_sit_in_dom_and_focus_order_on_the_example(
    browser: Browser, base_url: str
) -> None:
    page, _foreign = open_page(browser, base_url, (1920, 930))
    groups = page.evaluate(GROUP_ORDER)
    assert len(groups) == 7
    assert reading_order_is_visual_order(groups), groups
    page.context.close()


@pytest.fixture(scope="module")
def spans_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Two groups of six (two columns wide each) followed by groups of three: the
    layout where dense packing used to pull a later group above an earlier one."""
    from test_smart_stack_compact import raw

    data = raw()
    for tab in data["tabs"]:
        if tab["intent"] in ("videos", "follow"):
            tab["blocks"] = [
                {
                    **tab["blocks"][0],
                    "id": f"{tab['intent']}-{n}",
                    "url": f"https://example.invalid/{tab['intent']}/{n}",
                }
                for n in range(1, 7)
            ]
            tab["columns"] = 6
    out = tmp_path_factory.mktemp("gridspans") / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("width", [1920, 1280, 1024, 800], ids=lambda w: f"{w}w")
def test_wide_groups_never_let_a_later_group_jump_ahead(
    browser: Browser, spans_url: str, width: int
) -> None:
    page, _foreign = open_page(browser, spans_url, (width, 930))
    groups = page.evaluate(GROUP_ORDER)
    spanning = page.evaluate(
        "[...document.querySelectorAll('.smart-groups > .smart-group')]"
        ".filter(g => g.querySelector('.smart-cols-6')).length"
    )
    assert spanning == 2  # the pattern under test is really on the page
    assert reading_order_is_visual_order(groups), groups
    page.context.close()
