# SPDX-License-Identifier: Apache-2.0
"""Run the shipped click.js under Node against a small stub of the page (issue #566).

The click ping carries up to three closed labels and nothing else: ``s`` (source) and ``h`` (hop) are
read from the page address, ``l`` (landing) is a constant the build bakes into the page as
``data-landing``. Nothing is stored in the browser.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from importlib import resources

import pytest

DRIVER = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(process.argv[3]);
const sent = [];
let handler = null;
const node = {
  nodeType: 1,
  parentNode: null,
  hasAttribute: (n) => n === "data-link-id",
  getAttribute: (n) => (n === "data-link-id" ? "twitch" : null),
};
const sandbox = {
  URLSearchParams,
  location: { search: cfg.search },
  navigator: { sendBeacon: (u) => sent.push(u) },
  document: {
    body: { getAttribute: (n) => (n === "data-landing" ? cfg.landing : null) },
    createElement: () => ({}), // no "ping" property: the beacon path
    addEventListener: (_t, fn) => { handler = fn; },
  },
  localStorage: new Proxy({}, { get() { throw new Error("storage read"); } }),
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), sandbox);
handler({ target: node });
process.stdout.write(JSON.stringify(sent));
"""


def _node() -> str | None:
    found = shutil.which("node")
    if found is None and os.environ.get("CI"):
        pytest.fail("node is required in CI for the click script tests")
    return found


def _sent(search: str, landing: str | None, tmp_path, source: str | None = None) -> list[str]:
    node = _node()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    script = tmp_path / "click.js"
    text = source
    if text is None:
        text = (resources.files("reach_around.site") / "static" / "click.js").read_text(encoding="utf-8")
    script.write_text(text, encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(DRIVER, encoding="utf-8")
    done = subprocess.run(  # noqa: S603 - fixed argv: node, a staged driver and a copy of our script
        [node, str(driver), str(script), json.dumps({"search": search, "landing": landing})],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def test_no_labels_sends_the_bare_ping(tmp_path) -> None:
    assert _sent("", None, tmp_path) == ["/e/c/twitch"]


def test_source_hop_and_landing_are_forwarded_in_a_fixed_order(tmp_path) -> None:
    assert _sent("?s=Bio-IG&h=Blog", "support", tmp_path) == ["/e/c/twitch?s=bio-ig&h=blog&l=support"]


def test_landing_alone_and_hop_alone(tmp_path) -> None:
    assert _sent("", "home", tmp_path) == ["/e/c/twitch?l=home"]
    assert _sent("?h=links", None, tmp_path) == ["/e/c/twitch?h=links"]


@pytest.mark.parametrize(
    "search",
    ["?h=", "?h=a&h=b", "?h=a%20b", "?h=%E2%84%AAick", "?h=" + "x" * 31, "?h=%3Cscript%3E", "?hh=blog"],
)
def test_a_malformed_hop_sends_nothing(tmp_path, search: str) -> None:
    assert _sent(search, None, tmp_path) == ["/e/c/twitch"]


@pytest.mark.parametrize("landing", ["", "Home", "a b", "x" * 31, "<b>", "a/b"])
def test_a_malformed_landing_sends_nothing(tmp_path, landing: str) -> None:
    assert _sent("", landing, tmp_path) == ["/e/c/twitch"]


def test_the_script_reads_no_storage_and_no_referrer(tmp_path) -> None:
    # The stub's localStorage throws on any read; the run above would fail. The shipped text must not name them.
    text = (resources.files("reach_around.site") / "static" / "click.js").read_text(encoding="utf-8")
    for word in ("localStorage", "sessionStorage", "document.cookie", "referrer", "indexedDB"):
        assert word not in text


def test_the_guard_bites_on_a_script_that_ignores_hop(tmp_path) -> None:
    text = (resources.files("reach_around.site") / "static" / "click.js").read_text(encoding="utf-8")
    mutated = text.replace('"h"', '"zz"')
    assert mutated != text
    assert _sent("?h=blog", None, tmp_path, source=mutated) == ["/e/c/twitch"]  # the real one sends ?h=blog
