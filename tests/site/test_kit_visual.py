# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the media kit pages. Run with ``pytest --run-visual``.

Headless only, on loopback. The gate form is never submitted, and every request
the page makes must go to the loopback server (no third-party request).
"""

from __future__ import annotations

import functools
import os
import shutil
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright
from test_kit_build import TODAY, make_config

from reach_around.site.build import build_site

pytestmark = pytest.mark.visual


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    base = tmp_path_factory.mktemp("kit")
    out = base / "out"
    build_site(make_config(), out, today=TODAY, gated_out=base / "gated")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(out))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        binary = os.environ.get("RA_CHROMIUM")
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=binary or None
        )
        yield instance
        instance.close()


@pytest.mark.parametrize("path", ["/kit/", "/kit/retry/"])
@pytest.mark.parametrize("width", [390, 820, 1440])
def test_kit_pages_fit_the_screen_and_make_no_outside_request(
    browser: Browser, site: str, path: str, width: int
) -> None:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    hosts: set[str] = set()
    page.on("request", lambda r: hosts.add(r.url.split("/")[2]))
    page.goto(site + path)
    assert hosts == {site.split("/")[2]}
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )
    email = page.get_by_label("Email address")
    assert email.is_visible()
    button = page.get_by_role("button", name="Show the details")
    box = button.bounding_box()
    assert box is not None
    assert box["height"] >= 44
    honeypot = page.locator(".kit-hp").bounding_box()
    assert honeypot is None or (honeypot["width"] <= 1 and honeypot["height"] <= 1)
    if path == "/kit/":
        headline = page.locator(".kit-headline-number")
        assert headline.is_visible()
        assert headline.get_by_text("2027-03-01", exact=True).is_visible()
        value_size = headline.locator("strong").evaluate(
            "element => parseFloat(getComputedStyle(element).fontSize)"
        )
        assert value_size >= 48
        columns = page.locator(".kit-deck-intro").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns"
        )
        assert (len(columns.split()) > 1) is (width >= 820)
    context.close()


def test_the_gated_page_makes_no_third_party_request(
    browser: Browser, tmp_path: Path
) -> None:
    config = make_config(
        creator={"name": "Example Creator", "avatar": "https://example.com/a.png"}
    )
    out, gated = tmp_path / "out", tmp_path / "gated"
    build_site(config, out, today=TODAY, gated_out=gated)
    # Test-only: serve the stylesheet tree plus the gated page, the way the
    # Worker would serve it, from one loopback directory.
    root = tmp_path / "served"
    shutil.copytree(out, root)
    shutil.copy(gated / "kit.html", root / "kit.html")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        context = browser.new_context(reduced_motion="reduce")
        page = context.new_page()
        hosts: set[str] = set()
        page.on("request", lambda r: hosts.add(r.url.split("/")[2]))
        page.goto(base + "/kit.html")
        assert page.get_by_role(
            "heading", name="Example Creator media kit"
        ).is_visible()
        assert hosts == {base.split("/")[2]}
        context.close()
    finally:
        server.shutdown()
        server.server_close()


def test_a_remote_stylesheet_url_is_refused_and_a_local_one_loads_nothing_outside(
    browser: Browser, tmp_path: Path
) -> None:
    remote = tmp_path / "remote" / "overrides"
    remote.mkdir(parents=True)
    (remote / "custom.css").write_text(
        "body { background: url(https://example.com/x.png); }", encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"custom\.css"):
        build_site(
            make_config(),
            tmp_path / "r-out",
            today=TODAY,
            gated_out=tmp_path / "r-gated",
            overrides=remote,
        )
    local = tmp_path / "local" / "overrides"
    local.mkdir(parents=True)
    (local / "custom.css").write_text(
        "body { background: url(bg.png) no-repeat; }", encoding="utf-8"
    )
    out, gated = tmp_path / "out", tmp_path / "gated"
    build_site(make_config(), out, today=TODAY, gated_out=gated, overrides=local)
    root = tmp_path / "served"
    shutil.copytree(out, root)
    shutil.copy(gated / "kit.html", root / "kit.html")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        context = browser.new_context(reduced_motion="reduce")
        page = context.new_page()
        hosts: set[str] = set()
        page.on("request", lambda r: hosts.add(r.url.split("/")[2]))
        page.goto(base + "/kit.html")
        assert hosts == {base.split("/")[2]}
        context.close()
    finally:
        server.shutdown()
        server.server_close()
