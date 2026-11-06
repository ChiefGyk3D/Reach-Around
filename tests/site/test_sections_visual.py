# SPDX-License-Identifier: Apache-2.0
"""The sections layout in a browser (#232). Run with ``pytest --run-visual``.

Uses the shipped synthetic example with the brand-dark preset; only the crypto
tab and block titles are made equal to exercise the duplicate-heading rule.
"""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from playwright.sync_api import Browser, Page, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2027, 3, 10)
WIDTHS = [390, 768, 1440]
EDGE = 0.75  # px; sub-pixel rounding only


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data = yaml.safe_load(
        (ROOT / "examples/instance-variants/sections/site.yaml").read_text()
    )
    data["theme"] = yaml.safe_load(
        (ROOT / "examples/themes/brand-dark.yaml").read_text()
    )
    data["tabs"][2]["title"] = "Crypto"
    data["tabs"][2]["blocks"][0]["title"] = "Crypto"
    out = tmp_path_factory.mktemp("sections") / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
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


def _open(browser: Browser, url: str, width: int, all_open: bool) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state("load")
    if all_open:
        page.evaluate(
            "document.querySelectorAll('details').forEach(d => d.open = true)"
        )
    return page


@pytest.mark.parametrize("all_open", [False, True])
@pytest.mark.parametrize("width", WIDTHS)
def test_section_edges_match_the_top_link_buttons(
    browser: Browser, base_url: str, width: int, all_open: bool
) -> None:
    page = _open(browser, base_url, width, all_open)
    top = page.locator("main > ul.blocks a.link").first.bounding_box()
    assert top is not None
    boxes = page.locator("details.tab-section")
    assert boxes.count() == 4
    for i in range(boxes.count()):
        box = boxes.nth(i).bounding_box()
        assert box is not None
        assert abs(box["x"] - top["x"]) <= EDGE, (i, box, top)
        assert abs(box["x"] + box["width"] - (top["x"] + top["width"])) <= EDGE
    assert not page.evaluate(
        "document.documentElement.scrollWidth > document.documentElement.clientWidth"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_a_link_without_an_icon_is_centered_with_no_empty_gutter(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width, True)
    link = page.locator('a.link[href="https://example.com/share"]')
    box = link.bounding_box()
    text = link.locator(".link-text").bounding_box()
    assert box is not None and text is not None
    left = text["x"] - box["x"]
    right = box["x"] + box["width"] - (text["x"] + text["width"])
    assert abs(left - right) <= EDGE, (left, right)
    # No reserved 2.75rem slot: the label box fills the pill minus its padding.
    assert text["width"] > box["width"] - 40, (text, box)
    assert (
        page.evaluate(
            "(el) => getComputedStyle(el, '::after').display", link.element_handle()
        )
        == "none"
    )
    assert link.bounding_box()["height"] >= 44  # type: ignore[index]
    # An icon link keeps its chip and dots.
    iconed = page.locator('a.link[href="https://kofi.example.com/example"]')
    assert iconed.locator(".link-chip").count() == 1
    assert (
        page.evaluate(
            "(el) => getComputedStyle(el, '::after').display", iconed.element_handle()
        )
        != "none"
    )


@pytest.mark.parametrize("width", WIDTHS)
def test_an_equal_block_title_is_not_drawn_twice(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width, True)
    crypto = page.locator("#crypto")
    assert crypto.locator("h2").all_inner_texts() == ["Crypto"]
    assert crypto.locator(".crypto-title").count() == 0
    assert crypto.locator(".crypto-card").count() == 3
