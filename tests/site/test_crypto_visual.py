# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the crypto block. Run with ``pytest --run-visual``.

Set ``RA_CHROMIUM`` to a browser binary (for example /usr/bin/chromium). The
page is served from 127.0.0.1, which counts as a secure context, so the real
clipboard API runs. All addresses are synthetic.
"""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from reach_around.config import load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "crypto" / "site.yaml"
TODAY = date(2027, 3, 10)
BTC = "bc1qexampleexampleexampleexampleexample"
LONG = "4" + "Example" * 18  # 127 characters, no break opportunity

Scheme = Literal["light", "dark"]


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("crypto") / "out"
    build_site(load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent)
    handler = functools.partial(_Quiet, directory=str(out))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
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


def _context(
    browser: Browser,
    width: int,
    *,
    scheme: Scheme = "dark",
    script: bool = True,
) -> BrowserContext:
    return browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion="reduce",
        color_scheme=scheme,
        java_script_enabled=script,
    )


def _open(browser: Browser, url: str, width: int, *, script: bool = True) -> Page:
    page = _context(browser, width, script=script).new_page()
    page.goto(url)
    return page


@pytest.mark.parametrize("width", [360, 390])
def test_no_sideways_scroll_and_a_44px_copy_button(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    buttons = page.locator("button.copy-button")
    assert buttons.count() == 5
    for i in range(buttons.count()):
        box = buttons.nth(i).bounding_box()
        assert box is not None
        assert box["height"] >= 44 and box["width"] >= 44
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
    summary = page.locator(".crypto-qr summary").bounding_box()
    assert summary is not None and summary["height"] >= 44
    page.context.close()


@pytest.mark.parametrize("width", [360, 390])
def test_a_long_address_wraps_inside_its_card(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    page.evaluate(
        "(v) => { document.querySelector('.crypto-address').textContent = v; }", LONG
    )
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    code = page.locator(".crypto-address").first.bounding_box()
    assert code is not None
    assert code["x"] >= 0 and code["x"] + code["width"] <= width
    assert code["height"] > 40
    page.context.close()


def test_without_script_the_button_is_hidden_and_the_address_selectable(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 390, script=False)
    assert not page.locator("button.copy-button").first.is_visible()
    assert page.locator(".crypto-address").first.is_visible()
    mode = page.evaluate(
        "getComputedStyle(document.querySelector('.crypto-address')).userSelect"
    )
    assert mode == "all"
    page.context.close()


def test_the_script_marks_the_page_and_shows_the_button(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 390)
    assert page.evaluate("document.documentElement.classList.contains('copy-ready')")
    assert page.locator("button.copy-button").first.is_visible()
    page.context.close()


def test_copy_writes_the_address_announces_and_resets(
    browser: Browser, base_url: str
) -> None:
    context = _context(browser, 390)
    context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=base_url.rstrip("/")
    )
    page = context.new_page()
    requests: list[str] = []
    page.goto(base_url)
    page.on("request", lambda r: requests.append(r.url))
    button = page.locator("button.copy-button").first
    status = page.locator("[data-copy-status]").first
    assert status.get_attribute("aria-live") == "polite"
    button.click()
    page.wait_for_function(
        "document.querySelector('[data-copy-status]').textContent === 'Copied'"
    )
    assert button.inner_text() == "Copied"
    assert page.evaluate("navigator.clipboard.readText()") == BTC
    page.wait_for_function(
        "document.querySelector('[data-copy-status]').textContent === ''",
        timeout=4000,
    )
    assert button.inner_text() == "Copy"
    assert requests == [], "copying must not make a network request"
    context.close()


def test_without_clipboard_access_the_address_is_selected(
    browser: Browser, base_url: str
) -> None:
    context = _context(browser, 390)
    context.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: undefined});"
    )
    page = context.new_page()
    page.goto(base_url)
    page.locator("button.copy-button").first.click()
    assert page.evaluate("window.getSelection().toString()") == BTC
    status = page.locator("[data-copy-status]").first.inner_text()
    assert "selected" in status.lower()
    context.close()


def test_the_qr_opens_and_loads_from_this_site(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 390)
    image = page.locator(".crypto-qr-image")
    assert not image.is_visible()
    page.locator(".crypto-qr summary").click()
    assert image.is_visible()
    page.wait_for_function(
        "document.querySelector('.crypto-qr-image').naturalWidth > 0"
    )
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.context.close()


# --- contrast ----------------------------------------------------------------

_TO_RGB = """
(css) => {
  const c = document.createElement('canvas'); c.width = c.height = 1;
  const g = c.getContext('2d', {willReadFrequently: true});
  g.fillStyle = css; g.fillRect(0, 0, 1, 1);
  return Array.from(g.getImageData(0, 0, 1, 1).data).slice(0, 3);
}
"""


def _luminance(rgb: list[int]) -> float:
    lin = [
        (v / 255) / 12.92
        if v / 255 <= 0.04045
        else (((v / 255) + 0.055) / 1.055) ** 2.4
        for v in rgb
    ]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: list[int], b: list[int]) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_text_passes_aa_on_the_orange_and_the_card(
    browser: Browser, base_url: str, scheme: Scheme
) -> None:
    context = _context(browser, 390, scheme=scheme)
    page = context.new_page()
    page.goto(base_url)
    card_bg = page.evaluate(
        "getComputedStyle(document.querySelector('.crypto-card')).backgroundColor"
    )
    own_background = {
        "button.copy-button": True,
        ".crypto-address": True,
        ".crypto-network": False,
        ".crypto-label": False,
    }
    for selector, has_own in own_background.items():
        style = page.evaluate(
            "(s) => { const c = getComputedStyle(document.querySelector(s));"
            " return [c.color, c.backgroundColor]; }",
            selector,
        )
        fg = page.evaluate(_TO_RGB, style[0])
        bg = page.evaluate(_TO_RGB, style[1] if has_own else card_bg)
        assert _ratio(fg, bg) >= 4.5, (selector, scheme, _ratio(fg, bg))
    context.close()
