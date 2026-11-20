# SPDX-License-Identifier: Apache-2.0
"""click.js honors Global Privacy Control and Do Not Track, as view.js does (#599).

With either signal on, no click ping is sent: not by sendBeacon, and not by the
browser's own ``ping`` attribute (the attribute is removed at click time).
Navigation is never touched: the script never calls preventDefault and never
reads or changes ``href``. Without a signal, clicks still send exactly as before.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import Any, cast

import pytest

DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[3]);
const sent = [];
const log = {
  prevented: false, removed: false, ping: cfg.native ? "/e/c/twitch" : null,
};
const handlers = {};
const node = {
  nodeType: 1,
  parentNode: null,
  hasAttribute: (n) => n === "data-link-id" || (n === "ping" && log.ping !== null),
  getAttribute: (n) =>
    (n === "data-link-id" ? "twitch" : n === "ping" ? log.ping : null),
  setAttribute: (n, v) => { if (n === "ping") log.ping = v; },
  removeAttribute: (n) => {
    if (n === "ping") { log.ping = null; log.removed = true; }
  },
};
const nav = { sendBeacon: (u) => sent.push(u) };
for (const [k, v] of Object.entries(cfg.nav)) nav[k] = v;
for (const k of cfg.throwing || []) {
  Object.defineProperty(nav, k, { get: () => { throw new Error("blocked"); } });
}
const sandbox = {
  URLSearchParams,
  location: { search: cfg.search },
  navigator: nav,
  window: cfg.win,
  document: {
    createElement: () => (cfg.native ? { ping: "" } : {}),
    addEventListener: (t, fn) => { handlers[t] = fn; },
    querySelectorAll: () => [node],
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
const atLoad = log.ping;
for (const [k, v] of Object.entries(cfg.late || {})) nav[k] = v;
if (cfg.event) {
  handlers[cfg.event]({ target: node, preventDefault: () => { log.prevented = true; } });
}
process.stdout.write(JSON.stringify({
  sent, ping: log.ping, removed: log.removed, prevented: log.prevented, atLoad,
}));
"""


