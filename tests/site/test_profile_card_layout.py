# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the profile card. Run with ``pytest --run-visual``."""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml
from playwright.sync_api import Browser, Page, sync_playwright

from reach_around.config import load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)
LONG_TAG = "Extraordinarily-long-topic-name-x"[:30]
CARD = {
    "title": "Example Creator with a very long title " + "x" * 38,
    "tags": [
        "Retro games",
        "Security",
        "Amateur radio",
        "Open source",
        "Streaming",
        LONG_TAG,
        "Homelab",
        "Maker",
    ],
    "button": {"label": "Download the media kit now", "url": "https://example.com/kit"},
}
VARIANTS: dict[str, dict[str, Any]] = {
    "dark": {},
    "light": {"mode": "light"},
    "accent": {"colors": {"accent": "#2a6fdb", "on_accent": "#ffffff"}},
}


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    work = tmp_path_factory.mktemp("card")
    for name, theme in VARIANTS.items():
        raw = yaml.safe_load(EXAMPLE.read_text())
        raw["profile_card"] = CARD
        raw["theme"] = {**raw.get("theme", {}), **theme}
        config_path = work / f"{name}.yaml"
        config_path.write_text(yaml.safe_dump(raw))
        build_site(
            load_site_config(config_path),
            work / "out" / name,
            today=TODAY,
            assets_dir=EXAMPLE.parent,
        )
    handler = functools.partial(_Quiet, directory=str(work / "out"))
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
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
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


def _ratio(a: list[int], b: list[int]) -> float:
    def lum(c: list[int]) -> float:
        lin = [
            (v / 255) / 12.92
            if v / 255 <= 0.04045
            else (((v / 255) + 0.055) / 1.055) ** 2.4
            for v in c[:3]
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


_RGBA = """(css) => {
  const c = document.createElement('canvas'); c.width = c.height = 1;
  const x = c.getContext('2d'); x.fillStyle = css; x.fillRect(0, 0, 1, 1);
  return Array.from(x.getImageData(0, 0, 1, 1).data);
}"""


@pytest.mark.parametrize("width", [360, 390, 1440])
@pytest.mark.parametrize("path", ["", "events/", "privacy/"])
def test_card_fits_without_sideways_scroll(
    browser: Browser, base_url: str, width: int, path: str
) -> None:
    page = _open(browser, f"{base_url}dark/{path}", width)
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    card = page.locator(".profile-card")
    assert card.count() == 1
    box = card.bounding_box()
    assert box is not None and box["x"] >= 0 and box["x"] + box["width"] <= width
    for chip in page.locator(".profile-tag").all():
        cb = chip.bounding_box()
        assert cb is not None and cb["x"] >= 0 and cb["x"] + cb["width"] <= width
    page.context.close()


def test_chips_wrap_onto_several_rows_at_360px(browser: Browser, base_url: str) -> None:
    page = _open(browser, f"{base_url}dark/", 360)
    boxes = [c.bounding_box() for c in page.locator(".profile-tag").all()]
    assert len(boxes) == 8
    tops = {round(b["y"]) for b in boxes if b is not None}
    assert len(tops) >= 2, "eight chips should wrap on a phone"
    page.context.close()


@pytest.mark.parametrize("width", [360, 390, 1440])
def test_button_is_44px_tall_and_inside_the_card(
    browser: Browser, base_url: str, width: int
) -> None:
    page = _open(browser, f"{base_url}dark/", width)
    box = page.locator("a.profile-button").bounding_box()
    assert box is not None
    assert box["height"] >= 44
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    page.context.close()


def test_card_is_after_the_links_and_before_the_footer(
    browser: Browser, base_url: str
) -> None:
    page = _open(browser, f"{base_url}dark/", 390)
    last_link = page.locator("a.link").last.bounding_box()
    card = page.locator(".profile-card").bounding_box()
    footer = page.locator("main > footer").bounding_box()
    assert last_link and card and footer
    assert last_link["y"] < card["y"] < footer["y"]
    page.context.close()


@pytest.mark.parametrize("variant", sorted(VARIANTS))
def test_text_contrast_is_aa_in_every_variant(
    browser: Browser, base_url: str, variant: str
) -> None:
    page = _open(browser, f"{base_url}{variant}/", 390)
    pairs = {
        "title": ("h2.profile-title", "color", ".profile-card"),
        "chip": (".profile-tag", "color", ".profile-tag"),
        "button": ("a.profile-button", "color", "a.profile-button"),
    }
    for name, (text_sel, _prop, ground_sel) in pairs.items():
        styles = page.evaluate(
            """([t, g]) => [getComputedStyle(document.querySelector(t)).color,
                 getComputedStyle(document.querySelector(g)).backgroundColor]""",
            [text_sel, ground_sel],
        )
        fg = page.evaluate(_RGBA, styles[0])
        bg = page.evaluate(_RGBA, styles[1])
        assert bg[3] == 255, name
        assert _ratio(fg, bg) >= 4.5, (variant, name, _ratio(fg, bg))
    page.context.close()


def test_light_variant_really_is_light(browser: Browser, base_url: str) -> None:
    page = _open(browser, f"{base_url}light/", 390)
    surface = page.evaluate(
        _RGBA,
        page.evaluate(
            "getComputedStyle(document.querySelector('.profile-card')).backgroundColor"
        ),
    )
    assert sum(surface[:3]) > 600
    page.context.close()
