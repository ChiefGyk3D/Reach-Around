# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the media kit pages. Run with ``pytest --run-visual``.

Headless only, on loopback. The gate form is never submitted, and every request
the page makes must go to the loopback server (no third-party request).
"""

from __future__ import annotations

import base64
import functools
import os
import shutil
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, sync_playwright
from test_kit_build import TODAY, gated_doc, make_config

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


@pytest.mark.parametrize("width", [320, 390])
def test_the_number_tables_stack_so_label_method_and_date_are_readable(
    browser: Browser, tmp_path: Path, width: int
) -> None:
    """Headline and engagement rows are blocks under 40rem: nothing scrolls
    sideways and every cell, with its label and date, is inside the screen."""
    from test_kit_trust import NUMS, TABS, trust_config

    from reach_around.config.trust import API

    out, gated = tmp_path / "out", tmp_path / "gated"
    assets = tmp_path / "assets" / "media"
    assets.mkdir(parents=True)
    from test_kit_recent_posts import PNG

    (assets / "missing.png").write_bytes(PNG)
    build_site(
        trust_config(),
        out,
        today=TODAY,
        numbers=NUMS,
        gated_out=gated,
        assets_dir=assets.parent,
    )
    root = tmp_path / "served"
    shutil.copytree(out, root)
    shutil.copy(gated / "kit.html", root / "kit.html")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(root))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        for path in ("/kit/", "/kit.html"):
            context = browser.new_context(
                viewport={"width": width, "height": 900}, reduced_motion="reduce"
            )
            page = context.new_page()
            page.goto(base + path)
            assert TABS and API
            tables = page.locator(".kit-table")
            assert tables.count() >= 1
            problems = page.evaluate(
                """(width) => {
                    const bad = [];
                    for (const t of document.querySelectorAll('.kit-table')) {
                        if (t.scrollWidth > t.clientWidth) bad.push('table scrolls');
                        for (const c of t.querySelectorAll('th, td')) {
                            const r = c.getBoundingClientRect();
                            if (r.width === 0) continue;
                            const off = r.left < 0 || r.right > width;
                            if (off) bad.push(c.textContent.slice(0, 30));
                        }
                    }
                    if (document.documentElement.scrollWidth > width) bad.push('page');
                    return bad;
                }""",
                width,
            )
            assert problems == [], problems
            first = page.locator(".kit-table").first
            assert first.get_by_text("2027-03-09").first.is_visible()
            assert first.locator(".kit-trust").first.is_visible()
            assert first.locator(".kit-method").first.is_visible()
            context.close()
    finally:
        server.shutdown()
        server.server_close()


def _numbers_page(browser: Browser, site: str, width: int) -> object:
    context = browser.new_context(
        viewport={"width": width, "height": 900}, reduced_motion="reduce"
    )
    page = context.new_page()
    # the gate script is third-party and not under test here
    page.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(site) else route.abort()
        ),
    )
    page.goto(site + "/kit/")
    return page


def test_numbers_section_uses_the_full_content_width_at_1440(
    browser: Browser, site: str
) -> None:
    """#704: the numbers table sat in a narrow right column and wrapped the
    Source pill, the method line and the date word by word."""
    page = _numbers_page(browser, site, 1440)
    kit = page.locator(".kit-deck-intro").bounding_box()  # type: ignore[attr-defined]
    numbers = page.locator(".kit-numbers").bounding_box()  # type: ignore[attr-defined]
    assert kit and numbers
    assert numbers["width"] >= kit["width"] - 1
    # the first screen sits above the numbers, not beside them
    first = page.locator(".kit-first").bounding_box()  # type: ignore[attr-defined]
    assert first and first["y"] + first["height"] <= numbers["y"] + 1
    lines = page.evaluate(  # type: ignore[attr-defined]
        """() => {
          const count = (el) => el.getClientRects().length;
          const rect = (el) => el.getBoundingClientRect();
          const rows = [...document.querySelectorAll('.kit-numbers tbody tr')];
          return rows.map((tr) => ({
            pill: count(tr.querySelector('.kit-trust')),
            pillHeight: rect(tr.querySelector('.kit-trust')).height,
            lineHeight: parseFloat(
              getComputedStyle(tr.querySelector('.kit-trust')).lineHeight) || 20,
            asOfHeight: rect(tr.querySelector('td[data-label="As of"] time')).height,
            methodWidth: rect(tr.querySelector('.kit-method')).width,
          }));
        }"""
    )
    assert lines
    for row in lines:
        assert row["pill"] == 1
        assert row["pillHeight"] < row["lineHeight"] * 2
        # one line of date: well under two text lines
        assert row["asOfHeight"] < 30
        assert row["methodWidth"] >= 160


def test_numbers_stay_stacked_cards_on_a_phone(browser: Browser, site: str) -> None:
    for width in (320, 390):
        page = _numbers_page(browser, site, width)
        row = page.locator(".kit-numbers tbody tr").first  # type: ignore[attr-defined]
        assert row.evaluate("e => getComputedStyle(e).display") == "block"
        assert page.evaluate(  # type: ignore[attr-defined]
            "document.documentElement.scrollWidth"
            " <= document.documentElement.clientWidth"
        )


def test_table_layout_and_no_wrap_at_the_narrowest_table_width(
    browser: Browser, site: str
) -> None:
    """From 48rem the numbers are a real table at full width; at that narrow
    width the pill and the date stay on one line only because of nowrap."""
    page = _numbers_page(browser, site, 768)
    table = page.locator(".kit-numbers .kit-table")  # type: ignore[attr-defined]
    assert table.evaluate("e => getComputedStyle(e).display") == "table"
    box = table.bounding_box()
    intro = page.locator(".kit-deck-intro").bounding_box()  # type: ignore[attr-defined]
    assert box and intro
    assert box["width"] >= intro["width"] - 1
    # squeeze the section the way a long method line or a big font would
    page.evaluate(  # type: ignore[attr-defined]
        "document.querySelector('.kit-numbers').style.width = '26rem'"
    )
    wrapped = page.evaluate(  # type: ignore[attr-defined]
        """() => [...document.querySelectorAll(
          '.kit-numbers .kit-trust, .kit-numbers td[data-label="As of"] time'
        )].filter((el) => el.getBoundingClientRect().height > 30).length"""
    )
    assert wrapped == 0


@pytest.fixture(scope="module")
def photo_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    base = tmp_path_factory.mktemp("kit-photo")
    (base / "inst" / "media").mkdir(parents=True)
    (base / "inst" / "media" / "kit-photo.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
            "ASsJTYQAAAAASUVORK5CYII="
        )
    )
    out = base / "out"
    build_site(
        make_config(gated_doc(photo="media/kit-photo.png", tags=["Linux"])),
        out,
        today=TODAY,
        gated_out=base / "gated",
        assets_dir=base / "inst",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_Quiet, directory=str(out))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_photo_sits_beside_the_first_screen_from_51_25rem_and_stacks_below(
    browser: Browser, photo_site: str
) -> None:
    """The breakpoint is 51.25rem = 820 px: stacked at 819, beside at 820."""

    def boxes(width: int) -> tuple[dict[str, float], dict[str, float]]:
        page = _numbers_page(browser, photo_site, width)
        photo = page.locator(".kit-photo").bounding_box()  # type: ignore[attr-defined]
        first = page.locator(".kit-first").bounding_box()  # type: ignore[attr-defined]
        assert photo and first
        return photo, first

    for width in (820, 1440):
        photo, first = boxes(width)
        assert photo["x"] + photo["width"] <= first["x"] + 1, width
        assert abs(photo["y"] - first["y"]) < 40, width
    for width in (800, 819):
        photo, first = boxes(width)
        assert first["y"] >= photo["y"] + photo["height"] - 1, width
