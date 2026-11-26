# SPDX-License-Identifier: Apache-2.0
"""scripts/check-d1-migration-rollback.py stays safe and in sync (#907).

The script itself is a hand-run check that starts the pinned Wrangler; it is not
run for real here (the test guard forbids tests from starting Wrangler). These
tests read the script and drive main() with a fake runner that records every
child process request: it does nothing at import, refuses under a CI marker, every
call is list argv with the same credential-free environment, local only, inside
the temp directory, the Wrangler version must match the lock, a cleanup failure
fails the run, and it drops the same triggers as migration 0022. They need neither
worker/node_modules nor Node.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-d1-migration-rollback.py"
MIGRATION = ROOT / "worker" / "migrations" / "0022_kit_floor_guard_recreate.sql"


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Import the script with every process start forbidden."""

    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError(
            "the script started a process or touched a process API at import"
        )

    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    spec = importlib.util.spec_from_file_location("d1_rollback_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_exists_and_compiles() -> None:
    assert SCRIPT.is_file()
    compile(SCRIPT.read_text(), str(SCRIPT), "exec")


def test_import_has_no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(monkeypatch)
    assert callable(module.main)
    assert module.__name__ == "d1_rollback_check"  # not run as __main__


@pytest.mark.parametrize(
    "marker", ["CI", "GITHUB_ACTIONS", "BUILD_ID", "TF_BUILD", "JENKINS_URL"]
)
def test_refused_under_a_ci_marker(
    marker: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load(monkeypatch)
    calls: list[Any] = []

    def fake(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))
        raise AssertionError("the runner must not be called under a CI marker")

    code = module.main(env={marker: "1"}, runner=fake)
    out = capsys.readouterr().out
    assert code == 2
    assert f"REFUSED: {marker} is set" in out
    assert calls == []


NODE = "/opt/fake/bin/node"
GOOD_ENV = {"CI": "1", "RA_ROLLBACK_CHECK_OWNER_RUN": "1"}


class FakeRunner:
    """Records every child process request and plays a tiny local D1 and Node."""

    def __init__(
        self,
        module: ModuleType,
        node_out: str = "v22.23.5\n",
        wrangler: str = "4.162.0",
    ) -> None:
        self.module = module
        self.node_out = node_out
        self.wrangler = wrangler
        self.calls: list[tuple[Any, dict[str, Any]]] = []

    def __call__(self, cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, kwargs))
        if cmd[1:] == ["--version"]:
            out = self.node_out if cmd[0] == NODE else self.wrangler + "\n"
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return subprocess.CompletedProcess(cmd, self._migrate(cmd, kwargs), "", "")

    def _migrate(self, cmd: list[str], kwargs: dict[str, Any]) -> int:
        persist = Path(cmd[cmd.index("--persist-to") + 1])
        db = persist / "v3" / "d1" / "miniflare-D1DatabaseObject" / "x.sqlite"
        migrations = Path(kwargs["cwd"]) / "migrations"
        db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(db)
        try:
            first = not con.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'd1_migrations'"
            ).fetchone()
            if first:
                con.execute("CREATE TABLE d1_migrations (name TEXT)")
                con.execute("CREATE TABLE kit_meta (a)")
                for name in self.module.TRIGGERS:
                    con.execute(
                        f"CREATE TRIGGER {name} BEFORE INSERT ON kit_meta "
                        "BEGIN SELECT 1; END"
                    )
                con.commit()
                return 0
            if (migrations / "0022_fail.sql").exists():
                return 1  # rolled back whole: nothing changes
            if (migrations / "0022_drop.sql").exists():
                con.execute("INSERT INTO d1_migrations VALUES ('0022_drop.sql')")
                for name in self.module.TRIGGERS:
                    con.execute(f"DROP TRIGGER {name}")
                con.commit()
                return 1  # 0023 fails after the drop was kept
            return 0
        finally:
            con.close()


def _drive(
    module: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake: Any,
    env: dict[str, str] | None = None,
    **kwargs: Any,
) -> int:
    wrangler = tmp_path / "pinned" / "wrangler"
    wrangler.parent.mkdir(exist_ok=True)
    wrangler.write_text("")
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    monkeypatch.setattr(module, "WRANGLER", wrangler)
    monkeypatch.setattr(module, "SCRATCH_PARENT", scratch)
    return int(
        module.main(
            env=GOOD_ENV if env is None else env,
            runner=fake,
            which=lambda _name: NODE,
            **kwargs,
        )
    )


def test_owner_run_flag_lifts_only_the_ci_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load(monkeypatch)
    fake = FakeRunner(module)
    code = _drive(module, tmp_path, monkeypatch, fake)
    out = capsys.readouterr().out
    assert "is set" not in out
    assert fake.calls, "execution must continue past the CI refusal"
    assert fake.calls[0][0] == [NODE, "--version"]  # first step: the Node check
    assert code == 0  # the runner result decides the exit code
    assert out.splitlines() == [
        "PASS failing-file-rolls-back",
        "PASS control-detects-split",
    ]
    # the other safety conditions still hold: an old Node is still refused
    old = FakeRunner(module, node_out="v18.0.0\n")
    assert _drive(module, tmp_path, monkeypatch, old) == 2
    assert [c[0] for c in old.calls] == [[NODE, "--version"]]


