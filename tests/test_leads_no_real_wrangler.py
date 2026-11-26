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
    """Other test files still run their own scripts and plain node (tests/site runs
    the click JS with it); only a command that runs wrangler is refused for them."""
    from conftest import _is_forbidden

    for command in (
        ["/p/node_modules/.bin/wrangler", "d1"],
        ["npx", "wrangler"],
        ["npm", "exec", "wrangler"],
    ):
        assert _is_forbidden(command, strict=False), command  # all lists
    allowed: list[object] = [
        ["/usr/bin/bash", "backup-d1.sh"],
        ["git", "status"],
        "ls -l",
        "node script.js",
        ["/usr/bin/node", "x"],
        ["npm", "ci"],
    ]
    for other in allowed:
        assert not _is_forbidden(other, strict=False), other
    assert _is_forbidden(["/usr/bin/bash"], strict=True)


def test_an_allowed_interpreter_or_launcher_cannot_start_a_real_wrangler() -> None:
    """Review round 2 (Low): the guard looked only at the program, so `sh -c`,
    `env wrangler` or `python -c` could still start one. Every word of every
    argument is now looked at. Only direct Python-level process starts are guarded:
    what a child does on its own is out of reach (stated in conftest.py)."""
    from conftest import _is_forbidden

    refused: list[object] = [
        ["/bin/sh", "-c", "wrangler x y"],
        ["/bin/bash", "-c", "cd w && wrangler x"],
        ["/usr/bin/env", "wrangler", "x"],
        ["env", "PATH=/x", "/p/node_modules/.bin/wrangler"],
        [sys.executable, "-c", "import os; os.system('wrangler d1')"],
        "sh -c 'npx wrangler'",
        ["/bin/sh", "-c", "a;wrangler"],
        # Round 4 (#905, Low): the real entry file started through node.
        ["node", "worker/node_modules/wrangler/bin/wrangler.js", "d1", "info", "prod"],
        ["/usr/bin/node", "/r/worker/node_modules/wrangler/bin/wrangler.js"],
        ["node", "x/wrangler.mjs"],
        ["node", "x/wrangler.cjs", "d1"],
        ["node", "/r/worker/node_modules/wrangler/wrangler-dist/cli.js", "d1"],
        ["/usr/bin/env", "node", "./wrangler-dist/cli.js"],
        "node worker/node_modules/wrangler/bin/wrangler.js d1 info prod",
    ]
    for bad in refused:
        assert _is_forbidden(bad, strict=False), bad
    fine: list[object] = [
        # CI regression: a workflow step run through bash -c that only mentions
        # wrangler in comments and file names (tests/test_deploy_config.py).
        [
            "/bin/bash",
            "-c",
            "set -eu\n# run `wrangler secret put` later\ncp wrangler.toml x",
        ],
        ["/bin/sh", "-c", "echo node_modules"],
        ["node", "worker/wrangler.toml"],  # a config file is not the program
        ["node", "tests/site/click.js"],
        [sys.executable, "-c", "print('nodes')"],
    ]
    for good in fine:
        assert not _is_forbidden(good, strict=False), good


def test_in_the_leads_tests_an_interpreter_running_wrangler_is_refused() -> None:
    """Through the real Popen guard: in a `test_leads*` module every start is
    refused whatever the program (the indirect forms included)."""
    for command in (
        ["/bin/sh", "-c", "wrangler d1"],
        [sys.executable, "-c", "print('wrangler')"],
        ["/usr/bin/env", "wrangler"],
    ):
        with pytest.raises(AssertionError, match="real process"):
            subprocess.run(command, check=False)  # noqa: S603 - the guard refuses it


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
