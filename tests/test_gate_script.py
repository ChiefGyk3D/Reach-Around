# SPDX-License-Identifier: Apache-2.0
"""scripts/gate.sh: which steps each mode plans, and how it reports failure.

Everything runs offline in a throwaway git repo with stub ``ruff``, ``mypy`` and
``python`` programs, so no real tool or network is involved.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gate.sh"
BASH = shutil.which("bash")


# Prints its name and arguments; exits 1 when STUB_FAIL_TOOL names it. As the
# schema step's ``python``, it writes the same bytes the repo commits.
STUB = r"""#!/usr/bin/env bash
name=$(basename "$0")
echo "stub-$name $*"
while [ $# -gt 0 ]; do
    if [ "$1" = "--out" ]; then
        printf '{}\n' > "$2"
    fi
    shift
done
if [ "${STUB_FAIL_TOOL:-}" = "$name" ]; then
    exit 1
fi
exit 0
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, throwaway repo
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
        },
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one commit on ``base`` and a fake venv of stub tools."""
    root = tmp_path / "repo"
    (root / "src" / "reach_around").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "docs").mkdir()
    (root / "worker").mkdir()
    (root / "src" / "reach_around" / "widget.py").write_text("X = 1\n")
    (root / "tests" / "test_widget.py").write_text("def test_x(): pass\n")
    (root / "docs" / "guide.md").write_text("hi\n")
    (root / "docs" / "site.schema.json").write_text("{}\n")
    (root / "worker" / "index.ts").write_text("export {};\n")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "base")
    bindir = tmp_path / "venv" / "bin"
    bindir.mkdir(parents=True)
    for name in ("python", "ruff", "mypy"):
        stub = bindir / name
        stub.write_text(STUB)
        stub.chmod(0o755)
    return root


def _gate(
    repo: Path, *args: str, fail_tool: str = ""
) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    env = {
        **os.environ,
        "GATE_ROOT": str(repo),
        "GATE_VENV": str(repo.parent / "venv"),
        "GATE_BASE": "base",
    }
    env.pop("STUB_FAIL_TOOL", None)
    if fail_tool:
        env["STUB_FAIL_TOOL"] = fail_tool
    return subprocess.run(  # noqa: S603 - fixed argv
        [BASH, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _names(out: str) -> list[str]:
    return [
        ln.split(":")[1].strip() for ln in out.splitlines() if ln.startswith("plan:")
    ]


def test_full_plan_lists_every_step(repo: Path) -> None:
    res = _gate(repo, "--plan")
    assert res.returncode == 0, res.stderr
    assert _names(res.stdout) == [
        "pytest",
        "ruff check",
        "ruff format",
        "mypy",
        "schema",
        "worker install",
        "worker tsc",
        "worker vitest",
    ]


def test_visual_flag_reaches_pytest(repo: Path) -> None:
    assert "--run-visual" in _gate(repo, "--plan", "--visual").stdout
    assert "--run-visual" not in _gate(repo, "--plan").stdout


def test_fast_docs_only_runs_nothing_heavy(repo: Path) -> None:
    (repo / "docs" / "guide.md").write_text("changed\n")
    res = _gate(repo, "--fast", "--plan")
    assert res.returncode == 0, res.stderr
    assert _names(res.stdout) == []
    assert "nothing heavy" in res.stdout


def test_fast_python_change_maps_to_related_tests(repo: Path) -> None:
    (repo / "src" / "reach_around" / "widget.py").write_text("X = 2\n")
    res = _gate(repo, "--fast", "--plan")
    assert _names(res.stdout) == [
        "ruff check",
        "ruff format",
        "mypy",
        "pytest",
        "schema",
    ]
    assert "tests/test_widget.py" in res.stdout
    assert "worker" not in res.stdout


def test_fast_worker_change_runs_only_worker_steps(repo: Path) -> None:
    (repo / "worker" / "index.ts").write_text("export const a = 1;\n")
    res = _gate(repo, "--fast", "--plan")
    assert _names(res.stdout) == ["worker install", "worker tsc", "worker vitest"]


def test_fast_module_without_a_test_runs_the_whole_suite(repo: Path) -> None:
    (repo / "src" / "reach_around" / "orphan.py").write_text("Y = 1\n")
    res = _gate(repo, "--fast", "--plan")
    pytest_lines = [ln for ln in res.stdout.splitlines() if "plan: pytest" in ln]
    assert len(pytest_lines) == 1
    assert ".py" not in pytest_lines[0].split("pytest -q")[-1]


def test_passing_run_prints_exit_codes_and_one_summary(repo: Path) -> None:
    (repo / "src" / "reach_around" / "widget.py").write_text("X = 3\n")
    res = _gate(repo, "--fast")
    assert res.returncode == 0, res.stdout
    assert "-- ruff check: exit=0" in res.stdout
    assert res.stdout.strip().splitlines()[-1].startswith("gate: PASS")


def test_a_failing_step_fails_the_gate_but_runs_the_rest(repo: Path) -> None:
    """The guard bites: one red step gives a non-zero exit and a FAIL summary."""
    (repo / "src" / "reach_around" / "widget.py").write_text("X = 4\n")
    res = _gate(repo, "--fast", fail_tool="ruff")
    assert res.returncode == 1
    assert "-- ruff check: exit=1" in res.stdout
    assert "-- mypy: exit=0" in res.stdout
    last = res.stdout.strip().splitlines()[-1]
    assert last.startswith("gate: FAIL") and "ruff check" in last


def test_unknown_option_is_rejected(repo: Path) -> None:
    res = _gate(repo, "--bogus")
    assert res.returncode == 2


def test_missing_base_ref_is_an_error(repo: Path) -> None:
    assert BASH is not None
    env = {
        **os.environ,
        "GATE_ROOT": str(repo),
        "GATE_VENV": str(repo.parent / "venv"),
        "GATE_BASE": "no-such-ref",
    }
    res = subprocess.run(  # noqa: S603 - fixed argv
        [BASH, str(SCRIPT), "--fast"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 2
