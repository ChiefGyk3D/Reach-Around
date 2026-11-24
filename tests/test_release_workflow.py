# SPDX-License-Identifier: Apache-2.0
"""Release workflow: one repo-wide queue, and an older tag cannot publish."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, cast

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


@pytest.mark.parametrize(
    "tag",
    [
        "v1.0.0-rc1",
        "v1.0.0-beta.2",
        "v1.0.0+build5",
        "v1.0.0-rc1+build5",
        "v1.0",
        "v01.0.0",
        "v1.00.0",
        "v1.0.007",
        "V1.0.0",
        "v1.0.0é",
        "v１.0.0",  # fullwidth digit one
        "v1.0.0 ",
        "v1.0.0\nv9.0.0",
    ],
)
def test_a_tag_outside_the_accepted_grammar_is_refused(
    tmp_path: Path, tag: str
) -> None:
    # Fails closed even when the tag would be the highest one.
    done = _run(tmp_path, tag, [tag.splitlines()[0]])
    assert done.returncode == 1, done.stdout + done.stderr
    assert "is not a plain vMAJOR.MINOR.PATCH tag" in done.stderr


def test_a_refused_tag_is_refused_even_with_a_newer_stable_release(
    tmp_path: Path,
) -> None:
    done = _run(tmp_path, "v1.0.0-rc1", ["v1.0.0-rc1", "v9.0.0"])
    assert done.returncode == 1


@pytest.mark.parametrize("tag", ["v0.0.0", "v1.2.3", "v10.20.30"])
def test_a_plain_tag_inside_the_grammar_is_accepted(tmp_path: Path, tag: str) -> None:
    assert _run(tmp_path, tag, [tag]).returncode == 0


def test_remote_tags_outside_the_grammar_never_count_as_the_highest(
    tmp_path: Path,
) -> None:
    done = _run(tmp_path, "v1.0.0", ["v1.0.0", "v9.0.0-rc1", "v09.0.0", "V9.0.0"])
    assert done.returncode == 0, done.stderr


def test_the_trigger_is_narrowed_beyond_a_bare_v_glob() -> None:
    # YAML reads the bare key `on` as the boolean True.
    tags = cast("dict[Any, Any]", load())[True]["push"]["tags"]
    assert "v*" not in tags
    assert tags == ["v[0-9]*.[0-9]*.[0-9]*"]
