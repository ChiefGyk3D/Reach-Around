# SPDX-License-Identifier: Apache-2.0
"""Browser checks for static/live.js. Run with ``pytest --run-visual``.

Run through the project's quiet launcher (``RA_CHROMIUM``). The page is served
from a local port only. ``/e/live`` is answered by the test (a stub, no Worker).
Every request to any other host is aborted and recorded; before a click the
test asserts there were none, and after a click it asserts the one aborted
request is exactly the expected allowlisted player URL. Nothing navigates away
from the local page and no link is clicked. All data is synthetic.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
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
SIZES = [DESKTOP, WIDE, PHONE]

Reply = tuple[int, str] | None  # (status, body) or None to abort the request
HANG: Reply = (0, "")  # never answered: the request hangs until the script gives up
SMALL = (320, 640)


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
    out = tmp_path_factory.mktemp("liveclient") / "out"
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


def doc(
    kick: bool | None,
    twitch: bool | None,
    *,
    as_of: float | None = None,
    **extra: Any,
) -> str:
    """A status document as the Worker serves it."""
    now = int(time.time()) if as_of is None else int(as_of)

    def side(live: bool | None) -> dict[str, Any]:
        return {"live": live, "since": now - 600 if live else None}

    body: dict[str, Any] = {
        "v": 1,
        "as_of": now,
        "ttl": 30,
        "kick": side(kick),
        "twitch": side(twitch),
    }
    body.update(extra)
    return json.dumps(body)


class Site:
    """One open page with its recorded foreign requests and status replies."""

    def __init__(self, page: Page, foreign: list[str], hits: list[str]) -> None:
        self.page = page
        self.foreign = foreign
        self.hits = hits

    def checked(self, count: int = 1) -> None:
        self.page.wait_for_function(
            f"Number(document.documentElement.dataset.liveChecks || 0) >= {count}"
        )


def open_page(
    browser: Browser,
    base: str,
    size: tuple[int, int],
    reply: Reply | Callable[[], Reply],
    *,
    path: str = "watch/",
    clock: bool = False,
    wait: bool = True,
) -> Site:
    context = browser.new_context(
        viewport={"width": size[0], "height": size[1]},
        reduced_motion="reduce",
        color_scheme="dark",
    )
    foreign: list[str] = []
    hits: list[str] = []
    local_host = urlsplit(base).netloc

    def guard(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.netloc != local_host:
            foreign.append(route.request.url)
            route.abort()
        elif url.path == "/e/live":
            hits.append(route.request.url)
            answer = reply() if callable(reply) else reply
            if answer == HANG:
                return
            if answer is None:
                route.abort()
            else:
                route.fulfill(
                    status=answer[0],
                    body=answer[1],
                    content_type="application/json",
                )
        else:
            route.continue_()

    context.route("**/*", guard)
    page = context.new_page()
    if clock:
        page.clock.install(time=time.time())
    page.goto(base + path)
    site = Site(page, foreign, hits)
    if wait:
        site.checked(1)
    return site


def _visible(page: Page, selector: str) -> bool:
    return page.locator(selector).first.is_visible()


def _panels(page: Page) -> dict[str, bool]:
    return {
        name: page.locator(f"[data-live-panel={name}]").is_visible()
        for name in ("live", "offline", "degraded")
    }


def _badge(page: Page) -> bool:
    return page.locator("[data-live-badge]").first.is_visible()


def _scroll(page: Page) -> tuple[int, int, int, int]:
    values: list[int] = page.evaluate(
        "[document.documentElement.scrollWidth,"
        " document.documentElement.clientWidth,"
        " document.documentElement.scrollHeight, window.innerHeight]"
    )
    return values[0], values[1], values[2], values[3]


def _shown_cards(page: Page) -> list[dict[str, Any]]:
    """Per card in DOM order: the card box, the visible slot box and the 16:9 box."""
    out: list[dict[str, Any]] = page.evaluate(
        """() => [...document.querySelectorAll('.live-card')].map(card => {
          const slot = card.querySelector('[data-live-slot]:not([hidden])');
          const main = slot.querySelector('.live-facade, .live-notlive, .live-frame');
          const r = e => { const b = e.getBoundingClientRect();
            return {x: b.x, y: b.y, width: b.width, height: b.height}; };
          return {slot: slot.dataset.liveSlot, card: r(card), box: r(slot),
                  main: r(main)};
        })"""
    )
    return out


def _equal(a: dict[str, Any], b: dict[str, Any]) -> None:
    for key in ("width", "height"):
        assert abs(a[key] - b[key]) <= 1.01, (key, a, b)


# --- states --------------------------------------------------------------------


@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(
    ("kick", "twitch"), [(True, False), (False, True), (True, True), (True, None)]
)
def test_live_shows_the_live_panel_and_the_badge_with_equal_cards(
    browser: Browser,
    base_url: str,
    size: tuple[int, int],
    kick: bool | None,
    twitch: bool | None,
) -> None:
    site = open_page(browser, base_url, size, (200, doc(kick, twitch)))
    page = site.page
    assert _panels(page) == {"live": True, "offline": False, "degraded": False}
    assert _badge(page)
    assert page.locator("[data-live]").get_attribute("data-live-state") == "live"
    cards = _shown_cards(page)
    assert len(cards) == 2
    slots = [c["slot"] for c in cards]
    assert slots == [
        "player" if kick else "offline",
        "player" if twitch is True else "offline",
    ]
    _equal(cards[0]["card"], cards[1]["card"])
    _equal(cards[0]["box"], cards[1]["box"])
    _equal(cards[0]["main"], cards[1]["main"])
    assert abs(cards[0]["main"]["width"] / cards[0]["main"]["height"] - 16 / 9) < 0.02
    assert site.foreign == []
    width, client_width, height, inner = _scroll(page)
    assert width <= client_width
    if size != PHONE:
        assert height <= inner, f"no vertical scroll: {height} > {inner}"
        assert abs(cards[0]["main"]["y"] - cards[1]["main"]["y"]) <= 1, "one row"
        assert cards[0]["main"]["x"] < cards[1]["main"]["x"]
    page.context.close()


@pytest.mark.parametrize("size", SIZES)
def test_the_not_live_card_has_the_text_and_its_vod_link(
    browser: Browser, base_url: str, size: tuple[int, int]
) -> None:
    site = open_page(browser, base_url, size, (200, doc(True, False)))
    page = site.page
    card = page.locator(".live-card").nth(1)
    notlive = card.locator(".live-notlive")
    assert notlive.is_visible()
    assert "Not live right now" in notlive.inner_text()
    link = notlive.locator("a")
    assert link.get_attribute("href") == "https://www.twitch.tv/example_twitch/videos"
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert (link.bounding_box() or {"height": 0})["height"] >= 44
    assert card.locator(".live-facade").is_hidden()
    assert page.locator(".live-card").nth(0).locator(".live-facade").is_visible()
    assert site.foreign == []
    page.context.close()


@pytest.mark.parametrize(
    ("kick", "twitch"), [(True, False), (False, True), (True, True)]
)
def test_the_cards_stay_equal_on_the_smallest_phone(
    browser: Browser, base_url: str, kick: bool, twitch: bool
) -> None:
    site = open_page(browser, base_url, SMALL, (200, doc(kick, twitch)))
    cards = _shown_cards(site.page)
    _equal(cards[0]["card"], cards[1]["card"])
    _equal(cards[0]["main"], cards[1]["main"])
    width, client_width, _, _ = _scroll(site.page)
    assert width <= client_width
    site.page.context.close()


def test_the_not_live_card_content_fits_its_box(
    browser: Browser, base_url: str
) -> None:
    for size in (DESKTOP, PHONE, SMALL):
        site = open_page(browser, base_url, size, (200, doc(True, False)))
        fits = (
            site.page.locator(".live-notlive")
            .nth(1)
            .evaluate(
                "e => e.scrollHeight <= e.clientHeight + 1"
                " && e.scrollWidth <= e.clientWidth + 1"
            )
        )
        assert fits, size
        site.page.context.close()


def test_equal_boxes_whether_a_card_shows_the_player_or_the_not_live_card(
    browser: Browser, base_url: str
) -> None:
    """Same size in every state: the cards' main boxes match across all states."""
    sizes: set[tuple[int, int]] = set()
    for kick, twitch in [(True, False), (False, True), (True, True)]:
        site = open_page(browser, base_url, DESKTOP, (200, doc(kick, twitch)))
        for card in _shown_cards(site.page):
            sizes.add((round(card["main"]["width"]), round(card["main"]["height"])))
        site.page.context.close()
    assert len(sizes) == 1, sizes


