# SPDX-License-Identifier: Apache-2.0
"""Browser layout checks. Run with ``pytest --run-visual``.

They need Playwright and a Chromium: set ``RA_CHROMIUM`` to a browser binary,
or install Playwright's own with ``playwright install chromium``.
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
from typing import Literal

import pytest
import yaml
from playwright.sync_api import Browser, Page, sync_playwright

from reach_around.config import load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)

LONG_TITLE = "An extraordinarily long link title that must wrap " + "word " * 12
LONG_WORD = "Supercalifragilisticexpialidocious" * 3


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    raw = yaml.safe_load(EXAMPLE.read_text())
    raw["tabs"][0]["blocks"].append(
        {
            "id": "long-one",
            "title": LONG_TITLE,
            "subtitle": LONG_WORD,
            "url": "https://example.com/long",
            "icon": "link",
        }
    )
    for n in range(2, 7):
        raw["tabs"].append(
            {"id": f"tab-{n}", "title": f"Another Tab {n}", "blocks": []}
        )
    work = tmp_path_factory.mktemp("visual")
    config_path = work / "site.yaml"
    config_path.write_text(yaml.safe_dump(raw))
    out = work / "out"
    build_site(
        load_site_config(config_path),
        out,
        today=TODAY,
        assets_dir=EXAMPLE.parent,
    )
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


def _open(browser: Browser, url: str, width: int) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(url)
    return page


def test_no_horizontal_scroll_at_360px(browser: Browser, base_url: str) -> None:
    for path in ("", "tab-2/", "privacy/"):
        page = _open(browser, base_url + path, 360)
        assert page.evaluate("document.documentElement.scrollWidth") <= 360, path
        page.context.close()


def test_long_titles_wrap_inside_the_card(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    box = page.locator("a.link", has_text="extraordinarily").bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360
    assert box["height"] > 100, "the long title should wrap onto several lines"
    page.context.close()


def test_tab_bar_never_clips_a_tab_at_360px(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    bar = page.locator(".tabs ul")
    bar_box = bar.bounding_box()
    assert bar_box is not None
    assert bar_box["x"] >= 0 and bar_box["x"] + bar_box["width"] <= 360
    handle = bar.element_handle()
    assert page.evaluate("(el) => el.scrollWidth > el.clientWidth", handle), (
        "seven tabs should overflow a 360 px bar"
    )
    assert page.evaluate("(el) => getComputedStyle(el).overflowX", handle) in (
        "auto",
        "scroll",
    )
    # The edge cue is a scroll-attached gradient, so it shows only when tabs hide.
    image = page.evaluate("(el) => getComputedStyle(el).backgroundImage", handle)
    assert image.count("radial-gradient") == 2
    first = page.locator(".tabs a").first.bounding_box()
    assert first is not None and first["x"] >= bar_box["x"]
    page.context.close()


def test_desktop_column_is_centered_at_1440px(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 1440)
    box = page.locator("main").bounding_box()
    assert box is not None
    assert abs((box["x"] + box["width"] / 2) - 720) <= 1
    assert 500 <= box["width"] <= 600
    page.context.close()


def test_touch_targets_are_at_least_44px(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 360)
    for selector in ("a.link:visible", ".tabs a", ".social", "summary"):
        locator = page.locator(selector)
        assert locator.count() > 0, selector
        for i in range(locator.count()):
            box = locator.nth(i).bounding_box()
            assert box is not None
            assert box["height"] >= 43.5, (selector, i, box)
    social = page.locator(".social").first.bounding_box()
    assert social is not None and social["width"] >= 43.5
    page.context.close()


def test_icons_load_and_follow_the_text_color(browser: Browser, base_url: str) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    statuses: dict[str, int] = {}
    page.on("response", lambda r: statuses.__setitem__(r.url, r.status))
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    icons = {u: s for u, s in statuses.items() if "/static/icons/" in u}
    assert icons, "no icon was requested"
    assert set(icons.values()) == {200}
    assert all(u.startswith(base_url) for u in statuses), "off-host request"
    color = page.evaluate(
        "() => getComputedStyle(document.querySelector('.social .icon'))"
        ".backgroundColor"
    )
    assert color != "rgba(0, 0, 0, 0)"
    context.close()


def test_section_opens_and_chevron_rotates(browser: Browser, base_url: str) -> None:
    page = _open(browser, base_url, 390)
    section = page.locator("details.section").first
    assert section.get_attribute("open") is None
    probe = (
        "() => getComputedStyle(document.querySelector('summary'), '::after').transform"
    )
    before = page.evaluate(probe)
    page.locator("summary").first.click()
    assert section.get_attribute("open") is not None
    assert page.evaluate(probe) != before
    page.context.close()


def test_the_page_is_dark_whatever_the_visitor_prefers(
    browser: Browser, base_url: str
) -> None:
    schemes: tuple[Literal["light", "dark"], ...] = ("light", "dark")
    colors: set[str] = set()
    for scheme in schemes:
        context = browser.new_context(color_scheme=scheme)
        page = context.new_page()
        page.goto(base_url)
        colors.add(
            page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        )
        context.close()
    assert colors == {"rgb(14, 14, 14)"}


def _rgb(text: str) -> tuple[int, int, int]:
    nums = [int(n) for n in re.findall(r"\d+", text)[:3]]
    return nums[0], nums[1], nums[2]


def _ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    def lum(c: tuple[int, int, int]) -> float:
        lin = [
            (v / 255) / 12.92
            if v / 255 <= 0.04045
            else (((v / 255) + 0.055) / 1.055) ** 2.4
            for v in c
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_tab_bar_is_flush_to_the_top_of_the_column(
    browser: Browser, base_url: str
) -> None:
    for width in (1440, 390):
        page = _open(browser, base_url, width)
        bar = page.locator(".tabs ul").bounding_box()
        main = page.locator("main").bounding_box()
        assert bar is not None and main is not None
        assert bar["y"] == 0
        assert abs(bar["x"] - main["x"]) <= 1 and abs(bar["width"] - main["width"]) <= 1
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('.tabs a'))"
                ".textTransform"
            )
            == "uppercase"
        )
        page.context.close()


def test_link_buttons_are_orange_pills_with_dark_text_that_pass_aa(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 1440)
    style = page.evaluate(
        """() => {
          const el = document.querySelector('a.link:not(.link-featured)');
          const s = getComputedStyle(el);
          return [s.backgroundColor, s.color, s.borderTopLeftRadius, s.width];
        }"""
    )
    background, color, radius, _width = style
    assert _rgb(background) == (0xFB, 0xB0, 0x3B)
    assert _ratio(_rgb(color), _rgb(background)) >= 4.5
    assert radius == "36px"
    bar = page.evaluate(
        "() => { const s = getComputedStyle(document.querySelector('.tabs ul'));"
        " const a = getComputedStyle(document.querySelector('.tabs a'));"
        " return [s.backgroundColor, a.color]; }"
    )
    assert _ratio(_rgb(bar[1]), _rgb(bar[0])) >= 4.5
    page.context.close()


def test_jost_is_the_rendered_font_and_loads_from_this_site(
    browser: Browser, base_url: str
) -> None:
    context = browser.new_context(viewport={"width": 390, "height": 800})
    page = context.new_page()
    fonts: list[str] = []
    page.on("response", lambda r: fonts.append(r.url) if ".woff2" in r.url else None)
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    assert page.evaluate("() => document.fonts.check('16px Jost')")
    assert fonts and all(u.startswith(base_url) for u in fonts)
    context.close()


FOUR_TABS = ("Support", "Socials", "Product Recs", "Contact")
LONG_TABS = (
    "Support",
    "Socials",
    "Product Recommendations",
    "Contact Me Directly",
    "Press and Media Inquiries",
)
PHONE_WIDTHS = (360, 375, 390, 430)
TabSite = tuple[str, list[str]]


def _serve_tabs(work: Path, titles: tuple[str, ...]) -> Iterator[TabSite]:
    raw = yaml.safe_load(EXAMPLE.read_text())
    slugs = [re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-") for t in titles]
    raw["tabs"] = [
        {"id": slug, "title": title, "blocks": []}
        for slug, title in zip(slugs, titles, strict=True)
    ]
    config_path = work / "site.yaml"
    config_path.write_text(yaml.safe_dump(raw))
    out = work / "out"
    build_site(
        load_site_config(config_path), out, today=TODAY, assets_dir=EXAMPLE.parent
    )
    handler = functools.partial(_Quiet, directory=str(out))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/", slugs
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(scope="module")
def four_tabs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TabSite]:
    yield from _serve_tabs(tmp_path_factory.mktemp("four"), FOUR_TABS)


@pytest.fixture(scope="module")
def long_tabs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TabSite]:
    yield from _serve_tabs(tmp_path_factory.mktemp("long"), LONG_TABS)


def _tab_path(slugs: list[str], index: int) -> str:
    return "" if index == 0 else f"{slugs[index]}/"


@pytest.mark.parametrize("width", PHONE_WIDTHS)
def test_four_short_tabs_fit_one_row_without_scrolling(
    browser: Browser, four_tabs: TabSite, width: int
) -> None:
    url, slugs = four_tabs
    for index in range(len(FOUR_TABS)):
        page = _open(browser, url + _tab_path(slugs, index), width)
        bar = page.locator(".tabs ul").element_handle()
        assert page.evaluate("(el) => el.scrollWidth <= el.clientWidth", bar), (
            width,
            index,
            page.evaluate("(el) => [el.scrollWidth, el.clientWidth]", bar),
        )
        links = page.locator(".tabs a")
        assert links.count() == 4
        tops = set()
        for i in range(4):
            box = links.nth(i).bounding_box()
            assert box is not None
            assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, (
                width,
                i,
                box,
            )
            assert box["height"] >= 43.5, (width, i, box)
            tops.add(round(box["y"]))
        assert len(tops) == 1, "tabs should share one row"
        page.context.close()


def test_long_tab_labels_still_scroll_with_the_active_tab_visible(
    browser: Browser, long_tabs: TabSite
) -> None:
    url, slugs = long_tabs
    for width in (360, 390):
        page = _open(browser, url + _tab_path(slugs, len(LONG_TABS) - 1), width)
        bar = page.locator(".tabs ul").element_handle()
        assert page.evaluate("(el) => el.scrollWidth > el.clientWidth", bar)
        assert page.evaluate("(el) => getComputedStyle(el).overflowX", bar) in (
            "auto",
            "scroll",
        )
        page.wait_for_timeout(200)
        box = page.locator(".tabs a[aria-current='page']").bounding_box()
        bar_box = page.locator(".tabs ul").bounding_box()
        assert box is not None and bar_box is not None
        assert box["x"] >= bar_box["x"] - 0.5, (width, box, bar_box)
        assert box["x"] + box["width"] <= bar_box["x"] + bar_box["width"] + 0.5, (
            width,
            box,
            bar_box,
        )
        page.context.close()
