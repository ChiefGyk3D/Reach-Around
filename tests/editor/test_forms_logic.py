# SPDX-License-Identifier: Apache-2.0
"""Run the Node tests of the editor forms against a copy of the shipped assets."""

from __future__ import annotations

import os
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("forms_logic.mjs")


def node_or_none() -> str | None:
    found = shutil.which("node")
    if found is None and os.environ.get("CI"):
        pytest.fail("node is required in CI for the editor forms tests")
    return found


def stage(target: Path) -> Path:
    """The assets, a package.json that makes .js files modules, and the script."""
    root = resources.files("reach_around").joinpath("editor_assets")
    for item in root.iterdir():
        if item.is_file() and item.name.endswith(".js"):
            (target / item.name).write_bytes(item.read_bytes())
    (target / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    shutil.copyfile(SCRIPT, target / "forms_logic.mjs")
    return target / "forms_logic.mjs"


def run_node(node: str, script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node, "--test", "--test-reporter=spec", str(script)],
        cwd=script.parent,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PATH": os.environ.get("PATH", ""), "NODE_OPTIONS": ""},
    )


def test_the_forms_logic_passes_under_node(tmp_path: Path) -> None:
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    done = run_node(node, stage(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "tests 1" not in done.stdout  # more than a token test ran
    assert "fail 0" in done.stdout


def test_the_node_tests_go_red_on_broken_logic(tmp_path: Path) -> None:
    """The negative case: a mutated store.js must fail the same script."""
    node = node_or_none()
    if node is None:
        pytest.skip("node is not installed; the browser-side logic was not run")
    script = stage(tmp_path)
    store = tmp_path / "store.js"
    text = store.read_text(encoding="utf-8")
    mutated = text.replace("{ base: current.hash, ...step", '{ base: "0".repeat(64), ...step')
    assert mutated != text
    store.write_text(mutated, encoding="utf-8")
    done = run_node(node, script)
    assert done.returncode != 0