def test_offline_when_both_are_not_live(browser: Browser, base_url: str) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(False, False)))
    page = site.page
    assert _panels(page) == {"live": False, "offline": True, "degraded": False}
    assert not _badge(page)
    assert page.locator("[data-live]").get_attribute("data-live-state") == "offline"
    assert page.locator(".live-vod-link").count() == 3
    assert site.foreign == []
    page.context.close()


def test_one_unknown_and_one_not_live_is_degraded_not_offline(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(False, None)))
    assert _panels(site.page) == {"live": False, "offline": True, "degraded": True}
    assert not _badge(site.page)
    site.page.context.close()


def test_kill_switch_all_unknown_is_degraded_and_never_live(
    browser: Browser, base_url: str
) -> None:
    body = json.dumps(
        {
            "v": 1,
            "as_of": None,
            "ttl": 30,
            "kick": {"live": None, "since": None},
            "twitch": {"live": None, "since": None},
        }
    )
    site = open_page(browser, base_url, DESKTOP, (200, body))
    page = site.page
    assert _panels(page) == {"live": False, "offline": True, "degraded": True}
    assert not _badge(page)
    assert page.locator(".live-watch-link").count() == 2
    assert not _visible(page, ".live-facade")
    page.context.close()


def test_a_stale_document_never_shows_live(browser: Browser, base_url: str) -> None:
    body = doc(True, True, as_of=time.time() - 1900)
    site = open_page(browser, base_url, DESKTOP, (200, body))
    assert _panels(site.page) == {"live": False, "offline": True, "degraded": True}
    assert not _badge(site.page)
    site.page.context.close()


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param((200, "{nope"), id="not-json"),
        pytest.param((200, ""), id="empty"),
        pytest.param((200, doc(True, True, title="x")), id="extra-key"),
        pytest.param((200, doc(True, True).replace('"v": 1', '"v": 2')), id="version"),
        pytest.param(
            (200, doc(True, True).replace("true", '"true"')), id="string-boolean"
        ),
        pytest.param((200, "[true, true]"), id="array"),
        pytest.param((200, doc(True, True) + " " * 5000), id="oversize"),
        pytest.param((500, doc(True, True)), id="server-error"),
        pytest.param((404, doc(True, True)), id="not-found"),
        pytest.param((204, ""), id="no-content"),
        pytest.param(None, id="network-error"),
    ],
)
def test_malformed_or_failed_replies_are_degraded_never_live(
    browser: Browser, base_url: str, reply: Reply
) -> None:
    site = open_page(browser, base_url, DESKTOP, reply)
    page = site.page
    assert _panels(page) == {"live": False, "offline": True, "degraded": True}
    assert not _badge(page)
    assert not _visible(page, ".live-facade")
    assert site.foreign == []
    page.context.close()


