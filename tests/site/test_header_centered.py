# SPDX-License-Identifier: Apache-2.0
"""`header: centered` without an avatar stays centered at every width (#236).

The tablet rules for the avatar grid set `align-self` on the name and the icons.
In the column flex layout used when there is no avatar, `align-self` beats the
container's `align-items: center`, so the name sat at the right edge above
left-edged icons. Those child rules belong to the avatar grid only.
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

from reach_around.config import load_site_config
from reach_around.site.build import build_site

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "src" / "reach_around" / "site" / "static" / "site.css"
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
GUARD = ":has(> .avatar)"


def _selectors(css: str) -> list[str]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [
        s.strip()
        for head in re.findall(r"([^{}]+)\{", css)
        for s in head.split(",")
        if s.strip() and not s.strip().startswith("@")
    ]


def _unguarded(css: str) -> list[str]:
    """Selectors that style a direct child of `.header-centered` with no avatar guard."""
    return [
        s
        for s in _selectors(css)
        if ".header-centered >" in s and GUARD not in s.split(".header-centered >")[0]
    ]


def test_child_rules_of_centered_header_need_the_avatar_guard() -> None:
    css = CSS.read_text(encoding="utf-8")
    # Loose collection, strict assertion: something must have been collected.
    assert any(".header-centered" in s for s in _selectors(css))
    assert _unguarded(css) == []


def test_guard_bites_on_an_unguarded_rule() -> None:
    bad = "@media (min-width: 48rem) { .header-centered > h1 { align-self: end; } }"
    assert _unguarded(bad) == [".header-centered > h1"]
    good = ".header-centered:has(> .avatar) > h1 { align-self: end; }"
    assert _unguarded(good) == []


pytest.importorskip("playwright")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def pages(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    servers = []
    urls: dict[str, str] = {}
    base = load_site_config(EXAMPLE)
    for name, avatar in (("noavatar", None), ("avatar", base.creator.avatar)):
        out = tmp_path_factory.mktemp(name) / "out"
        creator = base.creator.model_copy(
            update={"header": "centered", "avatar": avatar}
        )
        config = base.model_copy(update={"creator": creator})
        build_site(config, out, today=date(2027, 3, 10), assets_dir=EXAMPLE.parent)
        handler = functools.partial(_Quiet, directory=str(out))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        urls[name] = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        yield urls
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


@pytest.mark.visual
@pytest.mark.parametrize("width", [390, 768, 1440])
def test_name_and_icons_share_one_center_without_an_avatar(
    pages: dict[str, str], width: int
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
        )
        page = browser.new_page(viewport={"width": width, "height": 900})
        page.goto(pages["noavatar"])
        page.wait_for_load_state("networkidle")
        h1 = page.locator(".header-centered > h1").bounding_box()
        socials = page.locator(".header-centered > .socials").bounding_box()
        header = page.locator(".header-centered").bounding_box()
        assert h1 and socials and header
        mid = header["x"] + header["width"] / 2
        assert abs(h1["x"] + h1["width"] / 2 - mid) <= 1
        assert abs(socials["x"] + socials["width"] / 2 - mid) <= 1
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        browser.close()


@pytest.mark.visual
def test_avatar_layout_keeps_the_tablet_grid(pages: dict[str, str]) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(pages["avatar"])
        page.wait_for_load_state("networkidle")
        avatar = page.locator(".header-centered > .avatar").bounding_box()
        h1 = page.locator(".header-centered > h1").bounding_box()
        socials = page.locator(".header-centered > .socials").bounding_box()
        assert avatar and h1 and socials
        assert h1["x"] >= avatar["x"] + avatar["width"]
        assert abs(h1["x"] - socials["x"]) <= 12  # icons are inset by their padding
        browser.close()
