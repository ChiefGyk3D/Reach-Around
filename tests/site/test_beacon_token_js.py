# SPDX-License-Identifier: Apache-2.0
"""The beacon token in the page scripts and in the build (issue #610).

``click.js`` and ``view.js`` ask the Worker for a short-lived token once per
page, keep it in page memory only, and add it to the click ping or the view
beacon. They must send nothing for a visitor with Global Privacy Control or
Do Not Track, never delay a link, and never break when the token request
fails. The build only turns this on with ``analytics.beacon_token``.

The scripts run under Node against a small stub of the page.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from beaconscan import SHARED_END, SHARED_START, network_problems, shared_block
from reach_around.config import SiteConfig
from reach_around.site.build import build_site

TOKEN = "b1." + "A" * 60 + "." + "B" * 43


def _script(name: str) -> str:
    return (resources.files("reach_around.site") / "static" / name).read_text(
        encoding="utf-8"
    )


# ---------------------------------------------------------------- source scan


@pytest.mark.parametrize("name", ["click.js", "view.js"])
def test_the_only_request_is_one_same_origin_token_fetch(name: str) -> None:
    text = _script(name)
    assert "MINT_ROUTE" in text and len(text) > 500  # loose: the file was read
    assert network_problems(text) == []


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "a second fetch",
            lambda t: t + '\nfetch(MINT_ROUTE, {method: "POST"}).then(1);',
        ),
        (
            "a cross-origin fetch",
            lambda t: t.replace(
                "fetch(MINT_ROUTE,", 'fetch("https://other.example.test/e/t",', 1
            ),
        ),
        (
            "a protocol-relative URL",
            lambda t: t.replace(
                "fetch(MINT_ROUTE,", 'fetch("//other.example.test/e/t",', 1
            ),
        ),
        ("a fetch of another path", lambda t: t.replace('"/e/t";', '"/e/x";', 1)),
        ("an XMLHttpRequest", lambda t: t + "\nvar x = new XMLHttpRequest();"),
        (
            "a WebSocket",
            lambda t: t + '\nvar w = new WebSocket("wss://x.example.test");',
        ),
        (
            "a beacon to another origin",
            lambda t: t + '\nnavigator.sendBeacon("https://other.example.test/c");',
        ),
        (
            "a fetch that sends cookies",
            lambda t: t.replace('credentials: "omit"', 'credentials: "include"', 1),
        ),
        (
            "a fetch that is not a POST",
            lambda t: t.replace('method: "POST"', 'method: "GET"', 1),
        ),
    ],
)
@pytest.mark.parametrize("name", ["click.js", "view.js"])
def test_the_scan_bites(name: str, label: str, mutate: Any) -> None:
    text = _script(name)
    mutated = mutate(text)
    assert mutated != text, label
    assert network_problems(mutated) != [], label


def test_the_shared_token_block_is_identical_in_both_scripts() -> None:
    click, view = shared_block(_script("click.js")), shared_block(_script("view.js"))
    assert click is not None and view is not None
    assert SHARED_START in click and SHARED_END in view
    assert click == view
    assert len(click) > 300
    assert shared_block("no markers") is None


def test_neither_script_stores_the_token() -> None:
    for name in ("click.js", "view.js"):
        text = _script(name)
        for word in (
            "localStorage",
            "sessionStorage",
            "document.cookie",
            "indexedDB",
            "caches",
        ):
            assert word not in text, (name, word)


# ---------------------------------------------------------------- Node driver

DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[3]);
const sent = [];
const fetches = [];
const timers = [];
const result = { sent, fetches, prevented: false, ping: null, clickReturn: "none" };
let clickHandler = null;

function fakeFetch(url, opts) {
  fetches.push({ url, opts });
  if (cfg.fetch === "reject") return Promise.reject(new Error("down"));
  if (cfg.fetch === "hang") return new Promise(() => {});
  if (cfg.fetch === "throw") throw new Error("sync");
  const body = {
    ok: { t: cfg.token, ttl: 900 },
    badtoken: { t: "not a token <script>", ttl: 900 },
    longtoken: { t: "b1." + "A".repeat(400) + ".B", ttl: 900 },
    nottl: { t: cfg.token },
    shortttl: { t: cfg.token, ttl: 5 },
    notstring: { t: 5, ttl: 900 },
  }[cfg.fetch];
  const answer = (status, json) => Promise.resolve({ status, json });
  if (cfg.fetch === "status500") return answer(500, async () => ({}));
  if (cfg.fetch === "badjson") {
    return answer(200, async () => { throw new Error("json"); });
  }
  return answer(200, async () => body);
}

const node = {
  nodeType: 1, parentNode: null,
  hasAttribute: (n) => n === "data-link-id" || (n === "ping" && cfg.native),
  getAttribute: (n) => (n === "data-link-id" ? "twitch" : null),
  setAttribute: (n, v) => { if (n === "ping") result.ping = v; },
};
const attrs = {
  "data-landing": cfg.landing || null,
  "data-page-id": cfg.script === "view" ? "home" : null,
  "data-view-route": cfg.script === "view" ? "/e/v/" : null,
  "data-token-route": cfg.tokenRoute,
};
const nav = {
  sendBeacon: (u, b) => { sent.push(u); return true; },
  globalPrivacyControl: cfg.gpc ? true : undefined,
  doNotTrack: cfg.dnt === null || cfg.dnt === undefined ? null : cfg.dnt,
};
const doc = {
  body: { getAttribute: (n) => (attrs[n] === undefined ? null : attrs[n]) },
  referrer: "", visibilityState: "visible", prerendering: false,
  createElement: () => (cfg.native ? { ping: "" } : {}),
  addEventListener: (type, fn) => { if (type === "click") clickHandler = fn; },
};
const win = { innerWidth: 1300 };
const sandbox = {
  URLSearchParams, URL, Promise, JSON, Date, Math,
  location: { search: cfg.search || "" },
  navigator: nav, document: doc, window: win,
  setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  clearTimeout: () => {},
  requestIdleCallback: cfg.idle
    ? (fn) => { timers.push({ fn, ms: 0 }); return 1; }
    : undefined,
  localStorage: new Proxy({}, { get() { throw new Error("storage"); } }),
};
if (cfg.fetch !== "none") sandbox.fetch = fakeFetch;
win.fetch = sandbox.fetch;
vm.createContext(sandbox);

const tick = async () => {
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
};
(async () => {
  vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
  for (const step of cfg.steps) {
    if (step === "flush") await tick();
    else if (step === "timers") {
      const due = timers.splice(0).sort((a, b) => a.ms - b.ms);
      for (const t of due) t.fn();
      await tick();
    } else if (step === "click") {
      const ev = { target: node, preventDefault: () => { result.prevented = true; } };
      result.clickReturn = String(clickHandler(ev));
    } else if (step === "gpc") nav.globalPrivacyControl = true;
    else if (step === "advance") {
      const later = () => Date.now() + cfg.advanceMs;
      sandbox.Date = Object.assign(function () {}, { now: later });
    }
  }
  result.pendingTimers = timers.length;
  process.stdout.write(JSON.stringify(result));
})().catch((e) => { process.stderr.write(String(e && e.stack)); process.exit(1); });
"""