def test_the_page_is_complete_without_a_status_document(
    browser: Browser, base_url: str
) -> None:
    """With no Worker (404) the static degraded page stays: links and note."""
    site = open_page(browser, base_url, DESKTOP, (404, "not found"))
    page = site.page
    assert page.locator(".live-watch-link").count() == 2
    assert _visible(page, ".live-status-note")
    page.context.close()


def test_the_badge_shows_on_every_page_of_the_site(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(False, True)), path="")
    assert _badge(site.page)
    assert site.hits and site.foreign == []
    site.page.context.close()
    site = open_page(browser, base_url, DESKTOP, (200, doc(False, False)), path="")
    assert not _badge(site.page)
    site.page.context.close()


# --- polling ---------------------------------------------------------------------


def test_the_badge_follows_the_status_on_each_poll(
    browser: Browser, base_url: str
) -> None:
    state: dict[str, Reply] = {"reply": (200, doc(True, False))}
    site = open_page(browser, base_url, DESKTOP, lambda: state["reply"], clock=True)
    page = site.page
    assert _badge(page) and _panels(page)["live"]
    assert len(site.hits) == 1
    state["reply"] = (200, doc(False, False))
    page.clock.run_for(60_000)
    site.checked(2)
    assert not _badge(page)
    assert _panels(page) == {"live": False, "offline": True, "degraded": False}
    state["reply"] = (200, doc(False, True))
    page.clock.run_for(60_000)
    site.checked(3)
    assert _badge(page) and _panels(page)["live"]
    assert [c["slot"] for c in _shown_cards(page)] == ["offline", "player"]
    assert len(site.hits) == 3
    page.context.close()