def _run(
    tmp_path: Path,
    nav: dict[str, Any],
    win: dict[str, Any] | None = None,
    native: bool = False,
    search: str = "",
    source: str | None = None,
    event: str | None = "click",
    late: dict[str, Any] | None = None,
    throwing: list[str] | None = None,
) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI for the click script tests")
        pytest.skip("node is not installed; the browser-side logic was not run")
    text = source
    if text is None:
        text = (resources.files("reach_around.site") / "static" / "click.js").read_text(
            encoding="utf-8"
        )
    script = tmp_path / "click.js"
    script.write_text(text, encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    cfg = {
        "nav": nav,
        "win": win or {},
        "native": native,
        "search": search,
        "event": event,
        "late": late or {},
        "throwing": throwing or [],
    }
    done = subprocess.run(  # noqa: S603 - fixed argv: node, a staged driver and a copy of our script
        [node, str(driver), str(script), json.dumps(cfg)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return cast("dict[str, Any]", json.loads(done.stdout))


# (name, navigator extras, window extras): every one is an opt-out.
OPT_OUTS: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("gpc", {"globalPrivacyControl": True}, {}),
    ("dnt-1", {"doNotTrack": "1"}, {}),
    ("dnt-yes", {"doNotTrack": "yes"}, {}),
    ("dnt-true-spaced-upper", {"doNotTrack": " TRUE "}, {}),
    ("dnt-number", {"doNotTrack": 1}, {}),
    ("window-dnt", {}, {"doNotTrack": "1"}),
    ("ms-dnt", {"msDoNotTrack": "1"}, {}),
]

# Not opt-outs: the click must still be counted.
NORMAL: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("no-signal", {}, {}),
    ("gpc-false", {"globalPrivacyControl": False}, {}),
    ("dnt-0", {"doNotTrack": "0"}, {}),
    ("dnt-null", {"doNotTrack": None}, {}),
    ("dnt-unspecified", {"doNotTrack": "unspecified"}, {}),
    ("window-dnt-0", {}, {"doNotTrack": "0"}),
]


@pytest.mark.parametrize(("name", "nav", "win"), OPT_OUTS, ids=[c[0] for c in OPT_OUTS])
def test_an_opt_out_sends_no_beacon(
    tmp_path: Path, name: str, nav: dict[str, Any], win: dict[str, Any]
) -> None:
    out = _run(tmp_path, nav, win, native=False, search="?s=bio")
    assert out["sent"] == [], name
    assert out["prevented"] is False


@pytest.mark.parametrize(("name", "nav", "win"), OPT_OUTS, ids=[c[0] for c in OPT_OUTS])
def test_an_opt_out_strips_the_native_ping_and_leaves_navigation_alone(
    tmp_path: Path, name: str, nav: dict[str, Any], win: dict[str, Any]
) -> None:
    out = _run(tmp_path, nav, win, native=True, search="?s=bio")
    assert out["ping"] is None, name  # the browser has no ping to send
    assert out["removed"] is True
    assert out["sent"] == []
    assert out["prevented"] is False


@pytest.mark.parametrize(("name", "nav", "win"), NORMAL, ids=[c[0] for c in NORMAL])
def test_a_normal_click_still_counts(
    tmp_path: Path, name: str, nav: dict[str, Any], win: dict[str, Any]
) -> None:
    beacon = _run(tmp_path, nav, win, native=False, search="?s=Bio")
    assert beacon["sent"] == ["/e/c/twitch?s=bio"], name
    native = _run(tmp_path, nav, win, native=True, search="?s=Bio")
    assert native["ping"] == "/e/c/twitch?s=bio", name
    assert native["removed"] is False
    assert native["sent"] == []


def test_the_guard_bites_on_a_script_that_ignores_the_signal(tmp_path: Path) -> None:
    text = (resources.files("reach_around.site") / "static" / "click.js").read_text(
        encoding="utf-8"
    )
    mutated = text.replace("globalPrivacyControl", "zzNotTheSignal")
    assert mutated != text
    out = _run(tmp_path, {"globalPrivacyControl": True}, native=False, source=mutated)
    assert out["sent"] == ["/e/c/twitch"]  # the real script sends nothing


@pytest.mark.parametrize(("name", "nav", "win"), OPT_OUTS, ids=[c[0] for c in OPT_OUTS])
def test_an_opt_out_strips_the_native_ping_at_load_with_no_click(
    tmp_path: Path, name: str, nav: dict[str, Any], win: dict[str, Any]
) -> None:
    # A link opened without a click event (context menu) must not carry a ping.
    out = _run(tmp_path, nav, win, native=True, event=None)
    assert out["atLoad"] is None, name
    assert out["sent"] == []


def test_a_normal_visitor_keeps_the_ping_at_load(tmp_path: Path) -> None:
    out = _run(tmp_path, {}, native=True, event=None)
    assert out["atLoad"] == "/e/c/twitch"


@pytest.mark.parametrize("event", ["auxclick", "contextmenu"])
def test_a_signal_that_appears_later_strips_the_ping_on_other_gestures(
    tmp_path: Path, event: str
) -> None:
    out = _run(tmp_path, {}, native=True, event=event, late={"globalPrivacyControl": True})
    assert out["atLoad"] == "/e/c/twitch"
    assert out["ping"] is None
    assert out["sent"] == []
    assert out["prevented"] is False


@pytest.mark.parametrize("event", ["auxclick", "contextmenu"])
def test_other_gestures_never_send_a_beacon(tmp_path: Path, event: str) -> None:
    out = _run(tmp_path, {}, native=False, event=event)
    assert out["sent"] == []


@pytest.mark.parametrize("prop", ["globalPrivacyControl", "doNotTrack", "msDoNotTrack"])
def test_a_throwing_signal_getter_fails_toward_privacy(tmp_path: Path, prop: str) -> None:
    beacon = _run(tmp_path, {}, native=False, throwing=[prop])
    assert beacon["sent"] == [], prop
    native = _run(tmp_path, {}, native=True, throwing=[prop])
    assert native["ping"] is None, prop
    assert native["atLoad"] is None, prop
    assert native["prevented"] is False