def _node() -> str | None:
    found = shutil.which("node")
    if found is None and os.environ.get("CI"):
        pytest.fail("node is required in CI for the token script tests")
    return found


def _run(script: str, tmp_path: Path, **over: Any) -> dict[str, Any]:
    node = _node()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    cfg: dict[str, Any] = {
        "script": script.removesuffix(".js"),
        "tokenRoute": "/e/t",
        "fetch": "ok",
        "token": TOKEN,
        "steps": ["flush", "timers", "click"],
        "native": False,
        "idle": True,
        "landing": None,
        "dnt": None,
        "gpc": False,
    }
    cfg.update(over)
    path = tmp_path / script
    path.write_text(_script(script), encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    done = subprocess.run(  # noqa: S603 - fixed argv: node, a staged driver and a copy of our script
        [node, str(driver), str(path), json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return cast("dict[str, Any]", json.loads(done.stdout))


# ------------------------------------------------------------------ click.js


def test_click_without_the_attribute_never_asks_for_a_token(tmp_path: Path) -> None:
    out = _run("click.js", tmp_path, tokenRoute=None, landing="home")
    assert out["fetches"] == []
    assert out["sent"] == ["/e/c/twitch?l=home"]


def test_click_asks_once_and_sends_the_token_after_the_labels(tmp_path: Path) -> None:
    out = _run("click.js", tmp_path, landing="home", search="?s=Bio")
    assert [f["url"] for f in out["fetches"]] == ["/e/t"]
    assert out["fetches"][0]["opts"]["method"] == "POST"
    assert out["fetches"][0]["opts"]["credentials"] == "omit"
    assert out["sent"] == [f"/e/c/twitch?s=bio&l=home&t={TOKEN}"]


def test_click_token_on_the_native_ping_path(tmp_path: Path) -> None:
    out = _run("click.js", tmp_path, native=True)
    assert out["ping"] == f"/e/c/twitch?t={TOKEN}"
    assert out["sent"] == []


def test_click_with_a_token_wait_never_delays_or_blocks_the_link(
    tmp_path: Path,
) -> None:
    # The token request never answers; the click is handled at once, without a token.
    out = _run("click.js", tmp_path, fetch="hang", steps=["click"])
    assert out["sent"] == ["/e/c/twitch"]
    assert out["prevented"] is False
    assert out["clickReturn"] == "undefined"
    assert len(out["fetches"]) <= 1


@pytest.mark.parametrize(
    "mode",
    [
        "reject",
        "throw",
        "status500",
        "badjson",
        "badtoken",
        "longtoken",
        "nottl",
        "shortttl",
        "notstring",
        "none",
    ],
)
def test_click_survives_a_failed_token_request(tmp_path: Path, mode: str) -> None:
    out = _run("click.js", tmp_path, fetch=mode)
    assert out["sent"] == ["/e/c/twitch"]
    assert out["prevented"] is False


@pytest.mark.parametrize(
    ("label", "over"),
    [
        ("global privacy control", {"gpc": True}),
        ("do not track 1", {"dnt": "1"}),
        ("do not track yes", {"dnt": " YES "}),
        ("do not track true", {"dnt": "true"}),
    ],
)
def test_click_asks_for_nothing_under_an_opt_out(
    tmp_path: Path, label: str, over: dict[str, Any]
) -> None:
    out = _run("click.js", tmp_path, **over)
    assert out["fetches"] == [], label
    assert "t=" not in "".join(out["sent"]), label


def test_click_drops_a_token_if_the_opt_out_arrives_later(tmp_path: Path) -> None:
    out = _run("click.js", tmp_path, steps=["flush", "timers", "gpc", "click"])
    assert len(out["fetches"]) == 1
    assert "t=" not in "".join(out["sent"])


def test_click_does_not_send_an_expired_token(tmp_path: Path) -> None:
    out = _run(
        "click.js",
        tmp_path,
        steps=["flush", "timers", "advance", "click"],
        advanceMs=900_000,
    )
    assert "t=" not in "".join(out["sent"])


def test_click_asks_on_first_interaction_when_nothing_ran_while_idle(
    tmp_path: Path,
) -> None:
    out = _run("click.js", tmp_path, idle=False, steps=["click", "flush", "click"])
    assert len(out["fetches"]) == 1
    assert out["sent"][0] == "/e/c/twitch"
    assert out["sent"][1].endswith(f"t={TOKEN}")


def test_click_attribute_value_other_than_the_one_route_is_ignored(
    tmp_path: Path,
) -> None:
    for value in (
        "/e/x",
        "https://other.example.test/e/t",
        "//other.example.test/e/t",
        "",
    ):
        out = _run("click.js", tmp_path, tokenRoute=value)
        assert out["fetches"] == [], value


# ------------------------------------------------------------------- view.js


def test_view_without_the_attribute_is_unchanged(tmp_path: Path) -> None:
    out = _run("view.js", tmp_path, tokenRoute=None, steps=["flush"])
    assert out["fetches"] == []
    assert out["sent"] == ["/e/v/home"]


def test_view_sends_the_token_in_the_url_after_one_request(tmp_path: Path) -> None:
    out = _run("view.js", tmp_path, steps=["flush"])
    assert [f["url"] for f in out["fetches"]] == ["/e/t"]
    assert out["sent"] == [f"/e/v/home?t={TOKEN}"]


def test_view_still_sends_once_when_the_token_request_hangs(tmp_path: Path) -> None:
    out = _run(
        "view.js", tmp_path, fetch="hang", steps=["flush", "timers", "flush", "timers"]
    )
    assert out["sent"] == ["/e/v/home"]


@pytest.mark.parametrize(
    "mode",
    [
        "reject",
        "throw",
        "status500",
        "badjson",
        "badtoken",
        "longtoken",
        "nottl",
        "none",
    ],
)
def test_view_still_sends_when_the_token_request_fails(
    tmp_path: Path, mode: str
) -> None:
    out = _run("view.js", tmp_path, fetch=mode, steps=["flush", "timers", "flush"])
    assert out["sent"] == ["/e/v/home"]


@pytest.mark.parametrize(
    "over", [{"gpc": True}, {"dnt": "1"}, {"dnt": "yes"}, {"dnt": "true"}]
)
def test_view_sends_and_asks_for_nothing_under_an_opt_out(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    out = _run("view.js", tmp_path, steps=["flush", "timers", "flush"], **over)
    assert out["fetches"] == []
    assert out["sent"] == []


def test_view_sends_nothing_if_the_opt_out_arrives_while_it_waits(
    tmp_path: Path,
) -> None:
    out = _run("view.js", tmp_path, fetch="hang", steps=["gpc", "timers", "flush"])
    assert out["sent"] == []


def test_view_sends_exactly_once(tmp_path: Path) -> None:
    out = _run("view.js", tmp_path, steps=["flush", "timers", "flush", "timers"])
    assert len(out["sent"]) == 1


# ----------------------------------------------------------------- the build


def make_config(**analytics: Any) -> SiteConfig:
    data: dict[str, Any] = {
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
        "features": {"click_tracking": True},
        "analytics": {"page_views": True, **analytics},
    }
    return SiteConfig.model_validate(data)


def _build(tmp: Path, **analytics: Any) -> Path:
    build_site(make_config(**analytics), tmp, today=date(2027, 3, 10))
    return tmp


def test_the_attribute_is_off_by_default_and_the_pages_are_byte_identical(
    tmp_path: Path,
) -> None:
    default = _build(tmp_path / "default")
    explicit = _build(tmp_path / "explicit", beacon_token=False)
    pages = sorted(default.rglob("*"))
    assert [p for p in pages if p.is_file()]
    for path in pages:
        if path.is_file():
            assert (
                path.read_bytes() == (explicit / path.relative_to(default)).read_bytes()
            )
            if path.suffix == ".html":
                assert "data-token-route" not in path.read_text()


def test_on_puts_the_attribute_on_tab_pages_only(tmp_path: Path) -> None:
    out = _build(tmp_path, beacon_token=True)
    assert 'data-token-route="/e/t"' in (out / "index.html").read_text()
    assert 'data-token-route="/e/t"' in (out / "more" / "index.html").read_text()
    assert "data-token-route" not in (out / "privacy" / "index.html").read_text()


def test_the_content_security_policy_is_unchanged_by_the_token(tmp_path: Path) -> None:
    off = _build(tmp_path / "off")
    on = _build(tmp_path / "on", beacon_token=True)
    assert (off / "_headers").read_bytes() == (on / "_headers").read_bytes()


@pytest.mark.parametrize("bad", ["yes", "true", 1, 0, None, "false"])
def test_the_flag_must_be_a_real_boolean(bad: Any) -> None:
    with pytest.raises(ValidationError):
        make_config(beacon_token=bad)
