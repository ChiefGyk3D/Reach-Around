# SPDX-License-Identifier: Apache-2.0
"""Responsive layout checks in a browser. Run with ``pytest --run-visual``.

One rich synthetic site holds every block type that exists (links, a featured
link, thumbnails, sections, text, an image, a divider, crypto cards with a QR,
the profile card, the footer, the privacy page) and is checked from a 360 px
phone to a 2560 px monitor. Set ``RA_CHROMIUM`` to a browser binary.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import struct
import threading
import zlib
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2027, 3, 10)
LONG_TITLE = "An extraordinarily long link title that must wrap " + "word " * 12
LONG_WORD = "Supercalifragilisticexpialidocious" * 3
PARAGRAPH = (
    "This is a long paragraph of ordinary body text that a creator might write "
    "about the show, the schedule and where to find things. It keeps going so "
    "that the line length on a wide screen is easy to measure and a line that "
    "runs the whole width of a monitor would be hard to read. "
) * 3

WIDTHS = (360, 390, 430, 768, 820, 1024, 1180, 1280, 1440, 1920, 2560)
Scheme = Literal["light", "dark"]
Header = Literal["compact", "centered", "hero"]


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def real_png(width: int, height: int) -> bytes:
    row = b"\x00" + b"\x20\x80\xc0" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def _links(prefix: str, count: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for n in range(1, count + 1):
        link: dict[str, Any] = {
            "id": f"{prefix}-{n}",
            "title": f"Example {prefix} link {n}",
            "url": f"https://example.com/{prefix}/{n}",
            "icon": "link",
        }
        if n % 2:
            link["subtitle"] = "A short subtitle"
        out.append(link)
    return out


def rich_config(header: Header) -> dict[str, Any]:
    """Every block type on one site, with a tab per link layout."""
    start: list[dict[str, Any]] = [
        {
            "id": "feat",
            "title": "Featured link",
            "subtitle": "The one to click",
            "url": "https://example.com/f",
            "icon": "link",
            "featured": True,
        },
        {
            "id": "pic",
            "title": "A link with a picture",
            "url": "https://example.com/p",
            "image": "media/wide.png",
            "image_alt": "Wide picture",
        },
        {
            "id": "long",
            "title": LONG_TITLE,
            "subtitle": LONG_WORD,
            "url": "https://example.com/l",
            "icon": "link",
        },
        *_links("plain", 3),
        {
            "id": "open-section",
            "title": "An open section",
            "subtitle": "Links grouped together",
            "collapsed": False,
            "links": _links("open", 5),
        },
        {
            "id": "shut-section",
            "title": "A collapsed section",
            "links": _links("shut", 2),
        },
        {"id": "text", "markdown": f"## A heading\n\n{PARAGRAPH}\n\n- one\n- two\n"},
        {"id": "photo", "src": "media/wide.png", "alt": "A wide picture"},
        {"id": "rule"},
    ]
    return {
        "version": 1,
        "creator": {
            "name": "Example Creator",
            "header": header,
            "avatar": "media/mascot.svg",
            "socials": [
                {"name": "Video", "url": "https://v.example.com/x", "icon": "youtube"},
                {"name": "Feed", "url": "https://f.example.com/x", "icon": "rss"},
                {"name": "Mail", "url": "https://m.example.com/x", "icon": "email"},
                {
                    "name": "Labelled",
                    "url": "https://l.example.com/x",
                    "icon": "chat",
                },
            ],
        },
        "disclosure": "This page uses affiliate links. Example Creator may earn a commission.",
        "profile_card": {
            "title": "Example Creator",
            "tags": ["Retro games", "Security", "Amateur radio", "Open source"],
            "button": {
                "label": "Download the media kit",
                "url": "https://e.example.com/k",
            },
        },
        "tabs": [
            {"id": "start", "title": "Start Here", "blocks": start},
            {
                "id": "grid",
                "title": "Grid",
                "layout": "grid",
                "blocks": [
                    *_links("g", 5),
                    {
                        "id": "g-sec",
                        "title": "Grid section",
                        "collapsed": False,
                        "layout": "grid",
                        "links": _links("gs", 4),
                    },
                ],
            },
            {
                "id": "cards",
                "title": "Cards",
                "layout": "cards",
                "blocks": [
                    *_links("c", 5),
                    {
                        "id": "c-sec",
                        "title": "Cards section",
                        "collapsed": False,
                        "layout": "cards",
                        "links": _links("cs", 4),
                    },
                ],
            },
            {
                "id": "support",
                "title": "Support",
                "blocks": [
                    {"id": "intro", "markdown": "Nothing here is required."},
                    {
                        "id": "crypto",
                        "title": "Send crypto",
                        "coins": [
                            {
                                "coin": "btc",
                                "network": "Bitcoin mainnet",
                                "address": "bc1qexampleexampleexampleexampleexample",
                                "qr": "media/qr.png",
                            },
                            {
                                "coin": "xmr",
                                "address": "4" + "Example" * 18,
                                "qr": "media/qr.png",
                            },
                            {
                                "coin": "eth",
                                "network": "ERC-20 on Ethereum",
                                "address": "0xExampleExampleExampleExampleExample",
                            },
                            {
                                "coin": "sol",
                                "address": "ExampleExampleExampleExampleExample",
                            },
                        ],
                    },
                ],
            },
            {"id": "tab-5", "title": "Another Tab 5", "blocks": []},
            {"id": "tab-6", "title": "Another Tab 6", "blocks": _links("t6", 2)},
            {"id": "tab-7", "title": "Another Tab 7", "blocks": []},
        ],
    }


def build_rich(work: Path, header: Header) -> Path:
    media = work / "media"
    media.mkdir(parents=True)
    (media / "wide.png").write_bytes(real_png(120, 40))
    shutil.copy(
        ROOT / "examples" / "crypto" / "media" / "qr-example.png", media / "qr.png"
    )
    shutil.copy(
        ROOT / "examples" / "instance" / "media" / "mascot.svg", media / "mascot.svg"
    )
    cfg = SiteConfig.model_validate(rich_config(header))
    out = work / "out"
    build_site(cfg, out, today=TODAY, assets_dir=work)
    return out


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _site(work: Path, header: Header) -> Iterator[str]:
    handler = functools.partial(_Quiet, directory=str(build_rich(work, header)))
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
def compact(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _site(tmp_path_factory.mktemp("compact"), "compact")


@pytest.fixture(scope="module")
def centered(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _site(tmp_path_factory.mktemp("centered"), "centered")


@pytest.fixture(scope="module")
def hero(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    yield from _site(tmp_path_factory.mktemp("hero"), "hero")


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
    height: int = 900,
    *,
    scheme: Scheme = "dark",
    reduced: Literal["reduce", "no-preference"] = "reduce",
    touch: bool = False,
) -> BrowserContext:
    return browser.new_context(
        viewport={"width": width, "height": height},
        reduced_motion=reduced,
        color_scheme=scheme,
        has_touch=touch,
        is_mobile=touch,
    )


def _open(browser: Browser, url: str, width: int, **kw: Any) -> Page:
    page = _context(browser, width, **kw).new_page()
    page.goto(url)
    return page


# --- pages and probes ---------------------------------------------------------

# (fixture name, path) for each page the matrix covers. The Support page has its
# QR panels opened by the helper below.
PAGES = (
    ("compact", ""),
    ("hero", "grid/"),
    ("centered", "cards/"),
    ("compact", "support/"),
    ("compact", "privacy/"),
    ("compact", "tab-5/"),
)
PAGE_IDS = ["start", "grid-hero", "cards-centered", "support", "privacy", "empty"]


def _sites(request: pytest.FixtureRequest, name: str, path: str) -> str:
    return str(request.getfixturevalue(name)) + path


def _show(page: Page) -> None:
    """Open every QR panel so the tallest, widest state is what gets measured."""
    for summary in page.locator(".crypto-qr summary").all():
        summary.click()


PAGE_OVERFLOW = "document.documentElement.scrollWidth"

CLIPPED = """
() => {
  const bad = [];
  const cards = document.querySelectorAll(
    'a.link, .crypto-card, .profile-card, .section, .profile-tag, .crypto-address,'
    + ' .profile-button, .social-label, .tabs a');
  for (const el of cards) {
    if (el.scrollWidth > el.clientWidth + 1) {
      bad.push([el.className, el.scrollWidth, el.clientWidth]);
    }
  }
  return bad;
}
"""

COLUMNS = """
(selector) => new Set(
  Array.from(document.querySelectorAll(selector))
    .filter((el) => el.getClientRects().length)
    .map((el) => Math.round(el.getBoundingClientRect().left))
).size
"""

LINE_LENGTHS = """
(selector) => {
  const out = [];
  for (const p of document.querySelectorAll(selector)) {
    const walker = document.createTreeWalker(p, NodeFilter.SHOW_TEXT);
    const lines = new Map();
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const text = n.textContent || '';
      const re = /\\S+\\s*/g;
      for (let m = re.exec(text); m; m = re.exec(text)) {
        const r = document.createRange();
        r.setStart(n, m.index);
        r.setEnd(n, m.index + m[0].length);
        const rect = r.getClientRects()[0];
        if (!rect) continue;
        const key = Math.round(rect.top);
        lines.set(key, (lines.get(key) || 0) + m[0].length);
      }
    }
    out.push(...lines.values());
  }
  return out;
}
"""


@pytest.mark.parametrize("width", (320, *WIDTHS))
@pytest.mark.parametrize(("name", "path"), PAGES, ids=PAGE_IDS)
def test_no_horizontal_overflow_and_no_clipped_text(
    browser: Browser,
    request: pytest.FixtureRequest,
    width: int,
    name: str,
    path: str,
) -> None:
    page = _open(browser, _sites(request, name, path), width)
    _show(page)
    assert page.evaluate(PAGE_OVERFLOW) <= width
    assert page.evaluate(CLIPPED) == []
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("last", [True, False], ids=["last-tab", "first-tab"])
def test_tab_bar_is_usable_and_shows_the_active_tab(
    browser: Browser, compact: str, width: int, last: bool
) -> None:
    page = _open(browser, compact + ("tab-7/" if last else ""), width)
    page.wait_for_timeout(250)
    bar = page.locator(".tabs ul")
    bar_box = bar.bounding_box()
    assert bar_box is not None
    assert bar_box["x"] >= 0 and bar_box["x"] + bar_box["width"] <= width + 0.5
    handle = bar.element_handle()
    fits = page.evaluate("(el) => el.scrollWidth <= el.clientWidth + 1", handle)
    current = page.locator(".tabs a[aria-current='page']").bounding_box()
    assert current is not None
    assert current["x"] >= bar_box["x"] - 0.5
    assert current["x"] + current["width"] <= bar_box["x"] + bar_box["width"] + 0.5
    if not fits:
        assert page.evaluate("(el) => getComputedStyle(el).overflowX", handle) in (
            "auto",
            "scroll",
        )
    for i in range(7):
        box = page.locator(".tabs a").nth(i).bounding_box()
        assert box is not None and box["height"] >= 43.5
        if fits:
            assert box["x"] >= bar_box["x"] - 0.5
            assert box["x"] + box["width"] <= bar_box["x"] + bar_box["width"] + 0.5
    page.context.close()


@pytest.mark.parametrize("width", (360, 390, 430, 768, 820, 1024))
def test_tap_targets_are_44px_on_a_coarse_pointer(
    browser: Browser, compact: str, width: int
) -> None:
    page = _open(browser, compact + "support/", width, touch=True)
    assert page.evaluate("matchMedia('(pointer: coarse)').matches")
    _show(page)
    start = _open(browser, compact, width, touch=True)
    box = start.locator("a.link").first.bounding_box()
    assert box is not None and box["height"] >= 43.5
    start.context.close()
    selectors = (
        ".tabs a",
        ".social",
        "summary",
        ".profile-button",
        ".privacy-link a",
        ".copy-button",
    )
    for selector in selectors:
        locator = page.locator(selector)
        assert locator.count() > 0, selector
        for i in range(locator.count()):
            box = locator.nth(i).bounding_box()
            assert box is not None
            assert box["height"] >= 43.5 and box["width"] >= 43.5, (selector, i, box)
    page.context.close()


def test_hover_effects_do_not_stick_on_a_touch_screen(
    browser: Browser, compact: str
) -> None:
    page = _open(browser, compact, 390, touch=True)
    assert not page.evaluate("matchMedia('(hover: hover)').matches")
    link = page.locator("a.link").first
    link.hover()
    transform = page.evaluate(
        "(el) => getComputedStyle(el).transform", link.element_handle()
    )
    assert transform == "none"
    page.context.close()
    mouse = _open(browser, compact, 1024)
    assert mouse.evaluate("matchMedia('(hover: hover)').matches")
    link = mouse.locator("a.link").first
    link.hover()
    mouse.wait_for_timeout(50)
    transform = mouse.evaluate(
        "(el) => getComputedStyle(el).transform", link.element_handle()
    )
    assert transform != "none"
    mouse.context.close()


@pytest.mark.parametrize(
    ("width", "low", "high"),
    [
        (360, 300, 360),
        (390, 340, 390),
        (430, 380, 430),
        (768, 720, 720),
        (820, 720, 720),
        (1024, 864, 864),
        (1180, 864, 864),
        (1280, 864, 864),
        (1440, 864, 864),
        (1920, 972, 972),
        (2560, 1080, 1080),
    ],
)
def test_column_width_per_breakpoint(
    browser: Browser, compact: str, width: int, low: int, high: int
) -> None:
    page = _open(browser, compact, width)
    box = page.locator("main").bounding_box()
    assert box is not None
    assert low - 1 <= box["width"] <= high + 1, box
    assert abs((box["x"] + box["width"] / 2) - width / 2) <= 1
    page.context.close()


@pytest.mark.parametrize(
    ("width", "columns"),
    [(360, 1), (430, 1), (768, 1), (820, 1), (895, 1), (1024, 2), (1440, 2), (2560, 2)],
)
def test_link_groups_take_two_columns_from_896px(
    browser: Browser, compact: str, width: int, columns: int
) -> None:
    page = _open(browser, compact, width)
    assert page.evaluate(COLUMNS, ".blocks > .block-link:not(:first-child)") == columns
    assert page.evaluate(COLUMNS, ".section-links > li") == columns
    page.context.close()


@pytest.mark.parametrize(
    ("width", "columns"), [(360, 1), (768, 2), (1024, 3), (1440, 3), (2560, 3)]
)
def test_card_layouts_add_columns_with_the_width(
    browser: Browser, centered: str, width: int, columns: int
) -> None:
    page = _open(browser, centered + "cards/", width)
    assert page.evaluate(COLUMNS, ".blocks.layout-cards > .block-link") == columns
    page.context.close()


@pytest.mark.parametrize(
    ("width", "columns"), [(390, 1), (895, 1), (1024, 2), (2560, 2)]
)
def test_crypto_cards_take_two_columns_from_896px(
    browser: Browser, compact: str, width: int, columns: int
) -> None:
    page = _open(browser, compact + "support/", width)
    assert page.evaluate(COLUMNS, ".crypto-card") == columns
    page.context.close()


@pytest.mark.parametrize(
    ("header", "width", "beside"),
    [
        ("centered", 390, False),
        ("centered", 768, True),
        ("hero", 768, False),
        ("hero", 1024, True),
    ],
)
def test_header_goes_horizontal_where_there_is_room(
    browser: Browser,
    request: pytest.FixtureRequest,
    header: str,
    width: int,
    beside: bool,
) -> None:
    page = _open(browser, str(request.getfixturevalue(header)), width)
    avatar = page.locator(".avatar").bounding_box()
    name = page.locator(".site-header h1").bounding_box()
    assert avatar is not None and name is not None
    side_by_side = name["x"] >= avatar["x"] + avatar["width"] - 1
    assert side_by_side is beside, (avatar, name)
    page.context.close()


@pytest.mark.parametrize("width", (1024, 1440, 1920, 2560))
def test_body_text_keeps_a_readable_measure(
    browser: Browser, compact: str, width: int
) -> None:
    for path, selector in (("", ".text p"), ("privacy/", ".privacy p")):
        page = _open(browser, compact + path, width)
        lines = page.evaluate(LINE_LENGTHS, selector)
        assert lines, selector
        assert max(lines) <= 80, (width, selector, max(lines))
        page.context.close()


def _rgb(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)[:3]]


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
@pytest.mark.parametrize("width", (360, 820, 1440))
def test_text_keeps_aa_contrast_at_every_size(
    browser: Browser, compact: str, scheme: Scheme, width: int
) -> None:
    page = _open(browser, compact, width, scheme=scheme)
    pairs = {
        "a.link": "a.link",
        ".tabs a": ".tabs ul",
        ".profile-button": ".profile-button",
    }
    for text_sel, bg_sel in pairs.items():
        fg, bg = page.evaluate(
            "([t, b]) => [getComputedStyle(document.querySelector(t)).color,"
            " getComputedStyle(document.querySelector(b)).backgroundColor]",
            [text_sel, bg_sel],
        )
        assert _ratio(_rgb(fg), _rgb(bg)) >= 4.5, (text_sel, scheme, width, fg, bg)
    page.context.close()
    page = _open(browser, compact + "support/", width, scheme=scheme)
    for text_sel in (".crypto-label", ".crypto-network"):
        fg, bg = page.evaluate(
            "([t, b]) => [getComputedStyle(document.querySelector(t)).color,"
            " getComputedStyle(document.querySelector(b)).backgroundColor]",
            [text_sel, ".crypto-card"],
        )
        assert _ratio(_rgb(fg), _rgb(bg)) >= 4.5, (text_sel, scheme, width, fg, bg)
    page.context.close()


@pytest.mark.parametrize(
    ("name", "path"),
    [PAGES[0], PAGES[3], PAGES[2], PAGES[4]],
    ids=["start", "support", "cards", "privacy"],
)
@pytest.mark.parametrize("width", (390, 640, 1280))
def test_two_hundred_percent_text_and_zoom_do_not_overflow_or_clip(
    browser: Browser,
    request: pytest.FixtureRequest,
    width: int,
    name: str,
    path: str,
) -> None:
    url = _sites(request, name, path)
    # Text only: the visitor's default font size doubled.
    page = _open(browser, url, width)
    page.add_style_tag(content="html { font-size: 200%; }")
    _show(page)
    assert page.evaluate(PAGE_OVERFLOW) <= width
    assert page.evaluate(CLIPPED) == []
    page.context.close()
    # Page zoom: the same screen is half as many CSS pixels wide.
    page = _open(browser, url, width // 2)
    _show(page)
    assert page.evaluate(PAGE_OVERFLOW) <= width // 2
    assert page.evaluate(CLIPPED) == []
    page.context.close()


def test_reduced_motion_stops_every_animation(browser: Browser, compact: str) -> None:
    probe = (
        "() => [getComputedStyle(document.querySelector('.site-header')).animationName,"
        " getComputedStyle(document.querySelector('.section'),"
        " '::details-content').transitionDuration,"
        " getComputedStyle(document.querySelector('a.link')).transitionDuration,"
        " getComputedStyle(document.querySelector('.tabs a')).transitionDuration]"
    )
    page = _open(browser, compact, 1024, reduced="reduce")
    assert page.evaluate(probe) == ["none", "0s", "0s", "0s"]
    page.context.close()
    page = _open(browser, compact, 1024, reduced="no-preference")
    name, details, link, tab = page.evaluate(probe)
    assert name == "ra-rise" and details != "0s" and link != "0s" and tab != "0s"
    page.context.close()


def test_print_hides_the_background_and_shows_link_addresses(
    browser: Browser, compact: str
) -> None:
    page = _open(browser, compact, 800)
    page.emulate_media(media="print")
    assert page.evaluate("getComputedStyle(document.body).backgroundImage") == "none"
    tabs = page.evaluate("getComputedStyle(document.querySelector('.tabs')).display")
    assert tabs == "none"
    shown = page.evaluate(
        "getComputedStyle(document.querySelector('a.link'), '::after').content"
    )
    assert shown.startswith('"https://'), shown
    page.context.close()