def test_every_child_process_gets_only_the_allowlisted_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load(monkeypatch)
    secrets = {
        "CLOUDFLARE_API_TOKEN": "x",
        "MY_SERVICE_TOKEN": "x",
        "NODE_OPTIONS": "--require /x",
        "HTTPS_PROXY": "http://x",
        "CF_ACCOUNT_ID": "x",
    }
    for key, value in secrets.items():
        monkeypatch.setenv(key, value)
    fake = FakeRunner(module)
    env = {**GOOD_ENV, **secrets}
    assert _drive(module, tmp_path, monkeypatch, fake, env=env) == 0
    assert len(fake.calls) >= 6  # node probe, wrangler probe, 2 x (baseline, apply)
    for cmd, kwargs in fake.calls:
        child_env = kwargs.get("env")
        assert child_env is not None, f"no env= for {cmd}"
        assert set(child_env) == {"PATH", "HOME", "WRANGLER_SEND_METRICS"}
        assert child_env["WRANGLER_SEND_METRICS"] == "false"
        assert child_env["PATH"] == "/opt/fake/bin:/usr/bin:/bin"
        assert Path(child_env["HOME"]).is_relative_to(tmp_path / "scratch")
        assert not set(secrets) & set(child_env)


def test_every_runner_call_is_local_list_argv_inside_the_temp_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = _load(monkeypatch)
    fake = FakeRunner(module)
    assert _drive(module, tmp_path, monkeypatch, fake) == 0
    inside = tmp_path / "scratch"
    wrangler = str(tmp_path / "pinned" / "wrangler")
    d1_calls = 0
    for cmd, kwargs in fake.calls:
        assert isinstance(cmd, list) and all(isinstance(a, str) for a in cmd)
        assert cmd[0] in (NODE, wrangler)
        assert not kwargs.get("shell")
        assert "--remote" not in cmd and "--preview" not in cmd
        assert Path(kwargs["cwd"]).is_relative_to(inside)
        assert kwargs["timeout"] > 0
        if cmd[1:2] == ["d1"]:
            d1_calls += 1
            assert cmd[1:4] == ["d1", "migrations", "apply"]
            assert "--local" in cmd
            assert Path(cmd[cmd.index("--persist-to") + 1]).is_relative_to(inside)
            assert Path(cmd[cmd.index("--config") + 1]).is_relative_to(inside)
    assert d1_calls == 4
    assert list(inside.iterdir()) == []  # the temp dir was removed


@pytest.mark.parametrize("reported", ["4.161.9", "4.162.1", "5.0.0", "", "wrangler"])
def test_a_wrangler_that_is_not_the_pinned_version_is_refused(
    reported: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load(monkeypatch)
    fake = FakeRunner(module, wrangler=reported)
    assert _drive(module, tmp_path, monkeypatch, fake) == 2
    out = capsys.readouterr().out
    assert out == (
        "REFUSED: the installed Wrangler is not the version pinned in the lock\n"
    )
    assert not [c for c, _ in fake.calls if c[1:2] == ["d1"]]


def test_the_pinned_version_comes_from_the_lock_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load(monkeypatch)
    lock = json.loads((ROOT / "worker" / "package-lock.json").read_text())
    expected = lock["packages"]["node_modules/wrangler"]["version"]
    assert module.pinned_wrangler_version(module.LOCK) == expected


def test_a_cleanup_failure_is_reported_and_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    module = _load(monkeypatch)
    removed: list[Path] = []

    def failing_remover(path: Path) -> None:
        removed.append(path)
        shutil.rmtree(path)  # keep the test tree clean, then report a failure
        raise OSError("cannot remove")

    fake = FakeRunner(module)
    code = _drive(module, tmp_path, monkeypatch, fake, remover=failing_remover)
    out = capsys.readouterr().out.splitlines()
    assert code == 1  # both checks passed; only the cleanup failed
    assert out[:2] == ["PASS failing-file-rolls-back", "PASS control-detects-split"]
    assert out[2] == f"FAIL cleanup: could not remove temp directory {removed[0].name}"
    assert removed[0].name.startswith("rollback-check-")
    assert len(out) == 3


def test_script_text_is_local_only() -> None:
    text = SCRIPT.read_text()
    assert "--local" in text
    assert "--remote" not in text
    assert "shell=True" not in text
    assert "CLOUDFLARE" not in text


def test_trigger_names_match_migration_0022(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load(monkeypatch)
    real = re.findall(
        r"^DROP TRIGGER IF EXISTS (\w+);", MIGRATION.read_text(), flags=re.MULTILINE
    )
    assert real, "migration 0022 no longer drops triggers: update the script"
    built = re.findall(
        r"^DROP TRIGGER IF EXISTS (\w+);", module.DROPS, flags=re.MULTILINE
    )
    assert built == real
    assert set(module.TRIGGERS) == set(real)
