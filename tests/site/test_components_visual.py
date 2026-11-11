# SPDX-License-Identifier: Apache-2.0
"""The component sheet at 390, 820 and 1440. Run with ``pytest --run-visual``."""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
WIDTHS = [390, 820, 1440]


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    handler = functools.partial(_Quiet, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/docs/design-sheet/components.html"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        instance = p.chromium.launch(
            args=["--no-sandbox"],
            executable_path=os.environ.get("RA_CHROMIUM") or None,
        )
        yield instance
        instance.close()


def _open(browser: Browser, url: str, width: int) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state("load")
    return page


@pytest.mark.parametrize("width", WIDTHS)
def test_sheet_has_no_sideways_scroll_and_every_component(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.locator(".ra-c-plate").count() >= 4
    assert page.locator(".ra-c-ticks").count() >= 3
    assert page.locator(".ra-c-chip").count() >= 10
    assert page.locator(".ra-c-btn").count() >= 15


@pytest.mark.parametrize("width", WIDTHS)
def test_buttons_meet_the_tap_target_and_square_corners(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    buttons = page.locator(".ra-c-btn")
    assert buttons.count() >= 15
    for i in range(buttons.count()):
        box = buttons.nth(i).bounding_box()
        assert box is not None
        assert box["height"] >= 43.5, (i, box)
        assert (
            page.evaluate(
                "e => getComputedStyle(e).borderTopLeftRadius",
                buttons.nth(i).element_handle(),
            )
            == "0px"
        )


@pytest.mark.parametrize("width", WIDTHS)
def test_keyboard_focus_draws_a_ring_and_ticks_paint(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    page.keyboard.press("Tab")
    ring = page.evaluate(
        "() => { const s = getComputedStyle(document.activeElement);"
        " return [s.outlineStyle, s.outlineWidth]; }"
    )
    assert ring == ["solid", "3px"]
    tick = page.evaluate(
        "() => getComputedStyle("
        "document.querySelector('.ra-c-ticks'), '::before').backgroundImage"
    )
    assert tick.count("linear-gradient") == 8
