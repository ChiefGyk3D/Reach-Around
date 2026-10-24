# SPDX-License-Identifier: Apache-2.0
"""The footer credit in a browser. ``pytest --run-visual``. Never clicks it."""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright

from reach_around.config import Footer, load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)
LONG = "Built with Reach Around, a very long credit label here"


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("footer") / "out"
    config = load_site_config(EXAMPLE).model_copy(
        update={"footer": Footer(credit_text=LONG)}
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


@pytest.mark.parametrize("path", ["", "privacy/", "404.html"])
@pytest.mark.parametrize("width", [360, 768, 1440])
def test_credit_fits_without_overflow(
    browser: Browser, base_url: str, path: str, width: int
) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion="reduce"
    )
    page = context.new_page()
    page.goto(base_url + path)
    page.wait_for_load_state("networkidle")
    link = page.locator("footer .credit a")
    assert link.count() == 1
    assert link.is_visible()
    assert link.inner_text() == LONG
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    box = link.bounding_box()
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    assert box["height"] >= 16
    context.close()
