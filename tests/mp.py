# SPDX-License-Identifier: Apache-2.0
"""Multi-process helpers: spawn children, release them together, kill them on cue.

Production code exposes no kill hook. Tests wrap the public seams inside the
child instead (see ``tests/store/crash_child.py``).
"""

from __future__ import annotations

import multiprocessing
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

KILLED = 137


def wait_for_go(release_dir: Path, index: int, timeout: float = 30.0) -> None:
    """Child side: announce readiness, then block until the parent says go."""
    (release_dir / f"ready-{index}").write_text("1")
    deadline = time.monotonic() + timeout
    while not (release_dir / "go").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("never released")
        time.sleep(0.005)


def run_children(
    target: Callable[..., None],
    n: int,
    *args: Any,
    release_dir: Path | None = None,
    timeout: float = 60.0,
) -> list[int]:
    """Run ``target(index, *args)`` in ``n`` spawned processes; return exit codes.

    With ``release_dir`` the parent waits until every child wrote its ready file
    (the child calls ``wait_for_go``) and then releases them all at once.
    """
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=target, args=(i, *args)) for i in range(n)]
    for proc in procs:
        proc.start()
    if release_dir is not None:
        deadline = time.monotonic() + timeout
        while len(list(release_dir.glob("ready-*"))) < n:
            if time.monotonic() > deadline:
                raise TimeoutError("children never became ready")
            time.sleep(0.005)
        (release_dir / "go").write_text("1")
    codes: list[int] = []
    for proc in procs:
        proc.join(timeout)
        if proc.is_alive():
            proc.kill()
            proc.join()
        codes.append(proc.exitcode if proc.exitcode is not None else -1)
    return codes


def kill_now() -> None:
    """Child side: die without cleanup, like a SIGKILL."""
    os._exit(KILLED)
