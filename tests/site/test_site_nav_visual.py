# SPDX-License-Identifier: Apache-2.0
"""The property bar in a browser (#671). ``pytest --run-visual``. Never clicks it.

A 320 px viewport and a 160 px layout viewport at device scale 2 (the layout of
a 320 px window at 200 percent zoom). The titles are the longest allowed (24
wide characters) and there are six items, so the row must wrap, not overflow.
"""

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
from playwright.sync_api import Browser, sync_playwright

from reach_around.config import SiteConfig, load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)
WIDE = "W" * 24
ITEMS: list[dict[str, Any]] = [
    {
        "title": WIDE[:-1] + chr(ord("A") + n),
        "url": f"https://s{n}.example.com/",
        "current": n == 0,
    }
    for n in range(6)
]
TAP_PX = 44


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("site-nav") / "out"
    config = SiteConfig.model_validate(
        {
            **load_site_config(EXAMPLE).model_dump(mode="json", exclude_none=True),
            "site_nav": {"items": ITEMS},
        }
    )
    build_site(config, out, today=TODAY, assets_dir=EXAMPLE.parent)
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


@pytest.mark.parametrize("path", ["", "privacy/"])
@pytest.mark.parametrize(
    ("width", "scale"), [(320, 1), (160, 2)], ids=["320px", "200pct-zoom"]
)
def test_bar_and_footer_line_wrap_without_overflow(
    browser: Browser, base_url: str, path: str, width: int, scale: int
) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": 800},
        device_scale_factor=scale,
        reduced_motion="reduce",
    )
    page = context.new_page()
    page.goto(base_url + path)
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.documentElement.scrollWidth") <= page.evaluate(
        "document.documentElement.clientWidth"
    )
    for kind in ("bar", "foot"):
        nav = page.locator(f"nav.site-nav-{kind}")
        assert nav.count() == 1
        links = nav.locator("a")
        assert links.count() == len(ITEMS)
        tops: set[int] = set()
        for i in range(links.count()):
            link = links.nth(i)
            assert link.is_visible()
            box = link.bounding_box()
            assert box is not None
            assert box["height"] >= TAP_PX, (kind, i, box)
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, (kind, i, box)
            tops.add(round(box["y"]))
        # At this width six wide titles cannot share a row: it wrapped.
        assert len(tops) > 1, (kind, tops)
        # Nothing inside an item is cut off sideways.
        assert nav.evaluate(
            "n => [...n.querySelectorAll('a')]"
            ".every(a => a.scrollWidth <= a.clientWidth)"
        )
    context.close()
