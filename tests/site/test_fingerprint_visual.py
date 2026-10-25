# SPDX-License-Identifier: Apache-2.0
"""Browser checks for hashed asset URLs. Run with ``pytest --run-visual``.

Run through ``$HOME/.local/share/reach-around-dev/quiet/safe-run.sh`` (headless,
no desktop session). The built examples are served from 127.0.0.1 only; any
request to another host is aborted and counted, and no link is ever clicked.
The checks: each page renders with its CSS (not unstyled) from hashed URLs,
fonts and icon masks resolve through the rewritten ``url()`` references, no
request fails, and no stable ``static/`` name is requested.
"""

from __future__ import annotations

import functools
import os
import re
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, Request, Route, sync_playwright

from reach_around.config import load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2027, 3, 10)
SITES = ("instance", "crypto", "email", "platforms")
HASHED = re.compile(r"\.[0-9a-f]{20}\.[A-Za-z0-9]+$")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def servers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    base = tmp_path_factory.mktemp("hashed")
    urls: dict[str, str] = {}
    running: list[tuple[ThreadingHTTPServer, threading.Thread]] = []
    for name in SITES:
        config = ROOT / "examples" / name / "site.yaml"
        out = base / name
        build_site(load_site_config(config), out, today=TODAY, assets_dir=config.parent)
        handler = functools.partial(_Quiet, directory=str(out))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        running.append((server, thread))
        urls[name] = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield urls
    finally:
        for server, thread in running:
            server.shutdown()
            server.server_close()
            thread.join()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        binary = os.environ.get("RA_CHROMIUM")
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=binary or None
        )
        yield instance
        instance.close()


class Watch:
    """What the page asked for and what came back."""

    def __init__(self, page: Page) -> None:
        self.statuses: dict[str, int] = {}
        self.failed: list[str] = []
        self.blocked: list[str] = []
        page.route("**/*", self._route)
        page.on("response", lambda r: self.statuses.__setitem__(r.url, r.status))
        page.on("requestfailed", self._failed)

    def _route(self, route: Route) -> None:
        if route.request.url.startswith("http://127.0.0.1:"):
            route.continue_()
        else:
            self.blocked.append(route.request.url)
            route.abort()

    def _failed(self, request: Request) -> None:
        if request.url not in self.blocked:
            self.failed.append(request.url)


def visit(browser: Browser, url: str, width: int) -> tuple[Page, Watch]:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    watch = Watch(page)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    return page, watch


def assert_styled(page: Page, watch: Watch, width: int) -> None:
    links = page.evaluate(
        "[...document.querySelectorAll('link[rel=stylesheet]')].map(l => l.href)"
    )
    assert links, "the page links no stylesheet"
    for href in links:
        assert HASHED.search(href), f"unhashed stylesheet {href}"
        assert watch.statuses.get(href) == 200, href
    sheets = page.evaluate("document.styleSheets.length")
    assert sheets == len(links)
    empty = page.evaluate(
        "[...document.styleSheets].filter(s => s.cssRules.length === 0).length"
    )
    assert empty <= 1  # theme.css is empty on an unthemed site
    # unstyled would be the browser default: 8px body margin, a serif face
    body = page.evaluate(
        "(() => { const s = getComputedStyle(document.body);"
        " return [s.marginTop, s.fontFamily, s.backgroundColor]; })()"
    )
    assert body[0] != "8px"
    assert "serif" not in body[1].replace("sans-serif", "").lower() or "Jost" in body[1]
    assert page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--ra-color-bg').trim()"
    )
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert watch.failed == []
    assert watch.blocked == []
    bad = {u: s for u, s in watch.statuses.items() if s >= 400}
    assert bad == {}
    for url in watch.statuses:
        if "/static/" in url:
            assert HASHED.search(url), f"unhashed static request {url}"


@pytest.mark.parametrize("width", [360, 1440])
@pytest.mark.parametrize("site", SITES)
def test_a_page_renders_with_its_css_from_hashed_urls(
    browser: Browser, servers: dict[str, str], site: str, width: int
) -> None:
    page, watch = visit(browser, servers[site] + "/", width)
    assert_styled(page, watch, width)
    page.context.close()


@pytest.mark.parametrize("width", [360, 1440])
@pytest.mark.parametrize("site", SITES)
def test_the_404_page_renders_with_its_css(
    browser: Browser, servers: dict[str, str], site: str, width: int
) -> None:
    page, watch = visit(browser, servers[site] + "/404.html", width)
    assert_styled(page, watch, width)
    assert "That page does not exist." in page.inner_text("body")
    page.context.close()


@pytest.mark.parametrize("width", [360, 1440])
def test_the_font_and_icon_masks_load_through_rewritten_urls(
    browser: Browser, servers: dict[str, str], width: int
) -> None:
    page, watch = visit(browser, servers["platforms"] + "/", width)
    faces = page.evaluate("document.fonts.load('16px Jost').then(f => f.length)")
    assert faces >= 1
    fonts = [u for u in watch.statuses if u.endswith(".woff2")]
    assert fonts
    for url in fonts:
        assert HASHED.search(url) and watch.statuses[url] == 200
    masks = page.evaluate(
        "[...document.querySelectorAll('[class*=icon-]')]"
        ".map(e => { const s = getComputedStyle(e);"
        " return s.maskImage || s.webkitMaskImage; })"
        ".filter(m => m && m !== 'none')"
    )
    assert masks, "no icon is drawn with a mask"
    for mask in masks:
        assert re.search(r"icons/[a-z]+\.[0-9a-f]{20}\.svg", mask), mask
    icons = [u for u in watch.statuses if "/static/icons/" in u]
    assert icons
    for url in icons:
        assert watch.statuses[url] == 200
    page.context.close()


@pytest.mark.parametrize("width", [360, 1440])
def test_the_mascot_and_avatar_load_from_hashed_media_urls(
    browser: Browser, servers: dict[str, str], width: int
) -> None:
    page, watch = visit(browser, servers["instance"] + "/", width)
    avatar = page.evaluate("document.querySelector('img.avatar').src")
    assert HASHED.search(avatar)
    assert watch.statuses[avatar] == 200
    assert page.evaluate("document.querySelector('img.avatar').naturalWidth") > 0
    page.context.close()
