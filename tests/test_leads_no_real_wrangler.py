# SPDX-License-Identifier: Apache-2.0
"""The test suite can never start a real wrangler (#905).

This file does NOT use the `fake_wrangler` fixture, so it sees exactly what a test
that forgot to install the fake would see: the autouse guard in conftest.py. The
guard sits at the lowest level (subprocess.Popen, os.system, os.exec*,
os.posix_spawn*) so a code path added later cannot start a process either.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from leads_fakes import write_install
from reach_around.cli import main
from reach_around.commands import leads


def test_the_process_runner_is_guarded_in_every_test() -> None:
    with pytest.raises(AssertionError, match="real process runner"):
        leads._exec(["/bin/true"], {}, 1)


def test_a_new_code_path_cannot_start_a_process_through_subprocess() -> None:
    """Review round 1 (Low): the old guard replaced only `leads._exec`. A direct
    `subprocess.run`, `Popen` or `check_output` anywhere now fails too."""
    for call in (subprocess.run, subprocess.check_output, subprocess.call):
        with pytest.raises(AssertionError, match="real process"):
            call(["/bin/true"])
    with pytest.raises(AssertionError, match="real process"):
        subprocess.Popen(["/x/wrangler", "d1"])


def test_the_os_level_process_starts_are_guarded() -> None:
    with pytest.raises(AssertionError, match="real process"):
        os.system("/bin/true")  # noqa: S605 - the guard must stop it
    with pytest.raises(AssertionError, match="real process"):
        os.execv("/bin/true", ["/bin/true"])  # noqa: S606 - the guard must stop it
    with pytest.raises(AssertionError, match="real process"):
        os.posix_spawn("/bin/true", ["/bin/true"], {})


def test_outside_the_leads_tests_only_a_real_wrangler_is_refused() -> None:
    """Other test files still run their own scripts; a command whose program is
    wrangler, npx, npm or node is refused for all of them."""
    from conftest import _is_forbidden

    for command in (
        ["/p/node_modules/.bin/wrangler", "d1"],
        ["npx", "wrangler"],
        "node script.js",
        ["/usr/bin/node", "x"],
        ["npm", "ci"],
    ):
        assert _is_forbidden(command, strict=False), command
    for command in (["/usr/bin/bash", "backup-d1.sh"], ["git", "status"], "ls -l"):
        assert not _is_forbidden(command, strict=False), command
    assert _is_forbidden(["/usr/bin/bash"], strict=True)


def test_the_online_path_cannot_start_a_process_without_the_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "e.json"
    export.write_text(
        json.dumps(
            [
                {
                    "results": [
                        {
                            "id": "0123456789abcdef0123456789abcdef",
                            "email": "a@example.test",
                        }
                    ],
                    "success": True,
                    "meta": {},
                }
            ]
        )
    )
    root = tmp_path / "repo"
    root.mkdir()
    write_install(root, "4.0.0")
    monkeypatch.setattr(leads, "REPO_ROOT", root)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("a@example.test\n"))
    argv = ["leads", "delete", "--input", str(export), "--email", "-", "--yes"]
    argv += ["--database", "reach-around-leads", "--alerts-database", "x"]
    argv += ["--alerts-database-id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"]
    argv += ["--leads-database-id", "11111111-2222-4333-8444-555555555555"]
    with pytest.raises(AssertionError, match="real process runner"):
        main(argv)
    assert not (tmp_path / "started").exists()