def test_a_hung_poll_times_out_to_degraded(browser: Browser, base_url: str) -> None:
    state: dict[str, Reply] = {"reply": (200, doc(True, True))}
    site = open_page(browser, base_url, DESKTOP, lambda: state["reply"], clock=True)
    page = site.page
    assert _badge(page)
    state["reply"] = HANG
    page.clock.run_for(60_000)
    page.wait_for_timeout(200)
    assert _badge(page), "still waiting"
    page.clock.run_for(5_001)
    site.checked(2)
    assert not _badge(page)
    assert _panels(page)["degraded"] and not _panels(page)["live"]
    page.context.close()


def test_a_failed_poll_goes_degraded(browser: Browser, base_url: str) -> None:
    state: dict[str, Reply] = {"reply": (200, doc(True, True))}
    site = open_page(browser, base_url, DESKTOP, lambda: state["reply"], clock=True)
    page = site.page
    assert _badge(page)
    state["reply"] = (500, "boom")
    page.clock.run_for(60_000)
    site.checked(2)
    assert not _badge(page)
    assert _panels(page)["degraded"]
    page.context.close()


def test_polling_pauses_while_hidden_and_resumes_when_visible(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(True, False)), clock=True)
    page = site.page
    assert len(site.hits) == 1
    set_hidden = """(hidden) => {
      Object.defineProperty(document, 'hidden',
        {get: () => hidden, configurable: true});
      Object.defineProperty(document, 'visibilityState',
        {get: () => hidden ? 'hidden' : 'visible', configurable: true});
      document.dispatchEvent(new Event('visibilitychange'));
    }"""
    page.evaluate(set_hidden, True)
    page.clock.run_for(300_000)
    page.wait_for_timeout(200)
    assert len(site.hits) == 1, "no request while the tab is hidden"
    page.evaluate(set_hidden, False)
    site.checked(2)
    assert len(site.hits) == 2, "one request as soon as it is visible again"
    page.clock.run_for(60_000)
    site.checked(3)
    assert len(site.hits) == 3
    page.context.close()


def test_the_request_is_the_plain_same_origin_route_with_no_parameters(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(True, False)))
    assert site.hits == [base_url.rstrip("/") + "/e/live"]
    site.page.context.close()


# --- click to load ---------------------------------------------------------------

EXPECTED = {
    "kick": "https://player.kick.com/example_kick",
    "twitch": "https://player.twitch.tv/?channel=example_twitch&parent=127.0.0.1",
}


