# SPDX-License-Identifier: Apache-2.0
"""Layout fixes from the device QA pass (#280). Run with ``pytest --run-visual``.

Every name, URL and count is synthetic. Nothing here clicks a link or leaves the
local page. Set ``RA_CHROMIUM`` to a headless browser.
"""

from __future__ import annotations

import base64
import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

TODAY = date(2027, 3, 10)
LONG_TITLE = "Extraordinarily" + "Unbreakable" * 4 + " collapsed section title"
LONG_SUB = (
    "A long explanatory subtitle that keeps going so the pill card would grow "
    "into a tall slab with a single dangling word on its last line today"
)
DISCLOSURE = (
    "This page contains affiliate links, which means the creator may earn a small "
    "commission when you buy through them, at no extra cost to you, and that "
    "never changes what is recommended here or why it is on the page."
)


def _chip(name: str, count: int) -> dict[str, Any]:
    return {
        "platform": name,
        "handle": "@examplecreator",
        "url": f"https://example.com/{name}",
        "count": count,
        "as_of": "2027-03-01",
    }


def _config() -> SiteConfig:
    tabs: list[dict[str, Any]] = [
        {
            "id": "home",
            "title": "Home",
            "blocks": [
                {
                    "id": "chips",
                    "title": "Follow me",
                    "layout": "chips",
                    "note": "Counts are rounded and updated by hand or by a script, "
                    "so treat every number here as approximate at best.",
                    "items": [
                        _chip("tiktok", 406800),
                        _chip("instagram", 38200),
                        _chip("youtube", 1240000),
                        _chip("twitch", 3812),
                        _chip("threads", 999),
                    ],
                },
                {
                    "id": "sec",
                    "title": LONG_TITLE,
                    "collapsed": True,
                    "links": [
                        {"id": "s1", "title": "One", "url": "https://example.com/d"}
                    ],
                },
                {
                    "id": "long-sub",
                    "title": "Long subtitle",
                    "subtitle": LONG_SUB,
                    "url": "https://example.com/a",
                    "icon": "link",
                },
                {
                    "id": "plain",
                    "title": "No icon here",
                    "url": "https://example.com/b",
                },
                {
                    "id": "icon",
                    "title": "Has icon",
                    "url": "https://example.com/c",
                    "icon": "youtube",
                    "affiliate": True,
                },
            ],
        }
    ]
    for tab_id in ("alpha", "bravo", "charlie", "delta", "zulu"):
        tabs.append(
            {
                "id": tab_id,
                "title": tab_id.title() + " Page",
                "blocks": [{"id": f"{tab_id}-t", "markdown": "Hello"}],
            }
        )
    raw = {
        "version": 1,
        "creator": {"name": "Example Creator", "header": "centered"},
        "disclosure": DISCLOSURE,
        "tabs": tabs,
    }
    return SiteConfig.model_validate(raw)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("devqa") / "out"
    build_site(_config(), out, today=TODAY)
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
        instance = p.chromium.launch(
            args=["--no-sandbox"],
            executable_path=os.environ.get("RA_CHROMIUM") or None,
        )
        yield instance
        instance.close()


def _open(
    browser: Browser,
    url: str,
    width: int,
    *,
    zoom: bool = False,
    open_all: bool = False,
) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state("load")
    if zoom:
        page.add_style_tag(content="html { font-size: 200%; }")
    if open_all:
        page.evaluate(
            "document.querySelectorAll('details').forEach(d => d.open = true)"
        )
    return page


def _inner_width(page: Page, selector: str) -> float:
    """Content width of the first match, padding excluded."""
    return float(
        page.evaluate(
            """(sel) => { const e = document.querySelector(sel);
              const s = getComputedStyle(e);
              return e.getBoundingClientRect().width
                - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight); }""",
            selector,
        )
    )


def _box(page: Page, selector: str, index: int = 0) -> Any:
    box = page.locator(selector).nth(index).bounding_box()
    assert box is not None, selector
    return box


def _pixels(page: Page, y: int, xs: list[int]) -> list[list[int]]:
    """RGB at (x, y) of a screenshot, read back through a canvas (no imaging lib)."""
    png = base64.b64encode(page.screenshot()).decode()
    pixels: list[list[int]] = page.evaluate(
        """async ([png, y, xs]) => {
          const i = new Image();
          i.src = 'data:image/png;base64,' + png;
          await i.decode();
          const c = document.createElement('canvas');
          c.width = i.width;
          c.height = i.height;
          const g = c.getContext('2d'); g.drawImage(i, 0, 0);
          return xs.map(x => Array.from(g.getImageData(x, y, 1, 1).data).slice(0, 3));
        }""",
        [png, y, xs],
    )
    return pixels


