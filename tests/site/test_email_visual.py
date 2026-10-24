# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the email block. Run with ``pytest --run-visual``.

Run them through the quiet wrapper so no desktop session or mail client can be
reached (``RA_CHROMIUM`` then points at a headless launcher). Two rules hold for
this file and the guard in ``test_browser_tests_never_navigate.py`` enforces them:

* every page gets ``window.__raOpen`` from an init script, so the script's mail
  navigation lands in a list instead of leaving the page;
* nothing here clicks the revealed mail link element. The "Write email" button
  is the thing under test.

All addresses are synthetic (example.test).
"""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Callable, Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

TODAY = date(2027, 3, 10)
ADDR = "name@example.test"
SEAM = (
    "window.__raCalls = [];"
    " window.__raOpen = function (u) { window.__raCalls.push(u); };"
)
SHOTS = os.environ.get("RA_SHOTS")

Serve = Callable[[str], str]


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _config(**over: Any) -> SiteConfig:
    block: dict[str, Any] = {
        "id": "mail",
        "title": "Email me",
        "address": ADDR,
        "note": "Business and press only. I read everything and answer within a week.",
    }
    block.update(over)
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator", "header": "centered"},
            "tabs": [{"id": "reach", "title": "Contact", "blocks": [block]}],
        }
    )


@pytest.fixture(scope="module")
def serve(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Serve]:
    servers: list[ThreadingHTTPServer] = []

    def start(mode: str) -> str:
        out = tmp_path_factory.mktemp(mode) / "out"
        build_site(_config(reveal=mode), out, today=TODAY)
        handler = functools.partial(_Quiet, directory=str(out))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}/"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        binary = os.environ.get("RA_CHROMIUM")
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=binary or None
        )
        yield instance
        instance.close()


def _context(browser: Browser, width: int, *, script: bool = True) -> BrowserContext:
    context = browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion="reduce",
        color_scheme="dark",
        java_script_enabled=script,
    )
    # Defined before any page script runs, on every page of this context.
    context.add_init_script(SEAM)
    return context


class Watch:
    """Records every main-frame navigation so a test can prove none happened."""

    def __init__(self, page: Page) -> None:
        self.urls: list[str] = []
        page.on(
            "framenavigated",
            lambda frame: (
                self.urls.append(frame.url) if frame.parent_frame is None else None
            ),
        )


def _open(browser: Browser, url: str, width: int, *, script: bool = True) -> Page:
    page = _context(browser, width, script=script).new_page()
    page.goto(url)
    return page


def _calls(page: Page) -> list[str]:
    result = page.evaluate("window.__raCalls")
    assert isinstance(result, list)
    return [str(item) for item in result]


def _shot(page: Page, name: str) -> None:
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


# --- behavior ---------------------------------------------------------------------


def test_without_script_the_spelled_address_shows_and_nothing_else(
    browser: Browser, serve: Serve
) -> None:
    page = _open(browser, serve("click"), 390, script=False)
    assert page.locator(".email-spelled").inner_text() == "name at example dot test"
    assert not page.locator(".email-actions button").is_visible()
    assert (
        page.locator("a[href]").filter(has=page.locator(".email-spelled")).count() == 0
    )
    mode = page.evaluate(
        "getComputedStyle(document.querySelector('.email-spelled')).userSelect"
    )
    assert mode != "none"
    assert ADDR not in page.content()
    page.context.close()


def test_with_script_the_page_has_a_show_button_and_still_no_address(
    browser: Browser, serve: Serve
) -> None:
    page = _open(browser, serve("click"), 390)
    assert page.evaluate("document.documentElement.classList.contains('email-ready')")
    assert page.get_by_role("button", name="Show email").is_visible()
    html = page.content()
    assert ADDR not in html
    assert "mailto" not in html.lower()
    assert page.locator(".email-address").count() == 0
    page.context.close()


def test_click_reveals_selectable_text_and_creates_the_link_only_then(
    browser: Browser, serve: Serve
) -> None:
    page = _open(browser, serve("click"), 390)
    watch = Watch(page)
    page.get_by_role("button", name="Show email").click()
    link = page.locator(".email-address")
    assert link.is_visible()
    assert link.inner_text() == ADDR
    assert link.get_attribute("href") == f"mailto:{ADDR}"
    assert not page.locator(".email-spelled").is_visible()
    # selectable: the user can select the text and read the selection back
    link.evaluate(
        "n => { const r = document.createRange(); r.selectNodeContents(n);"
        " const s = window.getSelection(); s.removeAllRanges(); s.addRange(r); }"
    )
    assert page.evaluate("window.getSelection().toString()") == ADDR
    assert _calls(page) == []  # revealing never opens the mail program
    assert watch.urls == []
    page.context.close()


def test_write_email_goes_through_the_seam_with_exactly_the_mail_url(
    browser: Browser, serve: Serve
) -> None:
    page = _open(browser, serve("click"), 390)
    watch = Watch(page)
    before = page.url
    page.get_by_role("button", name="Show email").click()
    page.get_by_role("button", name="Write email").click()
    assert _calls(page) == [f"mailto:{ADDR}"]
    assert page.url == before
    assert watch.urls == [], "the page must not have navigated"
    page.context.close()


def test_copy_writes_the_address_announces_and_resets(
    browser: Browser, serve: Serve
) -> None:
    url = serve("click")
    context = _context(browser, 390)
    context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=url.rstrip("/")
    )
    page = context.new_page()
    page.goto(url)
    watch = Watch(page)
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    page.get_by_role("button", name="Show email").click()
    button = page.locator("button.email-copy")
    button.click()
    page.wait_for_function(
        "document.querySelector('[data-email-status]').textContent === 'Copied'"
    )
    assert button.inner_text() == "Copied"
    assert page.evaluate("navigator.clipboard.readText()") == ADDR
    page.wait_for_function(
        "document.querySelector('[data-email-status]').textContent === ''",
        timeout=4000,
    )
    assert button.inner_text() == "Copy"
    assert _calls(page) == []
    assert watch.urls == []
    assert requests == [], "copying must not make a network request"
    context.close()


def test_without_clipboard_access_copy_selects_the_address(
    browser: Browser, serve: Serve
) -> None:
    context = _context(browser, 390)
    context.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: undefined});"
    )
    page = context.new_page()
    page.goto(serve("click"))
    page.get_by_role("button", name="Show email").click()
    page.get_by_role("button", name="Copy").click()
    assert page.evaluate("window.getSelection().toString()") == ADDR
    assert "selected" in page.locator("[data-email-status]").inner_text().lower()
    assert _calls(page) == []
    context.close()


def test_always_reveals_at_load_without_a_click(browser: Browser, serve: Serve) -> None:
    page = _open(browser, serve("always"), 390)
    assert page.locator(".email-address").inner_text() == ADDR
    assert not page.locator(".email-spelled").is_visible()
    assert _calls(page) == []
    page.context.close()


def test_never_js_shows_only_the_spelled_text_even_with_script(
    browser: Browser, serve: Serve
) -> None:
    page = _open(browser, serve("never_js"), 390)
    assert page.locator(".email-spelled").inner_text() == "name at example dot test"
    assert page.get_by_role("button").count() == 0
    assert page.locator(".email-address").count() == 0
    assert ADDR not in page.content()
    page.context.close()


def test_a_damaged_attribute_reveals_nothing(browser: Browser, serve: Serve) -> None:
    context = _context(browser, 390)
    page = context.new_page()
    page.goto(serve("click"))
    page.evaluate(
        "document.querySelector('.email-card')"
        ".setAttribute('data-email', '!!!not-base64')"
    )
    page.get_by_role("button", name="Show email").click()
    assert page.locator(".email-address").count() == 0
    assert _calls(page) == []
    context.close()


# --- layout at the two reference widths ----------------------------------------------


@pytest.mark.parametrize("width", [360, 1440])
@pytest.mark.parametrize("mode", ["click", "always"])
def test_layout_fits_and_buttons_are_tappable(
    browser: Browser, serve: Serve, width: int, mode: str
) -> None:
    page = _open(browser, serve(mode), width)
    if mode == "click":
        _shot(page, f"email-closed-{width}")
        show = page.get_by_role("button", name="Show email").bounding_box()
        assert show is not None
        assert show["height"] >= 44 and show["width"] >= 44
        assert show["x"] >= 0 and show["x"] + show["width"] <= width
        page.get_by_role("button", name="Show email").click()
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    for name in ("Write email", "Copy"):
        box = page.get_by_role("button", name=name).bounding_box()
        assert box is not None
        assert box["height"] >= 44 and box["width"] >= 44
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
    card = page.locator(".email-card").bounding_box()
    assert card is not None
    assert card["x"] >= 0 and card["x"] + card["width"] <= width
    _shot(page, f"email-{mode}-open-{width}")
    page.context.close()


@pytest.mark.parametrize("width", [360, 1440])
def test_a_long_address_wraps_inside_the_card(
    browser: Browser, serve: Serve, width: int
) -> None:
    page = _open(browser, serve("always"), width)
    page.evaluate(
        "(v) => { document.querySelector('.email-address').textContent = v; }",
        "x" * 64 + "@" + "y" * 60 + ".example.test",
    )
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    box = page.locator(".email-address").bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    page.context.close()
