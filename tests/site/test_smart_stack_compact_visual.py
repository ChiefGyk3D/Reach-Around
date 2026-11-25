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
TILES = 26


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
