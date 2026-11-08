# SPDX-License-Identifier: Apache-2.0
"""Run the Node tests of static/live.js against a copy of the shipped script."""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("live_logic.mjs")


def node_or_none() -> str | None:
    found = shutil.which("node")
    if found is None and os.environ.get("CI"):
        pytest.fail("node is required in CI for the live script tests")
    return found


def stage(target: Path, *, source: str | None = None) -> Path:
    """The shipped script (or a mutated copy) next to the test script."""
    text = source
    if text is None:
        shipped = resources.files("reach_around.site") / "static" / "live.js"
        text = shipped.read_text(encoding="utf-8")
    (target / "live.js").write_text(text, encoding="utf-8")
    shutil.copyfile(SCRIPT, target / "live_logic.mjs")
    return target / "live_logic.mjs"


def run_node(node: str, script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv: node and a staged copy of our script
        [node, "--test", "--test-reporter=spec", str(script)],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )


def test_the_live_logic_passes_under_node(tmp_path: Path) -> None:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    done = run_node(node, stage(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "fail 0" in done.stdout
    assert "malformed is degraded" in done.stdout  # the table of bad bodies ran


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("1800", "18000"),  # staleness limit loosened
        ("=== true", "!== false"),  # unknown treated as live somewhere
    ],
)
def test_the_node_tests_go_red_on_broken_logic(
    tmp_path: Path, old: str, new: str
) -> None:
    """The negative case: a mutated live.js must fail the same script."""
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    shipped = resources.files("reach_around.site") / "static" / "live.js"
    text = shipped.read_text(encoding="utf-8")
    assert old in text
    done = run_node(node, stage(tmp_path, source=text.replace(old, new)))
    assert done.returncode != 0
