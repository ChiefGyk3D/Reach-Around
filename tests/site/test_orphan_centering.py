# SPDX-License-Identifier: Apache-2.0
"""An odd last button sits centered under the pair, not at the left (#307).

The CSS and HTML checks always run. The browser checks run with
``pytest --run-visual`` (set ``RA_CHROMIUM`` or install Playwright's Chromium).
"""

from __future__ import annotations

import functools
import os
import re
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from reach_around.config import load_site_config
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "reach_around" / "site" / "static"
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
TODAY = date(2027, 3, 10)


def _rules(css: str) -> dict[str, str]:
    """Map each selector (comma lists split) to its declaration text."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: dict[str, str] = {}
    for head, body in re.findall(r"([^{}@]+)\{([^{}]*)\}", css):
        for sel in head.split(","):
            sel = " ".join(sel.split())
            if sel:
                out[sel] = out.get(sel, "") + body
    return out


def _centered(decls: str) -> bool:
    return (
        "grid-column: 1 / -1" in decls
        and "justify-self: center" in decls
        and "width: calc((100% - var(--ra-space-3)) / 2)" in decls
    )


def test_two_column_orphans_are_centered_not_stretched() -> None:
    site = _rules((STATIC / "site.css").read_text())
    platforms = _rules((STATIC / "platforms.css").read_text())
    crypto = _rules((STATIC / "crypto.css").read_text())
    assert any(".section-links.layout-list" in s for s in site)  # collected
    assert _centered(site[".section-links.layout-list > li:last-child:nth-child(odd)"])
    alone = [s for s in site if s.startswith(".blocks.layout-list > .block-link:not(")]
    assert alone and all(_centered(site[s]) for s in alone)
    assert _centered(platforms[".platforms-list > .platform:last-child:nth-child(odd)"])
    assert _centered(crypto[".crypto-list > .crypto-card:last-child:nth-child(odd)"])


def test_grid_and_cards_wrap_centered_with_a_fixed_basis() -> None:
    site = _rules((STATIC / "site.css").read_text())
    head = site[".blocks.layout-grid"]
    assert "flex-wrap: wrap" in head and "justify-content: center" in head
    assert "display: grid" not in head
    assert "flex: 0 0 min(10rem, 100%)" in site[".blocks.layout-grid > *"]
    assert "flex: 0 0 min(15rem, 100%)" in site[".blocks.layout-cards > *"]
    assert "flex: 0 0 100%" in site[".layout-grid > .block-section"]


def test_guard_bites_on_a_left_aligned_orphan() -> None:
    bad = _rules(".x:last-child:nth-child(odd) { grid-column: 1 / -1; }")
    assert not _centered(bad[".x:last-child:nth-child(odd)"])


def test_css_uses_no_inline_style_and_html_has_none(tmp_path: Path) -> None:
    out = _build(tmp_path, count=3)
    html = (out / "index.html").read_text()
    assert " style=" not in html
    items = re.search(r'<ul class="section-links layout-list">(.*?)</ul>', html, re.S)
    assert items is not None
    assert items.group(1).count("<li>") == 3


def _build(work: Path, *, count: int, layout: str = "list") -> Path:
    raw = yaml.safe_load(EXAMPLE.read_text())
    raw["tabs"][0]["blocks"] = [
        {
            "id": "odd",
            "title": "Odd",
            "layout": layout,
            "links": [
                {
                    "id": f"o{i}",
                    "title": f"Button {i}",
                    "url": f"https://example.com/{i}",
                }
                for i in range(count)
            ],
        }
    ]
    cfg = work / "site.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    out = work / "out"
    build_site(load_site_config(cfg), out, today=TODAY, assets_dir=EXAMPLE.parent)
    return out


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _serve(out: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    handler = functools.partial(_Quiet, directory=str(out))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(scope="module")
def sites(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    servers = []
    urls: dict[str, str] = {}
    for name, layout in (("list", "list"), ("grid", "grid")):
        work = tmp_path_factory.mktemp(name)
        server, thread = _serve(
            _build(work, count=3 if name == "list" else 5, layout=layout)
        )
        servers.append((server, thread))
        urls[name] = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        yield urls
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            thread.join()


@pytest.mark.visual
@pytest.mark.parametrize("width", [1280, 393])
@pytest.mark.parametrize("kind", ["list", "grid"])
def test_last_button_is_centered_in_the_browser(
    sites: dict[str, str], width: int, kind: str
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
        )
        page = browser.new_context(
            viewport={"width": width, "height": 900}, reduced_motion="reduce"
        ).new_page()
        page.goto(sites[kind])
        boxes = [b.bounding_box() for b in page.locator("a.link").all()]
        holder = page.locator(".section-links").bounding_box()
        browser.close()
    assert holder is not None and all(boxes) and len(boxes) >= 3
    last = boxes[-1]
    assert last is not None
    left = last["x"] - holder["x"]
    right = holder["x"] + holder["width"] - (last["x"] + last["width"])
    assert abs(left - right) <= 2, (left, right)
    if width == 1280 and kind == "list":
        first = boxes[0]
        assert first is not None
        assert last["width"] < holder["width"] * 0.6, "one column wide, not full"
        assert last["y"] > first["y"]
    if width == 393 and kind == "list":
        assert last["width"] > holder["width"] * 0.9, "phone stays one column"
