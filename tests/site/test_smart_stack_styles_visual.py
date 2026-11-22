# SPDX-License-Identifier: Apache-2.0
"""The row and icon group styles in a real browser (#672).

The page is served from a local port only. Anything else is aborted and
recorded, and every test asserts none was seen. All data is synthetic.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from collections.abc import Iterator
from datetime import date
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright
from test_smart_stack_styles import raw

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual
TODAY = date(2027, 3, 10)
SIZES = [(1920, 930), (393, 852), (320, 640)]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        binary = os.environ.get("RA_CHROMIUM")
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=binary or None
        )
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data = raw(rows=("videos", "community"), icons=True)
    # A long subtitle proves the row wraps instead of overflowing at 320 px.
    for link in data["tabs"][1]["blocks"]:
        link["subtitle"] = "A long subtitle that has to wrap on a very narrow screen"
    out = tmp_path_factory.mktemp("stylesvis") / "out"
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


def open_page(
    browser: Browser, base: str, size: tuple[int, int]
) -> tuple[Page, list[str]]:
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        reduced_motion="reduce",
        color_scheme="dark",
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


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_targets_are_44px_and_nothing_overflows(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size)
    # The page's root scale is a rem, so a "44 px" rule is read as rendered.
    rows = page.locator(".smart-style-rows a.link")
    assert rows.count() == 4
    for i in range(rows.count()):
        box = rows.nth(i).bounding_box()
        assert box is not None and box["height"] >= 44, box
    icons = page.locator(".smart-icons a")
    assert icons.count() == 4
    for i in range(icons.count()):
        box = icons.nth(i).bounding_box()
        assert box is not None and box["width"] >= 44 and box["height"] >= 44, box
    wide = page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )
    assert wide is False
    assert foreign == []
    page.context.close()


def test_rows_are_one_column_and_icons_sit_on_one_line_when_they_fit(
    browser: Browser, base_url: str
) -> None:
    page, foreign = open_page(browser, base_url, SIZES[0])
    tops = {
        round(page.locator(".smart-style-rows a.link").nth(i).bounding_box()["x"])  # type: ignore[index]
        for i in range(4)
    }
    assert len(tops) == 1, "rows share one column"
    ys = {
        round(page.locator(".smart-icons a").nth(i).bounding_box()["y"])  # type: ignore[index]
        for i in range(4)
    }
    assert len(ys) == 1, "four icons fit on one line on a desktop"
    assert foreign == []
    page.context.close()


def test_keyboard_order_follows_the_page_and_focus_is_visible(
    browser: Browser, base_url: str
) -> None:
    page, foreign = open_page(browser, base_url, SIZES[1])
    seen: list[str] = []
    for _ in range(40):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "(() => { const e = document.activeElement;"
            " const s = getComputedStyle(e);"
            " return [e.getAttribute('href') || '', s.outlineStyle, s.outlineWidth,"
            " e.matches(':focus-visible')]; })()"
        )
        seen.append(info[0])
        if info[0].endswith("/blog/2"):
            break
        if info[0] in ("https://example.com/t", "https://example.com/k"):
            assert info[3] is True
            assert info[1] != "none" and info[2] != "0px", info
    follow = [
        h for h in seen if h in ("https://example.com/t", "https://example.com/@me")
    ]
    assert follow, seen
    pos = {h: i for i, h in enumerate(seen)}
    videos = pos["https://example.com/videos/1"]
    community = pos["https://example.com/community/1"]
    icon = pos["https://example.com/n"]
    assert videos < icon < community, seen
    assert foreign == []
    page.context.close()
