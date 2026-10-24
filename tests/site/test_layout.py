# SPDX-License-Identifier: Apache-2.0
"""Browser layout checks. Run with ``pytest --run-visual``.

They need Playwright and a Chromium: set ``RA_CHROMIUM`` to a browser binary,
or install Playwright's own with ``playwright install chromium``.
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
    build_site(load_site_config(config_path), out, today=TODAY)
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


def test_dark_and_light_follow_the_visitor(browser: Browser, base_url: str) -> None:
    schemes: tuple[Literal["light", "dark"], ...] = ("light", "dark")
    colors: dict[str, str] = {}
    for scheme in schemes:
        context = browser.new_context(color_scheme=scheme)
        page = context.new_page()
        page.goto(base_url)
        colors[scheme] = page.evaluate(
            "() => getComputedStyle(document.body).backgroundColor"
        )
        context.close()
    assert colors["light"] != colors["dark"]
