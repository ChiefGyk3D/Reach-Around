# SPDX-License-Identifier: Apache-2.0
"""A test cannot start a real wrangler through the guarded paths (#905).

This file does NOT use the `fake_wrangler` fixture, so it sees exactly what a test
that forgot to install the fake would see: the autouse guard in conftest.py. The
guard patches the module attributes subprocess.Popen, os.system, os.exec* and
os.posix_spawn* at call time, and the names bound to those originals in the
imported `test_leads*` modules. It does not catch an alias captured at import time
in any other module; it is defense in depth, not a sandbox.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from os import system as _saved_system
from pathlib import Path
from subprocess import Popen as _saved_popen
from typing import Any

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


def test_a_name_bound_to_the_original_at_import_time_is_guarded_in_leads_tests() -> (
    None
):
    """Round 5 (#905, Low): `from subprocess import Popen as saved` keeps the
    original class, so patching the module attribute alone never reached it. The
    names bound in the imported `test_leads*` modules are patched too (this file
    imports two at the top). The paths do not exist: if the guard failed the start
    would raise something other than the guard's AssertionError."""
    with pytest.raises(AssertionError, match="real process"):
        _saved_popen(["/nonexistent/node_modules/wrangler/bin/wrangler.js"])
    with pytest.raises(AssertionError, match="real process"):
        _saved_system("/nonexistent/wrangler-not-installed")  # noqa: S605


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
    argument is now looked at. Only starts through the patched module attributes
    (and the aliases in the imported test_leads* modules) are guarded: what a child
    does on its own, or an alias captured at import time elsewhere, is out of reach
    (stated in conftest.py)."""
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


# --- the narrow local-wrangler opt-out (#907) ---------------------------------

_BASE = "/var/pytest-base"
_HOME = f"{_BASE}/t1/home"
_PIN = str(Path(__file__).resolve().parent.parent / "worker/node_modules/.bin/wrangler")
_ALLOWED_MODULE = str(Path(__file__).resolve().parent / "test_d1_migration_rollback.py")
_GOOD_CMD = [_PIN, "d1", "migrations", "apply", "DB", "--local", "--persist-to", "/s"]
_GOOD_ENV = {"PATH": "/usr/bin", "HOME": _HOME}
_UNSET: Any = object()


def _ok(
    command: Any = _UNSET,
    env: Any = _UNSET,
    module_file: Any = _UNSET,
    marked: bool = True,
) -> bool:
    from conftest import _local_wrangler_allowed

    return _local_wrangler_allowed(
        _GOOD_CMD if command is _UNSET else command,
        _GOOD_ENV if env is _UNSET else env,
        _ALLOWED_MODULE if module_file is _UNSET else module_file,
        marked,
        _BASE,
    )


def test_the_local_wrangler_opt_out_has_one_accepting_shape() -> None:
    assert _ok()


def test_the_opt_out_does_not_apply_to_another_module_or_without_the_marker() -> None:
    here = Path(__file__).resolve()
    assert not _ok(module_file=str(here))
    assert not _ok(module_file=str(here.parent / "test_leads_delete.py"))
    assert not _ok(module_file=None)
    assert not _ok(marked=False)


def test_the_opt_out_needs_local_and_refuses_remote() -> None:
    assert not _ok(command=[c for c in _GOOD_CMD if c != "--local"])
    assert not _ok(command=[*_GOOD_CMD, "--remote"])


def test_the_opt_out_refuses_a_cloudflare_or_token_variable() -> None:
    for bad in ("CLOUDFLARE_API_TOKEN", "CF_ACCOUNT_ID", "MY_SECRET", "SOME_API_KEY"):
        assert not _ok(env={**_GOOD_ENV, bad: "x"}), bad
    assert not _ok(env=None)  # no explicit environment: the real one is inherited


def test_the_opt_out_needs_a_temp_home() -> None:
    assert not _ok(env={"PATH": "/usr/bin"})
    assert not _ok(env={"PATH": "/usr/bin", "HOME": str(Path.home())})
    assert not _ok(env={"PATH": "/usr/bin", "HOME": f"{_BASE}-evil/h"})


def test_the_opt_out_only_starts_the_pinned_wrangler_as_a_list() -> None:
    assert not _ok(command=["/usr/local/bin/wrangler", *_GOOD_CMD[1:]])
    assert not _ok(command=["node", _PIN, *_GOOD_CMD[1:]])
    assert not _ok(command=" ".join(_GOOD_CMD))
    assert not _ok(command=[_PIN, "x", "/other/wrangler", "--local"])
    assert not _ok(command=["/bin/sh", "-c", f"{_PIN} --local"])


@pytest.mark.allow_local_wrangler
def test_a_marker_in_another_module_does_not_reach_the_real_guard(
    tmp_path: Path,
) -> None:
    """Through the real Popen guard: this module is not the allowed one, so the
    marker is ignored and a clean `--local` start of the pinned path is refused."""
    env = {"PATH": "/usr/bin", "HOME": str(tmp_path)}
    with pytest.raises(AssertionError, match="real process"):
        subprocess.Popen(_GOOD_CMD, env=env)  # noqa: S603 - the guard must stop it
