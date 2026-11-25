# SPDX-License-Identifier: Apache-2.0
"""Support and Contact screens in a real browser (#873): no scrolling, 44 px taps.

The example (placeholder data) is served from a local port only; anything else is
aborted and recorded, and every test asserts none was seen.
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
    / "site-link-screens.yaml"
)
SIZES = [(390, 844), (390, 664), (360, 640), (1878, 930)]
# page path -> tiles on it (not counting the tab bar)
TILES = {"": 21, "support/": 14, "contact/": 6}
MEASURE = """() => {
  const box = (a) => { const r = a.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
            w: r.width, h: r.height}; };
  return {
    h: innerHeight, w: innerWidth,
    scrollH: document.documentElement.scrollHeight,
    scrollW: document.documentElement.scrollWidth,
    tiles: [...document.querySelectorAll('a.smart-tile')].map(box),
    tabs: [...document.querySelectorAll('a.smart-screen-tab')].map(box),
    fills: [...document.querySelectorAll(
      'body, main, a.smart-tile, a.smart-screen-tab, .smart-group')]
      .map(e => getComputedStyle(e).backgroundColor),
  };
}"""


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
    out = tmp_path_factory.mktemp("screensvis") / "out"
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
    browser: Browser, url: str, size: tuple[int, int], *, script: bool = True
) -> tuple[Page, list[str]]:
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        reduced_motion="reduce",
        color_scheme="dark",
        java_script_enabled=script,
    )
    foreign: list[str] = []
    host = urlsplit(url).netloc

    def guard(route: Route) -> None:
        if urlsplit(route.request.url).netloc != host:
            foreign.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    context.route("**/*", guard)
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state("load")
    return page, foreign


@pytest.mark.parametrize("path", sorted(TILES), ids=lambda p: p or "home")
@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_screen_fits_without_scrolling_either_way(
    browser: Browser, base_url: str, path: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url + path, size)
    got = page.evaluate(MEASURE)
    assert len(got["tiles"]) == TILES[path]
    assert len(got["tabs"]) == 3
    for box in [*got["tiles"], *got["tabs"]]:
        assert box["top"] >= -0.5 and box["bottom"] <= got["h"] + 0.5, box
        assert box["left"] >= -0.5 and box["right"] <= got["w"] + 0.5, box
    assert got["scrollH"] <= got["h"], (got["scrollH"], got["h"])
    assert got["scrollW"] <= got["w"], (got["scrollW"], got["w"])
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize("path", sorted(TILES), ids=lambda p: p or "home")
@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_tile_and_tab_meets_the_44_px_tap_rule(
    browser: Browser, base_url: str, path: str, size: tuple[int, int]
) -> None:
    page, _foreign = open_page(browser, base_url + path, size)
    got = page.evaluate(MEASURE)
    for box in [*got["tiles"], *got["tabs"]]:
        assert box["h"] >= 44 - 0.5 and box["w"] >= 44 - 0.5, box
    page.context.close()


@pytest.mark.parametrize("path", sorted(TILES), ids=lambda p: p or "home")
def test_the_screens_are_dark_with_no_cream_or_white_fill(
    browser: Browser, base_url: str, path: str
) -> None:
    page, _foreign = open_page(browser, base_url + path, (390, 844))
    fills = page.evaluate(MEASURE)["fills"]
    for fill in fills:
        parts = [
            int(float(n))
            for n in fill.replace("rgba(", "")
            .replace("rgb(", "")
            .rstrip(")")
            .split(",")[:3]
        ]
        if fill.startswith("rgba") and fill.rstrip(")").endswith(", 0"):
            continue  # fully transparent
        # Amber (the current tab and the hire button) is the one bright fill.
        assert max(parts) < 100 or parts[0] > 200 and parts[2] < 100, fill
    page.context.close()


def test_the_current_tab_and_the_hire_button_are_amber_with_dark_text(
    browser: Browser, base_url: str
) -> None:
    page, _foreign = open_page(browser, base_url + "contact/", (390, 844))
    current = page.locator("a.smart-screen-tab[aria-current=page]")
    hire = page.locator(".smart-hire a.smart-tile")
    for link in (current, hire):
        fill, text = link.evaluate(
            "e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).color]"
        )
        assert fill == "rgb(251, 176, 59)", fill
        assert text == "rgb(26, 26, 26)", text
    page.context.close()


def test_the_hire_block_is_a_separate_panel_apart_from_the_social_tiles(
    browser: Browser, base_url: str
) -> None:
    page, _foreign = open_page(browser, base_url + "contact/", (390, 844))
    panel = page.locator(".smart-hire").bounding_box()
    say = page.locator(".smart-group-contact").bounding_box()
    assert panel is not None and say is not None
    assert panel["y"] >= say["y"] + say["height"] - 0.5  # below, not mixed in
    width = page.locator(".smart-hire").evaluate(
        "e => getComputedStyle(e).borderLeftWidth"
    )
    assert width == "8px"
    page.context.close()


@pytest.mark.parametrize("path", sorted(TILES), ids=lambda p: p or "home")
def test_the_screens_work_without_javascript_and_keep_keyboard_order(
    browser: Browser, base_url: str, path: str
) -> None:
    page, foreign = open_page(browser, base_url + path, (390, 844), script=False)
    assert page.locator("a.smart-tile:visible").count() == TILES[path]
    seen: list[str] = []
    for _ in range(40):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "(() => { const a = document.activeElement;"
            " return a.matches('a.smart-screen-tab, a.smart-tile')"
            " ? a.textContent.trim() : ''; })()"
        )
        if info in seen:
            break
        if info:
            seen.append(info)
    assert seen[:3] == ["Links", "Support", "Contact"]  # the tab bar comes first
    assert len(seen) == TILES[path] + 3
    assert foreign == []
    page.context.close()


def test_the_free_group_tiles_twitch_and_kick_are_the_same_size(
    browser: Browser, base_url: str
) -> None:
    page, _foreign = open_page(browser, base_url + "support/", (390, 844))
    boxes = [
        page.locator(f'a[data-link-id="free-{name}"]').bounding_box()
        for name in ("twitch", "kick")
    ]
    assert all(b is not None for b in boxes)
    sizes = {(round(b["width"]), round(b["height"])) for b in boxes if b}
    assert len(sizes) == 1, sizes
    page.context.close()


LABELS = [
    " ".join(["Supercalifragilistic expialidocious"] * 6)[:60],
    "W" * 60,
]


@pytest.mark.parametrize("label", LABELS)
@pytest.mark.parametrize("path", ["support/", "contact/"])
def test_a_long_label_stays_inside_its_tile_on_a_screen(
    tmp_path_factory: pytest.TempPathFactory,
    browser: Browser,
    path: str,
    label: str,
) -> None:
    out = tmp_path_factory.mktemp("screenslong") / "out"
    doc = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    for tab in doc["tabs"]:
        if tab.get("screen"):
            tab["title"] = label
            for link in tab["blocks"]:
                link["title"] = label
    build_site(
        SiteConfig.model_validate(doc), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/{path}"
        for size in [(390, 844), (360, 640)]:
            page, _foreign = open_page(browser, url, size)
            got = page.evaluate(MEASURE)
            assert got["scrollW"] <= got["w"], (path, size, got["scrollW"])
            inside = page.evaluate(
                """() => [...document.querySelectorAll('a.smart-tile')].every(a => {
                  const t = a.querySelector('.link-title');
                  const r = a.getBoundingClientRect(), s = t.getBoundingClientRect();
                  return s.left >= r.left - 0.5 && s.right <= r.right + 0.5
                    && s.top >= r.top - 0.5 && s.bottom <= r.bottom + 0.5;
                })"""
            )
            assert inside, (path, size)
            page.context.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
