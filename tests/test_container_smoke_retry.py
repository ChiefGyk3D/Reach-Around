# SPDX-License-Identifier: Apache-2.0
"""The Podman smoke script retries the image build a bounded number of times (#933)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "test_container_smoke.sh"

# A fake engine: `build` fails until the Nth call, every other command fails quietly,
# so a run that gets past the build stops at the first later check.
ENGINE = """#!/bin/bash
if [ "$1" = build ]; then
    n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$COUNT"
    if [ "$n" -lt "$SUCCEED_ON" ]; then echo "pull timeout $n" >&2; exit 125; fi
    exit 0
fi
exit 1
"""


def run(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], int]:
    engine = tmp_path / "engine"
    engine.write_text(ENGINE)
    engine.chmod(engine.stat().st_mode | stat.S_IXUSR)
    count = tmp_path / "count"
    full = {
        **os.environ,
        "CONTAINER_ENGINE": str(engine),
        "COUNT": str(count),
        "BUILD_RETRY_PAUSE": "0",
        **env,
    }
    proc = subprocess.run(  # noqa: S603
        ["/bin/bash", str(SCRIPT)],
        env=full,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return proc, int(count.read_text()) if count.exists() else 0


def test_succeeds_on_second_attempt(tmp_path: Path) -> None:
    proc, builds = run(tmp_path, SUCCEED_ON="2")
    assert builds == 2
    assert "build attempt 1 of 3 failed" in proc.stderr
    assert "image build failed" not in proc.stderr
    assert "build exited non-zero" in proc.stderr  # got past the build step


def test_fails_hard_after_exactly_the_attempt_limit(tmp_path: Path) -> None:
    proc, builds = run(tmp_path, SUCCEED_ON="99", BUILD_ATTEMPTS="4")
    assert builds == 4
    assert proc.returncode != 0
    assert "pull timeout 4" in proc.stderr
    assert "image build failed after 4 attempts" in proc.stderr


def test_bad_parameters_exit_two_before_any_build(tmp_path: Path) -> None:
    for bad in (
        {"BUILD_ATTEMPTS": "0"},
        {"BUILD_ATTEMPTS": "6"},
        {"BUILD_ATTEMPTS": "x"},
        {"BUILD_RETRY_PAUSE": "61"},
        {"BUILD_RETRY_PAUSE": "-1"},
    ):
        proc, builds = run(tmp_path, SUCCEED_ON="1", **bad)
        assert proc.returncode == 2, bad
        assert builds == 0
        assert "must be an integer" in proc.stderr
        (tmp_path / "count").unlink(missing_ok=True)
