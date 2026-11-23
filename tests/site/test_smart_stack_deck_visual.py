# SPDX-License-Identifier: Apache-2.0
"""The deck arrangement in a real browser (#677).

Served from a local port only; anything else is aborted and recorded, and every
test asserts none was seen. All data is synthetic.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml
from playwright.sync_api import Browser, FloatRect, Page, Route, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual
TODAY = date(2027, 3, 10)
ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "instance" / "site.yaml"
FIT_SIZES = [(1440, 900), (1878, 930)]
SOCIALS = [
    {"name": n, "url": f"https://example.com/{i}", "icon": i}
    for n, i in (
        ("YouTube", "youtube"),
        ("TikTok", "tiktok"),
        ("Instagram", "instagram"),
        ("Mastodon", "mastodon"),
        ("Bluesky", "bluesky"),
        ("Threads", "threads"),
        ("GitHub", "github"),
        ("RSS", "rss"),
    )
]


def hub_doc() -> dict[str, Any]:
    """The mock's six groups on the example instance's creator and theme."""
    doc: dict[str, Any] = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))

    def links(intent: str, titles: tuple[str, ...]) -> list[dict[str, Any]]:
        # Buttons (Watch, Support) carry no subtitle and Support no icon, as in
        # the mock; the row groups carry a short subtitle.
        rows = intent in ("community", "work", "blog")
        out = []
        for n, title in enumerate(titles, 1):
            link: dict[str, Any] = {
                "id": f"{intent}-{n}",
                "title": title,
                "url": f"https://example.com/{intent}/{n}",
            }
            if rows:
                link["subtitle"] = "A short line about the destination"
            if intent == "watch":
                link["icon"] = ("twitch", "kick")[n - 1]
            elif rows:
                link["icon"] = "link"
            out.append(link)
        return out

    tabs: list[dict[str, Any]] = []
    for intent, title, names, style in (
        ("watch", "Watch", ("Twitch", "Kick"), None),
        ("follow", "Follow", (), "icons"),
        ("community", "Community", ("Discord", "Matrix"), "rows"),
        (
            "support",
            "Support the work",
            ("Shop merch", "Tip on stream", "Ko-fi", "Patreon"),
            None,
        ),
        (
            "work",
            "Work with me",
            ("Brands and sponsors", "Consulting and hiring"),
            "rows",
        ),
        ("blog", "Read and gear", ("Latest posts", "Gear I use"), "rows"),
    ):
        tab: dict[str, Any] = {
            "id": intent,
            "title": title,
            "intent": intent,
            "blocks": links(intent, names),
        }
        if style:
            tab["style"] = style
        tabs.append(tab)
    doc["tabs"] = tabs
    doc["creator"]["socials"] = SOCIALS
    # Frames on the buttons only (D13 a): none on the status box, the header or
    # the featured card.
    doc["theme"]["frame_classes"] = {"hero": "none", "head": "none", "featured": "none"}
    doc["page"] = {
        "layout": "stack",
        "status": {
            "text": "Next stream: Friday 8 PM Eastern",
            "schedule": "Full replays on YouTube.",
        },
        "watch": ["watch-1", "watch-2"],
        "featured": {"link": "blog-1", "kicker": "New this week"},
    }
    doc["features"] = {"click_tracking": True}
    return doc


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=os.environ.get("RA_CHROMIUM") or None
        )
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("deckvis") / "out"
    build_site(
        SiteConfig.model_validate(hub_doc()),
        out,
        today=TODAY,
        assets_dir=EXAMPLE.parent,
    )
    handler = functools.partial(_Quiet, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def open_page(
    browser: Browser, base: str, size: tuple[int, int]
) -> tuple[Page, list[str]]:
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        reduced_motion="reduce",
        color_scheme="dark",
    )
    foreign: list[str] = []
    host = urlsplit(base).netloc

    def guard(route: Route) -> None:
        if urlsplit(route.request.url).netloc != host:
            foreign.append(route.request.url)
            route.abort()
        else:
            route.continue_()

    context.route("**/*", guard)
    page = context.new_page()
    page.goto(base)
    page.wait_for_load_state("load")
    return page, foreign


def box(page: Page, selector: str, index: int = 0) -> FloatRect:
    found = page.locator(selector).nth(index).bounding_box()
    assert found is not None, selector
    return found


@pytest.mark.parametrize("size", FIT_SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_the_deck_fits_the_screen_without_scrolling(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size)
    # 1878 wide is past 100rem, so the engine's 112.5% root scale applies.
    root = page.evaluate(
        "parseFloat(getComputedStyle(document.documentElement).fontSize)"
    )
    assert root == (18 if size[0] >= 1600 else 16)
    assert page.evaluate("document.documentElement.scrollHeight") <= size[1]
    assert page.evaluate("document.documentElement.scrollWidth") <= size[0]
    assert foreign == []
    page.context.close()


def test_desktop_places_tiles_in_the_deck_rows(browser: Browser, base_url: str) -> None:
    page, foreign = open_page(browser, base_url, (1440, 900))
    featured = box(page, ".smart-featured")
    onair = box(page, ".smart-onair")
    follow = box(page, ".smart-group-follow")
    community = box(page, ".smart-group-community")
    support = box(page, ".smart-group-support")
    work = box(page, ".smart-group-work")
    read = box(page, ".smart-group-blog")
    assert abs(featured["y"] - onair["y"]) < 2 and featured["x"] < onair["x"]
    assert abs(follow["y"] - community["y"]) < 2 and follow["x"] < community["x"]
    assert follow["width"] > community["width"]
    assert abs(support["y"] - work["y"]) < 2 and abs(work["y"] - read["y"]) < 2
    assert support["x"] < work["x"] < read["x"]
    assert onair["y"] < follow["y"] < support["y"]
    assert page.locator(".smart-jump").is_hidden()
    twitch, kick = box(page, ".smart-watch-link", 0), box(page, ".smart-watch-link", 1)
    assert abs(twitch["width"] - kick["width"]) < 0.5
    assert abs(twitch["height"] - kick["height"]) < 0.5
    assert abs(twitch["y"] - kick["y"]) < 1
    assert foreign == []
    page.context.close()


def test_tablet_has_two_columns_and_no_chips(browser: Browser, base_url: str) -> None:
    page, foreign = open_page(browser, base_url, (820, 1180))
    follow = box(page, ".smart-group-follow")
    community = box(page, ".smart-group-community")
    support = box(page, ".smart-group-support")
    work = box(page, ".smart-group-work")
    assert abs(support["y"] - work["y"]) < 2 and support["x"] < work["x"]
    assert follow["width"] < 820 * 0.6 or community["width"] < 820 * 0.6
    onair = box(page, ".smart-onair")
    assert onair["width"] > support["width"] * 1.8
    assert page.locator(".smart-jump").is_hidden()
    assert page.evaluate("document.documentElement.scrollWidth") <= 820
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize("size", [(390, 844), (320, 640)], ids=lambda s: f"{s[0]}")
def test_phone_stacks_one_column_with_44px_jump_chips(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size)
    chips = page.locator(".smart-jump a")
    assert chips.count() == 5
    for i in range(chips.count()):
        found = chips.nth(i).bounding_box()
        assert found is not None and found["height"] >= 44 and found["width"] >= 44
    onair, follow = box(page, ".smart-onair"), box(page, ".smart-group-follow")
    assert onair["y"] < box(page, ".smart-jump")["y"] < follow["y"]
    assert abs(follow["x"] - box(page, ".smart-group-work")["x"]) < 1
    assert page.evaluate("document.documentElement.scrollWidth") <= size[0]
    page.locator('.smart-jump a[href="#group-support"]').click()
    assert page.evaluate("location.hash") == "#group-support"
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize(
    "size", [(640, 450), (720, 450), (512, 360)], ids=lambda s: f"zoom200-{s[0]}"
)
def test_200_percent_zoom_reflows_with_no_sideways_scroll(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    # 200% zoom of a 1280, 1440 or 1024 wide window is a 640, 720 or 512 wide viewport.
    page, foreign = open_page(browser, base_url, size)
    assert page.evaluate("document.documentElement.scrollWidth") <= size[0]
    for i in range(page.locator(".smart-group").count()):
        found = box(page, ".smart-group", i)
        assert found["x"] >= 0 and found["x"] + found["width"] <= size[0] + 0.5
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize("size", [(1440, 900), (820, 1180), (390, 844)], ids=str)
def test_every_tile_is_dark_and_only_the_support_tile_is_the_plate(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = open_page(browser, base_url, size)
    colors = page.evaluate(
        "[...document.querySelectorAll('.smart-group, .smart-onair, .smart-featured')]"
        ".map(e => [e.className, getComputedStyle(e).backgroundColor])"
    )
    assert len(colors) == 7
    for name, color in colors:
        r, g, b = (
            int(v) for v in color.replace("rgb(", "").replace(")", "").split(",")[:3]
        )
        assert max(r, g, b) < 0x40, (name, color)
    plate = dict(colors)["smart-group smart-group-support"]
    assert plate == "rgb(40, 24, 8)"
    assert foreign == []
    page.context.close()


def test_keyboard_reaches_chips_then_watch_before_groups(
    browser: Browser, base_url: str
) -> None:
    page, foreign = open_page(browser, base_url, (390, 844))
    seen: list[str] = []
    for _ in range(30):
        page.keyboard.press("Tab")
        info = page.evaluate(
            "(() => { const e = document.activeElement;"
            " const s = getComputedStyle(e);"
            " return [e.getAttribute('href') || '', s.outlineStyle,"
            " s.outlineWidth]; })()"
        )
        seen.append(info[0])
        if info[0] == "#group-blog":
            assert info[1] != "none" and info[2] != "0px", info
            break
    assert seen.index("https://example.com/watch/1") < seen.index("#group-follow")
    assert seen.index("#group-follow") < seen.index("#group-blog")
    assert foreign == []
    page.context.close()
