# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the platforms block. Run with ``pytest --run-visual``.

Set ``RA_CHROMIUM`` to a headless browser launcher (see the project's quiet
launcher). Nothing here clicks a link or navigates away from the local page.
All handles and counts are synthetic.
"""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from playwright.sync_api import Browser, BrowserContext, Locator, Page, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "platforms" / "site.yaml"
TODAY = date(2027, 3, 10)
LONG = "Example" * 11 + "x"  # 78 characters, no break opportunity
Scheme = Literal["light", "dark"]


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _config() -> SiteConfig:
    raw: dict[str, Any] = yaml.safe_load(EXAMPLE.read_text("utf-8"))
    stress = [
        {
            "platform": "mastodon",
            "handle": LONG,
            "url": "https://example.com/m",
            "count": 12_345_678,
            "as_of": "2027-03-01",
        },
        {
            "platform": "other",
            "name": "N" * 40,
            "handle": LONG,
            "url": "https://example.com/o",
        },
    ]
    raw["tabs"].append(
        {
            "id": "stress",
            "title": "Stress",
            "blocks": [
                {"id": "wide", "items": stress},
                {"id": "wide-chips", "layout": "chips", "items": stress},
            ],
        }
    )
    return SiteConfig.model_validate(raw)


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("platforms") / "out"
    build_site(_config(), out, today=TODAY, assets_dir=EXAMPLE.parent)
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


def _context(browser: Browser, width: int, scheme: Scheme = "dark") -> BrowserContext:
    return browser.new_context(
        viewport={"width": width, "height": 900},
        reduced_motion="reduce",
        color_scheme=scheme,
    )


def _open(browser: Browser, url: str, width: int) -> Page:
    page = _context(browser, width).new_page()
    page.goto(url)
    return page


def _box(locator: Locator, index: int) -> Any:
    box = locator.nth(index).bounding_box()
    assert box is not None
    return box


@pytest.mark.parametrize("path", ["", "stress/"])
@pytest.mark.parametrize("width", [320, 360, 768, 895, 896, 1440, 2560])
def test_no_sideways_scroll_and_44px_targets(
    browser: Browser, base_url: str, width: int, path: str
) -> None:
    page = _open(browser, base_url + path, width)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    links = page.locator("a.platform-link")
    assert links.count() >= 2
    for i in range(links.count()):
        box = _box(links, i)
        assert box["height"] >= 44 and box["width"] >= 44, (i, box)
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, (i, box)
    page.context.close()


@pytest.mark.parametrize("width", [320, 360])
def test_long_handles_wrap_inside_their_card(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url + "stress/", width)
    handles = page.locator(".platforms-list .platform-handle")
    cards = page.locator(".platforms-list a.platform-link")
    assert handles.count() == 2
    for i in range(2):
        box, card = _box(handles, i), _box(cards, i)
        assert box["x"] >= card["x"] - 0.5
        assert box["x"] + box["width"] <= card["x"] + card["width"] + 0.5
        assert box["height"] > 24, "an unbroken 78 character handle should wrap"
    page.context.close()


@pytest.mark.parametrize(
    ("width", "columns"), [(360, 1), (768, 1), (895, 1), (896, 2), (1440, 2)]
)
def test_list_is_two_columns_from_56rem(
    browser: Browser, base_url: str, width: int, columns: int
) -> None:
    page = _open(browser, base_url, width)
    cards = page.locator(".platforms-list a.platform-link")
    xs = {round(_box(cards, i)["x"]) for i in range(cards.count())}
    assert len(xs) == columns, xs
    page.context.close()


def test_chips_are_a_compact_wrapping_strip(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    chips = page.locator(".platforms-chips a.platform-link")
    assert chips.count() == 5
    assert _box(chips, 0)["height"] < 70, "a chip is one line, not a full row"
    tops = {round(_box(chips, i)["y"]) for i in range(5)}
    assert len(tops) >= 2, "five chips wrap on a 360 px screen"
    page.context.close()


def test_the_exact_number_is_available_to_assistive_technology(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 390)
    count = page.locator(".platforms-chips .platform-count").first
    assert count.get_attribute("aria-hidden") == "true"
    text = page.locator(".platforms-chips a.platform-link").first.text_content()
    assert text is not None and "406,800 followers" in text
    page.context.close()


def test_a_missing_count_shows_no_number(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 390)
    kick = page.locator(".platforms-list a.platform-link", has_text="Kick")
    assert kick.count() == 1
    assert kick.locator(".platform-count").count() == 0
    page.context.close()


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
def test_text_passes_aa_on_the_card(
    browser: Browser, base_url: str, scheme: Scheme
) -> None:
    page = _context(browser, 390, scheme).new_page()
    page.goto(base_url)
    bg_css = page.evaluate(
        "getComputedStyle(document.querySelector('.platforms-list a.platform-link'))"
        ".backgroundColor"
    )
    bg = page.evaluate(_TO_RGB, bg_css)
    for selector in (
        ".platforms-list .platform-name",
        ".platforms-list .platform-handle",
        ".platforms-list .platform-count",
        ".platforms-list .platform-asof",
    ):
        color = page.evaluate(
            "(s) => getComputedStyle(document.querySelector(s)).color", selector
        )
        fg = page.evaluate(_TO_RGB, color)
        assert _ratio(fg, bg) >= 4.5, (selector, scheme, _ratio(fg, bg))
    page.context.close()


def test_coarse_pointer_keeps_every_card_44px(browser: Browser, base_url: str) -> None:
    context = browser.new_context(
        viewport={"width": 390, "height": 900},
        is_mobile=True,
        has_touch=True,
        reduced_motion="reduce",
    )
    page = context.new_page()
    page.goto(base_url)
    assert page.evaluate("matchMedia('(pointer: coarse)').matches")
    links = page.locator("a.platform-link")
    for i in range(links.count()):
        box = _box(links, i)
        assert box["height"] >= 44 and box["width"] >= 44, (i, box)
    context.close()


def test_print_shows_the_address_and_hides_the_icon(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 800)
    page.emulate_media(media="print")
    display = page.evaluate(
        "getComputedStyle(document.querySelector('.platform-icon')).display"
    )
    assert display == "none"
    after = page.evaluate(
        "getComputedStyle(document.querySelector('.platforms-list a.platform-link'),"
        " '::after').content"
    )
    assert "https://example.com/" in after
    page.context.close()


def test_text_zoom_to_200_percent_does_not_overflow(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url + "stress/", 390)
    page.evaluate("document.documentElement.style.fontSize = '200%'")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.context.close()
