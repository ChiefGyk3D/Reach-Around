# SPDX-License-Identifier: Apache-2.0
"""Release workflow: one repo-wide queue, and an older tag cannot publish."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def load() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def test_the_concurrency_group_is_repo_wide_and_never_cancels() -> None:
    conc = load()["concurrency"]
    # A per-ref group lets two tags publish at once; the later finisher wins
    # `latest`, which may be the older tag.
    assert "github.ref" not in conc["group"]
    assert conc["group"] == "${{ github.workflow }}"
    assert conc["cancel-in-progress"] is False


def _order_step() -> str:
    steps = load()["jobs"]["check-version"]["steps"]
    for step in steps:
        if "highest release tag" in step.get("name", ""):
            return str(step["run"])
    raise AssertionError("no ordering step")


def _run(
    tmp_path: Path, tag: str, remote: list[str]
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    git = bin_dir / "git"
    lines = "\n".join(f"deadbeef\trefs/tags/{t}" for t in remote)
    git.write_text(f"#!/usr/bin/env bash\ncat <<'EOF'\n{lines}\nEOF\n")
    git.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(_order_step())
    return subprocess.run(  # noqa: S603 - fixed argv, stub git
        ["/bin/bash", "-eo", "pipefail", str(script)],  # noqa: S607
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TAG": tag,
            "REPO_URL": "https://example.invalid/r",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_highest_tag_may_publish(tmp_path: Path) -> None:
    done = _run(tmp_path, "v0.10.0", ["v0.9.0", "v0.10.0", "v0.2.0"])
    assert done.returncode == 0, done.stderr


def test_an_older_tag_is_refused_when_a_newer_one_exists(tmp_path: Path) -> None:
    done = _run(tmp_path, "v0.9.0", ["v0.9.0", "v0.10.0"])
    assert done.returncode == 1
    assert "not the highest" in done.stderr


@pytest.mark.parametrize("tag", ["v1.0.0-rc1", "v1.0"])
def test_a_tag_outside_plain_semver_is_not_ordered(tmp_path: Path, tag: str) -> None:
    done = _run(tmp_path, tag, [tag, "v9.0.0"])
    assert done.returncode == 0, done.stderr
