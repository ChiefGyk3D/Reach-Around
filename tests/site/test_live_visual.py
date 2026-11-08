# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the live page. Run with ``pytest --run-visual``.

Set ``RA_CHROMIUM`` to a headless browser launcher (see the project's quiet
launcher). The page is served from a local port only: every request to any other
host is aborted and recorded, and the test asserts there were none. Nothing
here clicks a link or navigates away from the local page. All data is synthetic.

The live state comes from the page's own script (slice 3): ``/e/live`` is
answered here with a document that says both channels are live, or with a 404
(no Worker), which leaves the static degraded page.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

TODAY = date(2027, 3, 10)
DESKTOP = (1920, 930)
WIDE = (2560, 1290)
PHONE = (393, 852)


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def _config() -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "main",
                    "title": "Main",
                    "blocks": [{"id": "one", "title": "One", "url": "https://e.com/"}],
                },
                {
                    "id": "watch",
                    "title": "Live",
                    "blocks": [
                        {
                            "id": "live",
                            "players": [
                                {"platform": "kick", "channel": "example_kick"},
                                {"platform": "twitch", "channel": "example_twitch"},
                            ],
                            "also_on": [
                                {
                                    "platform": "youtube",
                                    "url": "https://www.youtube.com/@example",
                                },
                                {
                                    "platform": "tiktok",
                                    "url": "https://www.tiktok.com/@example",
                                },
                                {
                                    "platform": "instagram",
                                    "url": "https://www.instagram.com/example",
                                },
                            ],
                            "vods": {
                                "youtube": "https://www.youtube.com/@example/streams",
                                "kick": "https://kick.com/example_kick/videos",
                                "twitch": "https://www.twitch.tv/example_twitch/videos",
                            },
                        }
                    ],
                },
            ],
        }
    )


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("live") / "out"
    build_site(_config(), out, today=TODAY)
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


def _open(
    browser: Browser, base: str, size: tuple[int, int], *, live: bool
) -> tuple[Page, list[str]]:
    """Open the live page; ``live`` shows the live state (what the script will do)."""
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        reduced_motion="reduce",
        color_scheme="dark",
    )
    foreign: list[str] = []
    local_host = urlsplit(base).netloc

    both_live = json.dumps(
        {
            "v": 1,
            "as_of": int(time.time()),
            "ttl": 30,
            "kick": {"live": True, "since": int(time.time()) - 60},
            "twitch": {"live": True, "since": int(time.time()) - 60},
        }
    )

    def guard(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.netloc != local_host:
            foreign.append(route.request.url)
            route.abort()
        elif url.path == "/e/live":
            if live:
                route.fulfill(
                    status=200, body=both_live, content_type="application/json"
                )
            else:
                route.fulfill(status=404, body="not found")
        else:
            route.continue_()

    context.route("**/*", guard)
    page = context.new_page()
    page.goto(base + "watch/")
    page.wait_for_function(
        "Number(document.documentElement.dataset.liveChecks || 0) >= 1"
    )
    return page, foreign


def _boxes(page: Page, selector: str) -> list[Any]:
    out: list[Any] = []
    for i in range(page.locator(selector).count()):
        box = page.locator(selector).nth(i).bounding_box()
        assert box is not None
        out.append(box)
    return out


def _scroll(page: Page) -> tuple[int, int, int, int]:
    """Page width, client width, page height, window height (all whole numbers)."""
    values: list[int] = page.evaluate(
        "[document.documentElement.scrollWidth,"
        " document.documentElement.clientWidth,"
        " document.documentElement.scrollHeight, window.innerHeight]"
    )
    return values[0], values[1], values[2], values[3]


def _check_equal_players(boxes: list[Any]) -> None:
    assert len(boxes) == 2
    first, second = boxes
    assert abs(first["width"] - second["width"]) <= 1
    assert abs(first["height"] - second["height"]) <= 1
    assert abs(first["width"] / first["height"] - 16 / 9) < 0.02


def test_desktop_1920_two_equal_players_side_by_side_without_scrolling(
    browser: Browser, base_url: str
) -> None:
    page, foreign = _open(browser, base_url, DESKTOP, live=True)
    width, client_width, height, inner = _scroll(page)
    boxes = _boxes(page, ".live-facade")
    _check_equal_players(boxes)
    assert 900 <= boxes[0]["width"] <= 940, boxes[0]
    assert boxes[0]["y"] == boxes[1]["y"], "side by side"
    assert boxes[0]["x"] < boxes[1]["x"]
    assert width <= client_width, "no sideways scroll"
    assert height <= inner, f"no vertical scroll: {height} > {inner}"
    assert foreign == []
    page.context.close()


def test_wide_2560_players_are_capped_near_1100(
    browser: Browser, base_url: str
) -> None:
    page, foreign = _open(browser, base_url, WIDE, live=True)
    boxes = _boxes(page, ".live-facade")
    _check_equal_players(boxes)
    assert 1050 <= boxes[0]["width"] <= 1100.5, boxes[0]
    assert boxes[0]["y"] == boxes[1]["y"]
    width, client_width, height, inner = _scroll(page)
    assert width <= client_width
    assert height <= inner, f"no vertical scroll: {height} > {inner}"
    assert foreign == []
    page.context.close()


def test_phone_393_stacks_at_full_width_minus_two_gutters(
    browser: Browser, base_url: str
) -> None:
    page, foreign = _open(browser, base_url, PHONE, live=True)
    boxes = _boxes(page, ".live-facade")
    _check_equal_players(boxes)
    for box in boxes:
        assert abs(box["x"] - 16) <= 0.5, box
        assert abs(box["width"] - (PHONE[0] - 32)) <= 1, box
    assert boxes[0]["x"] == boxes[1]["x"], "stacked"
    assert boxes[0]["y"] < boxes[1]["y"]
    width, client_width, _, _ = _scroll(page)
    assert width <= client_width, "no sideways scroll"
    assert foreign == []
    page.context.close()


@pytest.mark.parametrize("size", [DESKTOP, WIDE, PHONE])
def test_order_is_fixed_and_the_cards_are_equal_in_every_viewport(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, _ = _open(browser, base_url, size, live=True)
    platforms = page.locator(".live-facade").evaluate_all(
        "els => els.map(e => e.dataset.platform)"
    )
    assert platforms == ["kick", "twitch"]
    # The cards' text may wrap differently on a phone; the player boxes may not.
    cards = _boxes(page, ".live-card")
    assert abs(cards[0]["width"] - cards[1]["width"]) <= 1
    _check_equal_players(_boxes(page, ".live-facade"))
    page.context.close()


@pytest.mark.parametrize("size", [DESKTOP, WIDE, PHONE])
def test_default_state_shows_the_offline_layout_and_watch_links(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    page, foreign = _open(browser, base_url, size, live=False)
    assert page.locator(".live-facade").first.is_hidden()
    assert page.locator(".live-badge").first.is_hidden()
    watch = _boxes(page, ".live-watch-link")
    assert len(watch) == 2
    assert abs(watch[0]["width"] - watch[1]["width"]) <= 1
    assert abs(watch[0]["height"] - watch[1]["height"]) <= 1
    vods = _boxes(page, ".live-vod-link")
    assert len(vods) == 3
    assert len({round(v["width"]) for v in vods}) <= 2
    assert len({round(v["height"]) for v in vods}) == 1
    for box in [*watch, *vods]:
        assert box["height"] >= 44
    width, client_width, _, _ = _scroll(page)
    assert width <= client_width
    assert foreign == []
    page.context.close()
