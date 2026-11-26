# SPDX-License-Identifier: Apache-2.0
"""The one process runner of `reach-around leads delete` kills the whole process
group (#905 review round 2, Medium).

This file is not a `test_leads*` module, so real (harmless) processes may start in
it: a small Python child that starts a grandchild, both holding the pipe. The
conftest guard still refuses any command that names wrangler, node, npm or npx.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from reach_around.commands import leads

CHILD = """\
import os, subprocess, sys, time
grand = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(120)"]
)
with open(sys.argv[1], "w") as handle:
    handle.write(str(grand.pid))
if sys.argv[2] == "flood":
    chunk = b"x" * 65536
    while True:
        os.write(1, chunk)
else:
    time.sleep(120)
"""


def _alive(pid: int) -> bool:
    """True while the process exists and is not a zombie (a reaper that is slow to
    collect a killed grandchild must not look like a survivor)."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return text.rsplit(")", 1)[1].split()[0] != "Z"


def _wait_dead(pid: int, seconds: float = 8.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


@pytest.fixture
def child(tmp_path: Path) -> tuple[list[str], Path]:
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    return [sys.executable, "-I", str(script), str(tmp_path / "grand.pid")], (
        tmp_path / "grand.pid"
    )


def _grandchild(pidfile: Path) -> int | None:
    try:
        return int(pidfile.read_text())
    except (OSError, ValueError):
        return None


@pytest.mark.parametrize("mode", ["flood", "hold"])
def test_a_grandchild_is_killed_with_the_child_on_the_cap_or_the_time_limit(
    child: tuple[list[str], Path],
    real_exec: Any,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    """The child starts a grandchild that inherits the output pipe (and, in real
    use, the token in its environment). The cap and the time limit used to kill
    only the child, so the grandchild stayed alive and kept the pipe."""
    argv, pidfile = child
    monkeypatch.setattr(leads, "MAX_WRANGLER_BYTES", 1024 * 1024)
    started = time.monotonic()
    grand: int | None = None
    try:
        with pytest.raises((leads.WranglerError, subprocess.TimeoutExpired)):
            real_exec(
                [*argv, mode], {"PATH": "/usr/bin:/bin"}, 3 if mode == "hold" else 20
            )
        assert time.monotonic() - started < 15
        grand = _grandchild(pidfile)
        assert grand is not None, "the child did not start a grandchild"
        assert _wait_dead(grand), "the grandchild outlived the call"
    finally:
        if grand is None:
            grand = _grandchild(pidfile)
        if grand is not None and _alive(grand):
            os.kill(grand, 9)  # a leftover of a failed run, killed by its pid


def test_outside_the_leads_tests_plain_node_still_starts() -> None:
    """CI regression: the guard once refused every `node`, which tests/site runs
    for the click JS. Only wrangler is refused outside the leads tests."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed in this image")
    done = subprocess.run(  # noqa: S603 - the one real, harmless start
        [node, "--version"], capture_output=True, check=False
    )
    assert done.returncode == 0 and done.stdout.startswith(b"v")
    for command in (["/bin/sh", "-c", "wrangler d1"], [node, "-e", "wrangler"]):
        with pytest.raises(AssertionError, match="real process"):
            subprocess.run(command, check=False)  # noqa: S603 - the guard refuses it


@pytest.mark.parametrize(
    "command",
    [
        ["node", "/nonexistent/node_modules/wrangler/bin/wrangler.js", "d1", "info"],
        ["/usr/bin/node", "/nonexistent/node_modules/wrangler/bin/wrangler.mjs"],
        ["node", "/nonexistent/node_modules/wrangler/wrangler-dist/cli.js", "d1"],
    ],
)
def test_outside_the_leads_tests_the_real_entry_file_is_refused(
    command: list[str],
) -> None:
    """Round 4 (#905, Low): `node .../wrangler.js` has no word named exactly
    `wrangler`, so the real entry file started through node used to pass the guard
    here. Through the real Popen guard, in a module that is not a leads test (the
    paths do not exist, so a guard that failed would start nothing real)."""
    with pytest.raises(AssertionError, match="real process"):
        subprocess.run(command, check=False)  # noqa: S603 - the guard refuses it


def test_the_child_is_its_own_session_leader(real_exec: Any) -> None:
    """The group id is the child's pid only when it leads a session of its own."""
    code = (
        "import os; print(os.getpgrp() == os.getpid() and os.getsid(0) == os.getpid())"
    )
    out = real_exec([sys.executable, "-I", "-c", code], {"PATH": "/usr/bin:/bin"}, 20)
    assert out == (0, b"True\n")
