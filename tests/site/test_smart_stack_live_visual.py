# SPDX-License-Identifier: Apache-2.0
"""The Watch badges of the smart stack in a real browser (#673).

The page is served from a local port only; ``/e/live`` is answered by the test (a
stub, no Worker). Nothing leaves localhost: any other origin is recorded and
aborted, and every test asserts none was seen. All data is synthetic.
"""

from __future__ import annotations

import functools
import os
import re
import threading
import time
from collections.abc import Iterator
from datetime import date

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from test_live_client_visual import (  # reuse the harness: stub route, guard, doc()
    HANG,
    Reply,
    _Quiet,
    _Server,
    doc,
    open_page,
)
from test_smart_stack_live import raw

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual
TODAY = date(2027, 3, 10)
SIZES = [(1920, 930), (393, 852), (320, 640)]


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        binary = os.environ.get("RA_CHROMIUM")
        instance = p.chromium.launch(
            args=["--no-sandbox"], executable_path=binary or None
        )
        yield instance
        instance.close()


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    data = raw()
    for link in data["tabs"][0]["blocks"]:
        link["title"] = "Watch on " + str(link["icon"]).title()
    out = tmp_path_factory.mktemp("stacklive") / "out"
    build_site(SiteConfig.model_validate(data), out, today=TODAY)
    handler = functools.partial(_Quiet, directory=str(out))
    server = _Server(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def badges(page: Page) -> dict[str, tuple[str, bool]]:
    return {
        el.get_attribute("data-watch-badge") or "": (
            el.text_content() or "",
            el.is_visible(),
        )
        for el in page.query_selector_all("[data-watch-badge]")
    }


def region(page: Page) -> str:
    return page.text_content("[data-watch-status]") or ""


def shape(page: Page, platform: str) -> str:
    """One badge's markup with its platform name blanked."""
    html = page.eval_on_selector(
        f'a:has([data-watch-badge="{platform}"])', "e => e.outerHTML"
    )
    html = re.sub(r'(href|ping|data-link-id)="[^"]*"', r'\1="X"', html)
    return re.sub(r"twitch|kick", "P", html, flags=re.IGNORECASE)


def test_live_and_offline_show_as_words_for_both_platforms(
    browser: Browser,
    base_url: str,
) -> None:
    for kick, twitch in ((True, False), (False, True), (True, True), (False, False)):
        site = open_page(browser, base_url, SIZES[0], (200, doc(kick, twitch)), path="")
        assert badges(site.page) == {
            "twitch": ("LIVE" if twitch else "OFFLINE", True),
            "kick": ("LIVE" if kick else "OFFLINE", True),
        }
        assert site.foreign == []
        assert len(site.hits) == 1
        site.page.context.close()


def test_swapping_the_data_swaps_the_words_and_the_markup_is_the_same(
    browser: Browser,
    base_url: str,
) -> None:
    a = open_page(browser, base_url, SIZES[0], (200, doc(True, False)), path="")
    b = open_page(browser, base_url, SIZES[0], (200, doc(False, True)), path="")
    assert badges(a.page)["kick"] == badges(b.page)["twitch"]
    assert badges(a.page)["twitch"] == badges(b.page)["kick"]
    # Platform names blanked, the same data gives the same markup on either side.
    assert shape(a.page, "kick") == shape(b.page, "twitch")
    assert shape(a.page, "twitch") == shape(b.page, "kick")

    def words_blank(text: str) -> str:
        return re.sub(r"(LIVE|OFFLINE)</span>", "W</span>", text)

    for site in (a, b):
        assert words_blank(shape(site.page, "kick")) == words_blank(
            shape(site.page, "twitch")
        )
    for site in (a, b):
        assert site.foreign == []
        site.page.context.close()


def test_one_polite_region_says_it_once_and_links_do_not_announce(
    browser: Browser,
    base_url: str,
) -> None:
    site = open_page(browser, base_url, SIZES[0], (200, doc(True, False)), path="")
    assert region(site.page) == "Kick: live. Twitch: offline."
    assert site.page.locator("[aria-live]").count() == 1
    assert site.page.locator("a[aria-live], a [aria-live]").count() == 0
    site.page.context.close()


@pytest.mark.parametrize(
    "reply",
    [
        (500, "boom"),
        (404, "not found"),
        (200, "not json"),
        (200, doc(True, True, as_of=time.time() - 4000)),
        (200, doc(None, None)),
        None,
        HANG,
    ],
    ids=["500", "404", "garbage", "stale", "unknown", "aborted", "hang"],
)
def test_no_data_means_no_badge_and_no_error_text(
    browser: Browser,
    base_url: str,
    reply: Reply,
) -> None:
    site = open_page(browser, base_url, SIZES[0], reply, path="", wait=reply != HANG)
    if reply == HANG:
        site.page.wait_for_timeout(300)
    assert badges(site.page) == {"twitch": ("", False), "kick": ("", False)}
    assert region(site.page) == ""
    body = site.page.inner_text("body")
    for word in ("error", "Error", "unavailable", "failed", "LIVE", "OFFLINE"):
        assert word not in body.replace("Live Friday", ""), word
    assert site.page.locator(".smart-watch-link").count() == 2
    assert site.foreign == []
    site.page.context.close()


def test_a_later_failure_clears_a_badge_it_showed(
    browser: Browser,
    base_url: str,
) -> None:
    answers = [(200, doc(True, False)), (500, "boom")]
    site = open_page(
        browser,
        base_url,
        SIZES[0],
        lambda: answers.pop(0) if len(answers) > 1 else answers[0],
        path="",
        clock=True,
    )
    assert badges(site.page)["kick"] == ("LIVE", True)
    site.page.clock.run_for(61000)
    site.checked(2)
    assert badges(site.page) == {"twitch": ("", False), "kick": ("", False)}
    assert region(site.page) == ""
    site.page.context.close()


def test_no_script_leaves_the_static_state(browser: Browser, base_url: str) -> None:
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    page.goto(base_url)
    assert page.locator(".smart-watch-link").count() == 2
    assert page.locator("[data-watch-badge]:visible").count() == 0
    assert (
        page.locator(".smart-status-schedule").inner_text()
        == "Next stream: November 13"
    )
    context.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_badges_fit_and_are_the_same_size_at_every_width(
    browser: Browser,
    base_url: str,
    size: tuple[int, int],
) -> None:
    site = open_page(browser, base_url, size, (200, doc(True, False)), path="")
    boxes = [
        site.page.locator(f'[data-watch-badge="{p}"]').bounding_box()
        for p in ("twitch", "kick")
    ]
    assert all(boxes)
    assert abs(boxes[0]["height"] - boxes[1]["height"]) < 1  # type: ignore[index]
    overflow = site.page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 0
    site.page.context.close()


def test_badge_text_contrast_and_no_motion(browser: Browser, base_url: str) -> None:
    site = open_page(browser, base_url, SIZES[0], (200, doc(True, False)), path="")
    style = site.page.eval_on_selector(
        '[data-watch-badge="kick"]',
        "e => { const s = getComputedStyle(e); return [s.color, s.backgroundColor,"
        " s.animationName, s.transitionDuration, s.fontSize]; }",
    )
    assert style[2] == "none"
    assert style[3] in ("0s", "")

    def lum(css: str) -> float:
        nums = [int(x) / 255 for x in re.findall(r"\d+", css)[:3]]
        lin = [
            c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in nums
        ]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    hi, lo = sorted((lum(style[0]), lum(style[1])))[::-1]
    assert (hi + 0.05) / (lo + 0.05) >= 4.5
    site.page.context.close()