@pytest.mark.parametrize("platform", ["kick", "twitch"])
def test_a_click_builds_exactly_the_allowlisted_iframe(
    browser: Browser, base_url: str, platform: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(True, True)))
    page = site.page
    assert site.foreign == [], "no third-party request before a click"
    assert page.locator("iframe").count() == 0
    before = _shown_cards(page)
    page.locator(f".live-facade[data-platform={platform}]").click()
    frame = page.locator("iframe")
    assert frame.count() == 1
    assert frame.get_attribute("src") == EXPECTED[platform]
    assert frame.get_attribute("sandbox") == (
        "allow-scripts allow-same-origin allow-presentation"
    )
    assert frame.get_attribute("loading") == "lazy"
    assert frame.get_attribute("allow") == "autoplay; fullscreen; picture-in-picture"
    assert (frame.get_attribute("title") or "").strip()
    assert frame.get_attribute("referrerpolicy")
    assert page.locator(f".live-facade[data-platform={platform}]").count() == 0
    # the other card is untouched: still a facade, same layout
    other = "twitch" if platform == "kick" else "kick"
    assert page.locator(f".live-facade[data-platform={other}]").is_visible()
    after = _shown_cards(page)
    for was, now in zip(before, after, strict=True):
        _equal(was["main"], now["main"])
        _equal(was["card"], now["card"])
    page.wait_for_function(
        "window.performance.getEntriesByType('resource').length >= 0"
    )
    # the single foreign request is the iframe itself, and it was aborted
    deadline = time.time() + 5
    while not site.foreign and time.time() < deadline:
        page.wait_for_timeout(50)
    assert site.foreign == [EXPECTED[platform]]
    page.context.close()


def test_both_players_can_be_loaded_and_the_panels_stay(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(True, True)))
    page = site.page
    page.locator(".live-facade[data-platform=kick]").click()
    page.locator(".live-facade[data-platform=twitch]").click()
    srcs = page.locator("iframe").evaluate_all("els => els.map(e => e.src)")
    assert srcs == [EXPECTED["kick"], EXPECTED["twitch"]]
    assert _panels(page)["live"]
    cards = _shown_cards(page)
    _equal(cards[0]["main"], cards[1]["main"])
    page.context.close()


def test_a_bad_channel_value_loads_nothing(browser: Browser, base_url: str) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(True, True)))
    page = site.page
    for platform, value in [
        ("kick", "a b"),
        ("kick", "../x"),
        ("kick", "example_kick&x=1"),
        ("twitch", "a-b"),
        ("twitch", "example_twitch&parent=evil.test"),
        ("twitch", ""),
    ]:
        page.evaluate(
            """([p, v]) => document
              .querySelector(`.live-facade[data-platform=${p}]`)
              .setAttribute('data-channel', v)""",
            [platform, value],
        )
        page.locator(f".live-facade[data-platform={platform}]").click()
    assert page.locator("iframe").count() == 0
    page.wait_for_timeout(200)
    assert site.foreign == []
    page.context.close()


def test_an_unknown_platform_value_loads_nothing(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(True, True)))
    page = site.page
    page.evaluate(
        """() => document.querySelector('.live-facade')
          .setAttribute('data-platform', 'youtube')"""
    )
    page.locator(".live-facade").first.click()
    page.wait_for_timeout(200)
    assert page.locator("iframe").count() == 0
    assert site.foreign == []
    page.context.close()


def test_a_loaded_player_is_not_torn_down_by_a_later_poll(
    browser: Browser, base_url: str
) -> None:
    state: dict[str, Reply] = {"reply": (200, doc(True, True))}
    site = open_page(browser, base_url, DESKTOP, lambda: state["reply"], clock=True)
    page = site.page
    page.locator(".live-facade[data-platform=kick]").click()
    state["reply"] = (200, doc(False, False))
    page.clock.run_for(60_000)
    site.checked(2)
    assert page.locator("iframe").count() == 1
    assert page.locator("iframe").is_visible()
    assert not _badge(page)
    page.context.close()


def test_a_non_live_page_makes_no_player_request_ever(
    browser: Browser, base_url: str
) -> None:
    site = open_page(browser, base_url, DESKTOP, (200, doc(False, False)))
    site.page.wait_for_timeout(300)
    assert site.foreign == []
    site.page.context.close()