def test_collapsed_section_title_wraps_at_200_percent_zoom(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 360, zoom=True)
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    summary = _box(page, ".section summary")
    assert summary["x"] + summary["width"] <= 360.5
    overflow = page.evaluate(
        "(() => { const s = document.querySelector('.section summary');"
        " return s.scrollWidth - s.clientWidth; })()"
    )
    assert overflow <= 1, overflow
    page.context.close()


def test_summary_and_children_may_shrink(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    style = page.evaluate(
        "(() => { const q = document.querySelector('.section summary');"
        " const s = getComputedStyle(q);"
        " return [s.minWidth, s.overflowWrap]; })()"
    )
    assert style[0] == "0px" and style[1] == "anywhere", style
    page.context.close()


def test_long_subtitle_pill_is_not_a_fully_round_slab(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 360)
    card = page.locator("a.link", has_text="Long subtitle")
    info = card.evaluate(
        "(el) => { const sub = el.querySelector('.link-subtitle');"
        " const s = getComputedStyle(el);"
        " return {radius: parseFloat(s.borderTopLeftRadius),"
        " wrap: getComputedStyle(sub).textWrapStyle,"
        " height: el.getBoundingClientRect().height}; }"
    )
    assert info["wrap"] in ("balance", "pretty"), info
    assert info["radius"] <= 28, info
    page.context.close()


def test_chips_fill_balanced_rows_and_heading_lines_up(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 360)
    chips = page.locator(".platforms-chips a.platform-link")
    assert chips.count() == 5
    rows: dict[int, list[Any]] = {}
    for i in range(5):
        box = chips.nth(i).bounding_box()
        assert box is not None
        rows.setdefault(round(box["y"]), []).append(box)
    assert len(rows) >= 2
    strip = _box(page, ".platforms-chips")
    for row in rows.values():
        first = min(b["x"] for b in row)
        last = max(b["x"] + b["width"] for b in row)
        assert abs(first - strip["x"]) <= 1.0, (first, strip)
        assert abs(last - (strip["x"] + strip["width"])) <= 1.0, (last, strip)
    heading = _box(page, ".platforms-title")
    first_chip = min(b["x"] for row in rows.values() for b in row)
    assert abs(heading["x"] - first_chip) <= 1.0, (heading, first_chip)
    page.context.close()


def test_link_without_icon_reserves_no_slot(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    plain = page.locator("a.link", has_text="No icon here")
    iconed = page.locator("a.link", has_text="Has icon")
    p, i = plain.bounding_box(), iconed.bounding_box()
    assert p is not None and i is not None
    text = plain.locator(".link-text").bounding_box()
    assert text is not None
    # The text spans the pill (minus padding), not a middle column.
    assert text["width"] >= p["width"] - 2 * 16 - 2, (text, p)
    assert p["height"] <= i["height"] + 1
    page.context.close()


def test_lone_last_card_spans_the_row_on_desktop(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 1440)
    inner = _inner_width(page, ".blocks")
    last_link = page.locator(".blocks > .block-link").last
    box = last_link.bounding_box()
    assert box is not None
    assert last_link.evaluate("el => el.matches(':last-child:nth-child(odd)')")
    assert box["width"] >= inner - 1, (box, inner)
    page.context.close()


def test_lone_last_link_in_an_open_section_spans(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 1440, open_all=True)
    inner = _inner_width(page, ".section-links")
    link = _box(page, ".section-links > li a.link")
    assert link["width"] >= inner - 1, (link, inner)
    page.context.close()


def test_phone_tab_bar_cue_and_current_tab_in_view(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url + "zulu/", 360)
    scroll = page.evaluate(
        "(() => { const u = document.querySelector('.tabs ul');"
        " return [u.scrollWidth, u.clientWidth, u.scrollLeft]; })()"
    )
    assert scroll[0] > scroll[1], "six tabs overflow a phone"
    cur = _box(page, ".tabs a[aria-current=page]")
    assert cur["x"] >= -0.5 and cur["x"] + cur["width"] <= 360.5, "current tab in view"
    page.close()
    page = _open(browser, base_url + "alpha/", 360)
    # A visible fade must sit on the right edge while more tabs hide there. The
    # sample row is the top 3 px of the bar, clear of any label text.
    ul = _box(page, ".tabs ul")
    y = int(ul["y"]) + 3
    reds = _pixels(page, y, [200, 346, 350, 356])
    base = reds[0]
    assert sum(abs(a - b) for a, b in zip(reds[1], base, strict=True)) >= 20, (
        reds
    )  # 14 px in
    assert sum(abs(a - b) for a, b in zip(reds[3], base, strict=True)) >= 40, (
        reds
    )  # at the edge
    page.context.close()


def test_long_paragraphs_balance_or_pretty(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    for selector, expected in (
        (".disclosure", "balance"),
        (".platforms-note", "pretty"),
    ):
        wrap = page.evaluate(
            f"getComputedStyle(document.querySelector('{selector}')).textWrapStyle"
        )
        assert wrap == expected, (selector, wrap)
    page.context.close()
