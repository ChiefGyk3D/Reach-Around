# SPDX-License-Identifier: Apache-2.0
"""The 404 page in a browser, served the way a host does. ``pytest --run-visual``.

The server answers any unknown path with 404.html and status 404, so the page
is requested at a deep URL and its root-relative assets must still load.
"""

from __future__ import annotations

import functools
import io
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import BinaryIO

import pytest
from playwright.sync_api import Browser, sync_playwright

from reach_around.config import load_site_config
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)
DEEP = "a/b/c"


class _NotFound(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def send_head(self) -> io.BytesIO | BinaryIO | None:
        if Path(self.translate_path(self.path)).exists():
            return super().send_head()
        body = Path(self.translate_path("/404.html")).read_bytes()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        return io.BytesIO(body)


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("nf") / "out"
    build_site(load_site_config(EXAMPLE), out, today=TODAY, assets_dir=EXAMPLE.parent)
    handler = functools.partial(_NotFound, directory=str(out))
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


@pytest.mark.parametrize("width", [390, 1440])
def test_404_looks_like_the_site_at_a_deep_path(
    browser: Browser, base_url: str, width: int
) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": 800}, reduced_motion="reduce"
    )
    page = context.new_page()
    failed: list[str] = []
    page.on("response", lambda r: failed.append(r.url) if r.status >= 400 else None)
    response = page.goto(base_url + DEEP)
    assert response is not None and response.status == 404
    page.wait_for_load_state("networkidle")
    # Only the document itself may be a 404; every stylesheet and image loaded.
    assert failed == [base_url + DEEP]
    assert page.locator(".tabs a").count() >= 2
    assert page.locator("text=That page does not exist.").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    # site.css applied: it centers and caps the column, the default does not.
    max_width = page.evaluate(
        "getComputedStyle(document.querySelector('main')).maxWidth"
    )
    assert max_width != "none"
    shot = os.environ.get("RA_404_SHOT")
    if shot:
        page.screenshot(path=f"{shot}-{width}.png")
    page.locator(".tabs a").first.click()
    assert page.url == base_url
    context.close()
