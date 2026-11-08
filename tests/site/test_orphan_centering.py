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


# Full-row blocks in the flex-wrap grid and cards layouts (#307 review).
def _full_row_types() -> set[str]:
    """Block types that spanned a whole row before the flex change.

    Built from the old `grid-column: 1 / -1` rules, so a new type that gets
    one in any stylesheet must also get its flex rule or this test fails.
    """
    types: set[str] = set()
    for sheet in STATIC.glob("*.css"):
        for sel, body in _rules(sheet.read_text()).items():
            m = re.fullmatch(r"\.(?:blocks|layout-list) > \.block-(\w+)", sel)
            if m and "grid-column: 1 / -1" in body:
                types.add(m.group(1))
    return types


def test_full_row_blocks_keep_their_row_in_grid_and_cards() -> None:
    types = _full_row_types()
    # Loose collection, strict assertion: the known types must be found.
    assert {"platforms", "crypto", "email", "live", "section", "text"} <= types
    site = _rules((STATIC / "site.css").read_text())
    for layout in ("layout-grid", "layout-cards"):
        for kind in sorted(types):
            decls = site.get(f".{layout} > .block-{kind}", "")
            assert "flex: 0 0 100%" in decls and "width: 100%" in decls, (layout, kind)


def test_full_row_guard_bites_when_a_type_is_missing() -> None:
    site = _rules(".layout-grid > .block-text { flex: 0 0 100%; width: 100%; }")
    assert ".layout-grid > .block-platforms" not in site


def _wide_build(work: Path, layout: str) -> Path:
    raw = yaml.safe_load(EXAMPLE.read_text())
    links = [
        {"id": f"w{i}", "title": f"Button {i}", "url": f"https://example.com/{i}"}
        for i in range(3)
    ]
    items = [
        {"platform": "twitch", "handle": "example", "url": "https://example.com/t"},
        {"platform": "kick", "handle": "example", "url": "https://example.com/k"},
    ]
    raw["tabs"][0]["layout"] = layout
    raw["tabs"][0]["blocks"] = [
        *links,
        {"id": "plat", "title": "Find me", "items": items},
        {
            "id": "pay",
            "title": "Send crypto",
            "coins": [{"coin": "btc", "address": "bc1qexampleexampleexample"}],
        },
        {"id": "mail", "title": "Email", "address": "name@example.test"},
    ]
    cfg = work / "site.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    out = work / "out"
    build_site(load_site_config(cfg), out, today=TODAY, assets_dir=EXAMPLE.parent)
    return out


def test_wide_blocks_render_inside_a_grid_tab(tmp_path: Path) -> None:
    html = (_wide_build(tmp_path, "grid") / "index.html").read_text()
    for kind in ("platforms", "crypto", "email"):
        assert f"block block-{kind}" in html


@pytest.mark.visual
@pytest.mark.parametrize("layout", ["grid", "cards"])
def test_platforms_crypto_and_email_span_the_container_at_1280(
    tmp_path: Path, layout: str
) -> None:
    from playwright.sync_api import sync_playwright

    server, thread = _serve(_wide_build(tmp_path, layout))
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--no-sandbox"],
                executable_path=os.environ.get("RA_CHROMIUM") or None,
            )
            page = browser.new_context(
                viewport={"width": 1280, "height": 900}, reduced_motion="reduce"
            ).new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            # The content box of the list, without its own padding.
            inner = page.locator("ul.blocks").first.evaluate(
                "el => { const c = getComputedStyle(el);"
                " return el.clientWidth - parseFloat(c.paddingLeft)"
                " - parseFloat(c.paddingRight); }"
            )
            spans = {
                kind: page.locator(f"li.block-{kind}").first.bounding_box()
                for kind in ("platforms", "crypto", "email")
            }
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert inner > 600
    for kind, box in spans.items():
        assert box is not None, kind
        assert box["width"] >= inner - 2, (kind, box, inner)
