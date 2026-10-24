# SPDX-License-Identifier: Apache-2.0
"""Thumbnail layout in a browser. Run with ``pytest --run-visual``."""

from __future__ import annotations

import functools
import os
import struct
import threading
import zlib
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

TODAY = date(2027, 3, 10)
LONG = "An extraordinarily long link title that must wrap " + "word " * 12


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def real_png(width: int, height: int) -> bytes:
    """A real, decodable, non-square PNG so object-fit has work to do."""
    row = b"\x00" + b"\x20\x80\xc0" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    work = tmp_path_factory.mktemp("thumbs")
    (work / "wide.png").write_bytes(real_png(120, 40))
    cfg = SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "C"},
            "tabs": [
                {
                    "id": "a",
                    "title": "A",
                    "blocks": [
                        {
                            "id": "pic",
                            "title": "Picture",
                            "url": "https://example.com/1",
                            "image": "wide.png",
                            "image_alt": "Wide picture",
                            "icon": "link",
                        },
                        {
                            "id": "long",
                            "title": LONG,
                            "url": "https://example.com/2",
                            "image": "wide.png",
                        },
                        {
                            "id": "plain",
                            "title": "Plain",
                            "url": "https://example.com/3",
                            "icon": "link",
                        },
                    ],
                }
            ],
        }
    )
    out = work / "out"
    build_site(cfg, out, today=TODAY, assets_dir=work)
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


def _open(browser: Browser, url: str, width: int) -> Page:
    context = browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(url)
    page.wait_for_load_state("load")
    return page


@pytest.mark.parametrize("width", [360, 390, 1440])
def test_thumbnail_is_a_round_square_inside_the_card(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    img = page.locator("a.link img.link-thumb").first
    # lazy images below the fold load on scroll; this one is in view.
    handle = img.element_handle()
    box = img.bounding_box()
    card = page.locator("a.link").first.bounding_box()
    assert box is not None and card is not None
    assert abs(box["width"] - box["height"]) < 0.5, box
    assert abs(box["width"] - 44) < 0.5, box
    assert box["x"] >= card["x"]
    assert box["x"] + box["width"] <= card["x"] + card["width"]
    fit, radius = page.evaluate(
        "(el) => [getComputedStyle(el).objectFit,"
        " getComputedStyle(el).borderTopLeftRadius]",
        handle,
    )
    assert fit == "cover"
    assert radius in ("50%", "22px"), radius
    assert page.evaluate("(el) => el.complete && el.naturalWidth", handle) == 120
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    page.context.close()


@pytest.mark.parametrize("width", [360, 390, 1440])
def test_image_replaces_the_chip_and_text_column_is_unchanged(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, base_url, width)
    links = page.locator("a.link")
    assert links.nth(0).locator(".link-chip").count() == 0
    assert links.nth(2).locator(".link-chip").count() == 1
    with_thumb = links.nth(0).locator(".link-text").bounding_box()
    with_chip = links.nth(2).locator(".link-text").bounding_box()
    assert with_thumb is not None and with_chip is not None
    assert abs(with_thumb["x"] - with_chip["x"]) < 0.5
    assert abs(with_thumb["width"] - with_chip["width"]) < 0.5
    page.context.close()


def test_long_title_with_thumbnail_stays_inside_at_360(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, base_url, 360)
    box = page.locator("a.link", has_text="extraordinarily").bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= 360
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    page.context.close()
