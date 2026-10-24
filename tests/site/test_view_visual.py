# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the page-view beacon. Run with ``pytest --run-visual``.

Run through the quiet wrapper so no desktop session is involved
(``safe-run.sh``), which also sets ``RA_CHROMIUM``. The site is served from
127.0.0.1 and every host name is synthetic. ``navigator.sendBeacon`` is stubbed
by an init script so the calls can be read; one test uses the real one against
a recording local server.
"""

from __future__ import annotations

import functools
import os
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Route,
    sync_playwright,
)

from reach_around.config import SiteConfig
from reach_around.site.build import build_site

pytestmark = pytest.mark.visual

TODAY = date(2027, 3, 10)

# Records every call the page makes that could leave the browser or persist.
PROBE = """
window.__beacons = [];
window.__other = [];
window.__writes = [];
navigator.sendBeacon = function (url, data) {
  const form = data instanceof URLSearchParams;
  window.__beacons.push({
    url: String(url),
    body: form ? data.toString() : String(data),
    form: form,
  });
  return true;
};
window.fetch = function () {
  window.__other.push('fetch');
  return Promise.reject(new Error('blocked'));
};
const xhrOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function () {
  window.__other.push('xhr');
  return xhrOpen.apply(this, arguments);
};
for (const name of ['setItem', 'removeItem', 'clear']) {
  Storage.prototype[name] = function () { window.__writes.push(name); };
}
const cookie = Object.getOwnPropertyDescriptor(Document.prototype, 'cookie');
Object.defineProperty(Document.prototype, 'cookie', {
  get: function () { return cookie.get.call(this); },
  set: function (v) { window.__writes.push('cookie'); },
  configurable: true,
});
"""


def build_config() -> SiteConfig:
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "Example Creator"},
            "tabs": [
                {
                    "id": "start",
                    "title": "Start",
                    "blocks": [
                        {"id": "one", "title": "One", "url": "https://example.com/1"}
                    ],
                },
                {
                    "id": "more",
                    "title": "More",
                    "blocks": [
                        {"id": "two", "title": "Two", "url": "https://example.com/2"}
                    ],
                },
            ],
            "analytics": {"page_views": True, "route": "/stats/pv/"},
        }
    )


class Handler(SimpleHTTPRequestHandler):
    posts: list[tuple[str, str, str]] = []

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        Handler.posts.append((self.path, self.headers.get("Content-Type", ""), body))
        self.send_response(204)
        self.end_headers()


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    out = tmp_path_factory.mktemp("views") / "out"
    build_site(build_config(), out, today=TODAY)
    handler = functools.partial(Handler, directory=str(out))
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
    browser: Browser,
    url: str,
    *,
    width: int = 800,
    init: str = "",
    probe: bool = True,
) -> tuple[Page, BrowserContext, list[str]]:
    context = browser.new_context(viewport={"width": width, "height": 900})
    errors: list[str] = []
    page = context.new_page()
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    if probe:
        page.add_init_script(PROBE)
    if init:
        page.add_init_script(init)
    page.goto(url)
    page.wait_for_load_state("load")
    return page, context, errors


def _beacons(page: Page) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = page.evaluate("window.__beacons")
    return result


def test_one_beacon_with_the_contract_shape(browser: Browser, base_url: str) -> None:
    ref = (
        "Object.defineProperty(Document.prototype, 'referrer', {get: () =>"
        " 'https://news.example.test/a/b?token=SECRETQ#frag'});"
    )
    page, ctx, errors = _open(
        browser,
        base_url + "?utm_source=Bluesky&email=a@b.test",
        init=ref,
    )
    calls = _beacons(page)
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "/stats/pv/start"
    assert call["form"] is True
    assert call["body"] == "r=news.example.test&u=bluesky&w=l"
    blob = call["url"] + call["body"]
    for secret in ("SECRETQ", "/a/b", "a@b.test", "email", "127.0.0.1", "frag"):
        assert secret not in blob
    assert errors == []
    ctx.close()


def test_a_second_tab_page_uses_its_own_id(browser: Browser, base_url: str) -> None:
    page, ctx, _ = _open(browser, base_url + "more/")
    calls = _beacons(page)
    assert [c["url"] for c in calls] == ["/stats/pv/more"]
    assert calls[0]["body"] == "w=l"
    ctx.close()


@pytest.mark.parametrize(
    ("width", "letter"),
    [(479, "s"), (480, "m"), (767, "m"), (768, "l"), (1199, "l"), (1200, "x")],
)
def test_width_letter_edges(
    browser: Browser, base_url: str, width: int, letter: str
) -> None:
    page, ctx, _ = _open(browser, base_url, width=width)
    assert [c["body"] for c in _beacons(page)] == [f"w={letter}"]
    ctx.close()


def test_bad_utm_and_ip_literal_referrer_are_omitted(
    browser: Browser, base_url: str
) -> None:
    ref = "Object.defineProperty(Document.prototype, 'referrer', {get: () => 'http://[::1]:8080/x'});"
    page, ctx, _ = _open(browser, base_url + "?utm_source=a_b", init=ref)
    assert [c["body"] for c in _beacons(page)] == ["w=l"]
    ctx.close()


@pytest.mark.parametrize("label", ["a_b", "x" * 31, "x@y", ""])
def test_bad_utm_labels_are_omitted(
    browser: Browser, base_url: str, label: str
) -> None:
    page, ctx, _ = _open(browser, base_url + "?utm_source=" + label)
    assert [c["body"] for c in _beacons(page)] == ["w=l"]
    ctx.close()


def test_a_thirty_character_utm_label_is_sent(browser: Browser, base_url: str) -> None:
    page, ctx, _ = _open(browser, base_url + "?utm_source=" + "x" * 30)
    assert [c["body"] for c in _beacons(page)] == ["u=" + "x" * 30 + "&w=l"]
    ctx.close()


GPC = (
    "Object.defineProperty(Navigator.prototype, 'globalPrivacyControl', "
    "{get: () => true});"
)


def _dnt(value: str) -> str:
    return (
        "Object.defineProperty(Navigator.prototype, 'doNotTrack', "
        f"{{get: () => '{value}'}});"
    )


def test_global_privacy_control_sends_nothing(browser: Browser, base_url: str) -> None:
    page, ctx, _ = _open(browser, base_url, init=GPC)
    assert _beacons(page) == []
    ctx.close()


@pytest.mark.parametrize("value", ["1", "yes"])
def test_do_not_track_sends_nothing(
    browser: Browser, base_url: str, value: str
) -> None:
    page, ctx, _ = _open(browser, base_url, init=_dnt(value))
    assert _beacons(page) == []
    ctx.close()


def test_do_not_track_zero_still_sends(browser: Browser, base_url: str) -> None:
    page, ctx, _ = _open(browser, base_url, init=_dnt("0"))
    assert len(_beacons(page)) == 1
    ctx.close()


def test_window_and_ms_do_not_track_send_nothing(
    browser: Browser, base_url: str
) -> None:
    for init in (
        "window.doNotTrack = '1';",
        "Object.defineProperty(Navigator.prototype, 'msDoNotTrack', {get: () => '1'});",
    ):
        page, ctx, _ = _open(browser, base_url, init=init)
        assert _beacons(page) == []
        ctx.close()


def test_prerendering_waits_for_activation_then_sends_once(
    browser: Browser, base_url: str
) -> None:
    init = (
        "Object.defineProperty(Document.prototype, 'prerendering', "
        "{get: () => !window.__active, configurable: true});"
    )
    page, ctx, _ = _open(browser, base_url, init=init)
    assert _beacons(page) == []
    page.evaluate(
        "window.__active = true;"
        " document.dispatchEvent(new Event('prerenderingchange'));"
    )
    assert len(_beacons(page)) == 1
    page.evaluate("document.dispatchEvent(new Event('prerenderingchange'));")
    assert len(_beacons(page)) == 1
    ctx.close()


def test_hidden_at_load_sends_on_first_visible_and_once(
    browser: Browser, base_url: str
) -> None:
    init = (
        "Object.defineProperty(Document.prototype, 'visibilityState', "
        "{get: () => window.__state || 'hidden', configurable: true});"
    )
    page, ctx, _ = _open(browser, base_url, init=init)
    assert _beacons(page) == []
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'));")
    assert _beacons(page) == []
    page.evaluate(
        "window.__state = 'visible';"
        " document.dispatchEvent(new Event('visibilitychange'));"
    )
    assert len(_beacons(page)) == 1
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'));")
    assert len(_beacons(page)) == 1
    ctx.close()


def test_without_send_beacon_nothing_else_is_tried(
    browser: Browser, base_url: str
) -> None:
    requests: list[str] = []
    context = browser.new_context()
    page = context.new_page()
    page.on("request", lambda r: requests.append(r.method + " " + r.url))
    page.add_init_script(PROBE)
    page.add_init_script("navigator.sendBeacon = undefined;")
    page.goto(base_url)
    page.wait_for_load_state("load")
    assert page.evaluate("window.__other") == []
    assert [r for r in requests if "/stats/pv/" in r] == []
    assert [r for r in requests if r.startswith("POST")] == []
    context.close()


def test_a_throwing_send_beacon_is_swallowed(browser: Browser, base_url: str) -> None:
    init = "navigator.sendBeacon = function () { throw new Error('boom'); };"
    context = browser.new_context()
    errors: list[str] = []
    page = context.new_page()
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.add_init_script(init)
    page.goto(base_url)
    page.wait_for_load_state("load")
    assert errors == []
    context.close()


def test_no_storage_writes_no_cookies_no_other_requests(
    browser: Browser, base_url: str
) -> None:
    page, ctx, _ = _open(browser, base_url)
    assert len(_beacons(page)) == 1
    assert page.evaluate("window.__writes") == []
    assert page.evaluate("window.__other") == []
    assert ctx.cookies() == []
    assert page.evaluate("document.cookie") == ""
    assert page.evaluate("localStorage.length") == 0
    assert page.evaluate("sessionStorage.length") == 0
    assert page.evaluate("indexedDB.databases().then(d => d.length)") == 0
    ctx.close()


def test_the_real_beacon_is_a_small_form_post(browser: Browser, base_url: str) -> None:
    Handler.posts.clear()
    context = browser.new_context(viewport={"width": 1300, "height": 900})
    page = context.new_page()
    page.goto(base_url + "more/")
    page.wait_for_load_state("load")
    for _ in range(50):
        if Handler.posts:
            break
        page.wait_for_timeout(100)
    assert Handler.posts == [
        (
            "/stats/pv/more",
            "application/x-www-form-urlencoded;charset=UTF-8",
            "w=x",
        )
    ]
    assert context.cookies() == []
    context.close()


def _tampered(browser: Browser, url: str, old: str, new: str) -> Page:
    """Load the page with one attribute rewritten in the HTML on the way in."""
    context = browser.new_context(viewport={"width": 800, "height": 900})
    page = context.new_page()
    page.add_init_script(PROBE)

    def rewrite(route: Route) -> None:
        response = route.fetch()
        html = response.text()
        assert old in html
        route.fulfill(response=response, body=html.replace(old, new, 1))

    page.route(url, rewrite)
    page.goto(url)
    page.wait_for_load_state("load")
    return page


@pytest.mark.parametrize("bad", ["Bad Id", "../x", "UPPER", "a" * 41, ""])
def test_a_bad_page_id_sends_nothing(browser: Browser, base_url: str, bad: str) -> None:
    page = _tampered(browser, base_url, 'data-page-id="start"', f'data-page-id="{bad}"')
    assert _beacons(page) == []
    page.context.close()


def test_a_missing_page_id_sends_nothing(browser: Browser, base_url: str) -> None:
    page = _tampered(browser, base_url, 'data-page-id="start"', "")
    assert _beacons(page) == []
    page.context.close()


@pytest.mark.parametrize(
    "bad",
    ["https://evil.test/", "//evil.test/", "/a/../", "/e/v", "e/v/", "/" + "a/" * 40],
)
def test_a_bad_route_sends_nothing(browser: Browser, base_url: str, bad: str) -> None:
    page = _tampered(
        browser, base_url, 'data-view-route="/stats/pv/"', f'data-view-route="{bad}"'
    )
    assert _beacons(page) == []
    page.context.close()
