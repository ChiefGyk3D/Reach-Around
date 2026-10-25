# SPDX-License-Identifier: Apache-2.0
"""The responsive matrix with every block type present. ``pytest --run-visual``.

One synthetic site holds link, section, image, text, divider, crypto, email and
platforms blocks, the profile card and a footer credit with a long label, plus
the 404 page. Each page is opened on a touch screen at 320, 375, 768, 1024 and
1440 px and checked for sideways scroll, clipped text and tap targets of at
least 44 by 44 px. Set ``RA_SHOTS`` to a folder to save full-page screenshots
(keep it outside the repository). Nothing here clicks a mail or phone link.
"""

from __future__ import annotations

import copy
import functools
import os
import shutil
import threading
import urllib.request
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from test_responsive import (
    CLIPPED,
    PAGE_OVERFLOW,
    ROOT,
    TODAY,
    _show,
    real_png,
    rich_config,
)

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

WIDTHS = (320, 375, 768, 1024, 1440)
SHOTS = os.environ.get("RA_SHOTS")
LONG_HANDLE = "@an_extraordinarily_long_handle_that_has_no_spaces_at_all_ever_so_long_x"
assert len(LONG_HANDLE) <= 80
CREDIT = "Built with Reach Around, a very long credit label here"

PLATFORM_ITEMS: list[dict[str, Any]] = [
    {
        "platform": "youtube",
        "handle": "@examplecreator",
        "url": "https://example.com/youtube",
        "label": "subscribers",
        "count": 1240000,
        "as_of": "2027-03-01",
    },
    {
        "platform": "tiktok",
        "handle": LONG_HANDLE,
        "url": "https://example.com/tiktok",
        "count": 406800,
        "as_of": "2027-03-01",
    },
    {
        "platform": "twitch",
        "handle": "examplecreator",
        "url": "https://example.com/twitch",
        "count": 3812,
        "as_of": "2027-03-01",
    },
    {
        "platform": "kick",
        "handle": "examplecreator",
        "url": "https://example.com/kick",
    },
    {
        "platform": "threads",
        "handle": "@examplecreator",
        "url": "https://example.com/threads",
        "count": 999,
        "as_of": "2027-03-01",
    },
]


def full_config() -> dict[str, Any]:
    raw = copy.deepcopy(rich_config("centered"))
    raw["footer"] = {"credit_text": CREDIT}
    raw["tabs"][0]["blocks"].append(
        {
            "id": "plat",
            "title": "Find me everywhere",
            "note": "Counts are rounded and updated by hand.",
            "items": PLATFORM_ITEMS,
        }
    )
    raw["tabs"][0]["blocks"].append(
        {"id": "strip", "layout": "chips", "items": PLATFORM_ITEMS}
    )
    raw["tabs"].append(
        {
            "id": "contact",
            "title": "Contact",
            "blocks": [
                {
                    "id": "mail",
                    "title": "Email me",
                    "address": "name@example.test",
                    "note": "Business and press only. I read everything.",
                }
            ],
        }
    )
    return raw


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    work = tmp_path_factory.mktemp("matrix")
    media = work / "media"
    media.mkdir()
    (media / "wide.png").write_bytes(real_png(120, 40))
    shutil.copy(ROOT / "examples/crypto/media/qr-example.png", media / "qr.png")
    shutil.copy(ROOT / "examples/instance/media/mascot.svg", media / "mascot.svg")
    cfg = SiteConfig.model_validate(full_config())
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
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
        )
        yield instance
        instance.close()


PAGES = ("", "grid/", "support/", "contact/", "privacy/", "404.html")

# Visible controls, minus inline links inside running text (WCAG exempts them).
CONTROLS = """
() => {
  const out = [];
  const sel = 'a[href], button, summary, input, select, textarea';
  for (const el of document.querySelectorAll(sel)) {
    if (el.closest('.text') || el.classList.contains('skip-link')) continue;
    const r = el.getBoundingClientRect();
    if (!el.getClientRects().length || (r.width === 0 && r.height === 0)) continue;
    if (getComputedStyle(el).visibility === 'hidden') continue;
    out.push([el.tagName.toLowerCase() + '.' + el.className, r.width, r.height]);
  }
  return out;
}
"""

# Anything painted past the right edge, outside a horizontal scroller.
STICKS_OUT = """
(width) => {
  const bad = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.tabs ul')) continue;
    const r = el.getBoundingClientRect();
    if (r.width && r.right > width + 1) {
      bad.push([el.tagName + '.' + el.className, r.right]);
    }
  }
  return bad;
}
"""


def _open(browser: Browser, url: str, width: int) -> Page:
    ctx = browser.new_context(
        viewport={"width": width, "height": 800},
        reduced_motion="reduce",
        color_scheme="dark",
        has_touch=True,
        is_mobile=True,
    )
    page = ctx.new_page()
    page.goto(url)
    return page


def _shoot(page: Page, name: str, width: int) -> None:
    if SHOTS:
        stem = name.strip("/").replace("/", "-").replace(".html", "") or "start"
        page.screenshot(path=str(Path(SHOTS) / f"{stem}-{width}.png"), full_page=True)


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.strip("/") or "start")
def test_every_block_fits_and_every_control_is_44px(
    browser: Browser, site: str, path: str, width: int
) -> None:
    page = _open(browser, site + path, width)
    _show(page)
    _shoot(page, path, width)
    assert page.evaluate(PAGE_OVERFLOW) <= width
    assert page.evaluate(STICKS_OUT, width) == []
    assert page.evaluate(CLIPPED) == []
    controls = page.evaluate(CONTROLS)
    assert controls, "the guard found no controls"
    small = [c for c in controls if c[1] < 43.5 or c[2] < 43.5]
    assert small == [], small
    page.context.close()


@pytest.mark.parametrize("width", WIDTHS)
def test_the_pages_hold_every_block_and_the_credit(
    browser: Browser, site: str, width: int
) -> None:
    page = _open(browser, site, width)
    for selector in (
        "a.link",
        ".section",
        ".text",
        "hr",
        ".platforms-list",
        ".platforms-chips",
        ".profile-card",
        "footer .credit a",
    ):
        assert page.locator(selector).count() > 0, selector
    credit = page.locator("footer .credit a").bounding_box()
    assert credit is not None
    assert credit["x"] >= 0 and credit["x"] + credit["width"] <= width
    page.context.close()
    other = _open(browser, site + "support/", width)
    assert other.locator(".crypto-card").count() > 0
    other.context.close()
    mail = _open(browser, site + "contact/", width)
    assert mail.locator(".email-card").count() > 0
    mail.context.close()


def test_the_guard_bites(browser: Browser, site: str) -> None:
    page = _open(browser, site, 320)
    page.add_style_tag(content="main { width: 900px; max-width: none; }")
    assert page.evaluate(PAGE_OVERFLOW) > 320
    assert page.evaluate(STICKS_OUT, 320)
    page.context.close()
    page = _open(browser, site, 320)
    page.add_style_tag(content="a.link { min-height: 0; padding: 0; height: 20px; }")
    assert [c for c in page.evaluate(CONTROLS) if c[2] < 43.5]
    page.context.close()


def test_the_site_is_served_with_cover(site: str) -> None:
    assert site.startswith("http://127.0.0.1:")
    with urllib.request.urlopen(site) as resp:  # noqa: S310 - loopback test server
        assert b"viewport-fit=cover" in resp.read()
