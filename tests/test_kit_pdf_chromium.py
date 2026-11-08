# SPDX-License-Identifier: Apache-2.0
"""The real browser step of the kit PDF. Run with ``pytest --run-visual``.

Set ``RA_CHROMIUM`` to a browser binary (for example /usr/bin/chromium), or have
``chromium`` on PATH. The pages are synthetic; the only server is on 127.0.0.1
and exists to prove the browser never calls it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from kitpdfharness import CANARY, build_instance
from reach_around.kit.pdf import (
    Isolation,
    build_kit_pdf,
    chromium_renderer,
    find_chromium,
)
from reach_around.kit.pdftext import extract_pages

pytestmark = pytest.mark.visual

CREATED = date(2027, 3, 10)


@pytest.fixture(scope="module")
def chromium() -> Path:
    found = find_chromium(None)
    if found is None:
        pytest.skip("no Chromium found; set RA_CHROMIUM")
    return found


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", hits
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


def render(chromium: Path, tmp_path: Path, isolation: Isolation = "off") -> Path:
    config, site, gated = build_instance(tmp_path)
    result = build_kit_pdf(
        config,
        gated_dir=gated,
        site_dir=site,
        out_dir=tmp_path / "private",
        name="kit.pdf",
        created=CREATED,
        renderer=chromium_renderer(chromium, isolation=isolation),
    )
    return result.path


def test_real_render_has_the_text_and_is_scrubbed(
    chromium: Path, tmp_path: Path
) -> None:
    data = render(chromium, tmp_path).read_bytes()
    flat = "".join(" ".join(extract_pages(data)).split())
    assert f"AcmeFakeCo{CANARY}" in flat and "Exampleland40%" in flat
    for leaked in (b"HeadlessChrome", b"Mozilla", b"Skia", b"/Creator", b"/ModDate"):
        assert leaked not in data
    assert b"D:20270310000000Z" in data
    assert str(tmp_path).encode() not in data


def test_real_render_is_reproducible_byte_for_byte(
    chromium: Path, tmp_path: Path
) -> None:
    first = render(chromium, tmp_path / "a").read_bytes()
    second = render(chromium, tmp_path / "b").read_bytes()
    assert first == second


def test_the_browser_never_calls_a_server_even_when_the_page_asks(
    chromium: Path, tmp_path: Path, server: tuple[str, list[str]]
) -> None:
    base, hits = server
    page = (
        "<!doctype html><meta charset=utf-8><title>t</title>"
        f'<link rel=stylesheet href="{base}/css"><img src="{base}/img">'
        f'<script src="{base}/js"></script><iframe src="{base}/frame"></iframe>'
        f'<p>hello <a href="{base}/link">there</a></p>'
    )
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    pdf = chromium_renderer(chromium)(page, work)
    assert pdf.startswith(b"%PDF")
    assert hits == []


def _netns_works() -> bool:
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    probe = subprocess.run(  # noqa: S603 - fixed argv, no input
        [unshare, "--user", f"--map-user={os.getuid()}", "--net", "true"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    return probe.returncode == 0


def test_network_namespace_isolation_renders_too(
    chromium: Path, tmp_path: Path
) -> None:
    if not _netns_works():
        pytest.skip("unprivileged network namespaces are not available")
    data = render(chromium, tmp_path, isolation="netns").read_bytes()
    flat = "".join(" ".join(extract_pages(data)).split())
    assert "Exampleland40%" in flat
