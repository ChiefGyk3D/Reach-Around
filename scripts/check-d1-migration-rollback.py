#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Hand-run check: a failed D1 migration file is rolled back whole (#907).

Migration 0022 drops the two kit_meta floor guard triggers and creates them again
in one file. That is safe only if ``wrangler d1 migrations apply`` rolls a failing
file back as a unit. The Worker pool test helper runs statements one at a time, so
nothing else exercises the property. This script does, on the pinned Wrangler
(worker/node_modules, the version in worker/package-lock.json), against a
disposable LOCAL D1 database in a temp directory it creates and removes.

It is NOT part of pytest or CI, on purpose: the test guard forbids any test from
starting Wrangler. Run it by hand after ``cd worker && npm ci --ignore-scripts``,
with Node 22.12 or newer on PATH:

    python3 scripts/check-d1-migration-rollback.py

Checks (one PASS or FAIL line each; a refusal or a cleanup failure adds one fixed
line):
  failing-file-rolls-back   a file that drops both triggers and then fails leaves
                            both triggers in place and is not recorded as applied
  control-detects-split     the drop as its own migration followed by a failing one
                            shows the triggers gone (proves the check can detect it)

What is guaranteed, and what is not:
  * Every process this script starts (the Node probe, the Wrangler version probe,
    every Wrangler run) goes through one wrapper that passes one allowlisted
    environment: PATH (the directory of the Node binary found on the caller's PATH,
    then /usr/bin and /bin), a temp HOME and WRANGLER_SEND_METRICS=false. No
    Cloudflare, token, proxy or NODE_OPTIONS variable is passed, so a network call
    could not authenticate. Commands are list-form argv, never a shell, with
    ``--local``, a temp ``--persist-to`` and a timeout.
  * Before anything runs, the installed Wrangler must report exactly the version
    pinned in worker/package-lock.json; any other result is a refusal.
  * Network access is NOT enforced. ``--local`` does not request a remote D1 and
    metrics are off, but nothing here blocks a connection by a modified binary. Only
    a sandbox or firewall around the run enforces that. No Wrangler flag that
    disables network use or update checks is documented in ``wrangler --help`` for
    this command, so none is used.
  * The temp directory is removed at the end. If removal fails, the script prints a
    FAIL cleanup line naming the directory and exits non-zero; the directory is
    then left for you to remove. A killed run can also leave it.
  * Exit 0 only when both checks pass and cleanup succeeded. Refuses to run when a
    CI marker is set unless RA_ROLLBACK_CHECK_OWNER_RUN=1.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIG_DIR = ROOT / "worker" / "migrations"
WRANGLER = ROOT / "worker" / "node_modules" / ".bin" / "wrangler"
LOCK = ROOT / "worker" / "package-lock.json"
TRIGGERS = ("kit_meta_floor_insert", "kit_meta_floor_update")
TIMEOUT = 180
NODE_MIN = (22, 12)
CI_MARKERS = ("CI", "GITHUB_ACTIONS", "BUILD_ID", "TF_BUILD", "JENKINS_URL")
OWNER_RUN = "RA_ROLLBACK_CHECK_OWNER_RUN"
SCRATCH_PARENT = Path.home() / ".local" / "share" / "reach-around-dev"

CONFIG = """name = "rollback-probe"
main = "index.js"
compatibility_date = "2026-10-01"

[[d1_databases]]
binding = "DB"
database_name = "rollback-probe-db"
database_id = "00000000-0000-0000-0000-000000000000"
migrations_dir = "migrations"
"""

DROPS = "".join(f"DROP TRIGGER IF EXISTS {name};\n" for name in TRIGGERS)
FAILING = "THIS IS NOT VALID SQL;\n"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Exec = Callable[..., "subprocess.CompletedProcess[str]"]


class Refused(Exception):
    """The environment is not fit to run the check."""


def ci_marker(env: Mapping[str, str]) -> str | None:
    """Name of the first CI marker that is set, or None."""
    for name in CI_MARKERS:
        if env.get(name):
            return name
    return None


def build_env(home: Path, node_dir: str) -> dict[str, str]:
    """The ONE environment every child process gets: nothing inherited."""
    return {
        "PATH": f"{node_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "WRANGLER_SEND_METRICS": "false",
    }


def make_exec(runner: Runner, env: Mapping[str, str], default_cwd: Path) -> Exec:
    """The ONE way a child process is started: list argv, no shell, fixed env."""
    child_env = dict(env)

    def run(
        cmd: list[str], cwd: Path | None = None, timeout: int = TIMEOUT
    ) -> subprocess.CompletedProcess[str]:
        return runner(
            list(cmd),
            cwd=cwd if cwd is not None else default_cwd,
            env=child_env,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    return run


def pinned_wrangler_version(lock: Path) -> str:
    try:
        data = json.loads(lock.read_text())
        version = data["packages"]["node_modules/wrangler"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("the pinned Wrangler version could not be read") from None
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise Refused("the pinned Wrangler version could not be read")
    return version


def node_version(run: Exec, node: str) -> tuple[int, int] | None:
    try:
        out = run([node, "--version"], timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.match(r"v(\d+)\.(\d+)", str(out).strip())
    return (int(found.group(1)), int(found.group(2))) if found else None


def wrangler_matches_pin(run: Exec, pinned: str) -> bool:
    try:
        out = run([str(WRANGLER), "--version"], timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return str(out).strip().splitlines()[-1:] == [pinned]


class Sandbox:
    """One disposable local D1 database with its own copy of migrations up to 0021."""

    def __init__(self, base: Path, run: Exec) -> None:
        self.base = base
        self.run = run
        self.state = base / "state"
        self.migrations = base / "migrations"
        self.migrations.mkdir()
        (base / "wrangler.toml").write_text(CONFIG)
        (base / "index.js").write_text("export default {};\n")
        for path in sorted(MIG_DIR.glob("*.sql")):
            if path.name < "0022":
                shutil.copy(path, self.migrations / path.name)

    def apply(self) -> subprocess.CompletedProcess[str]:
        cmd = [
            str(WRANGLER),
            "d1",
            "migrations",
            "apply",
            "DB",
            "--local",
            "--persist-to",
            str(self.state),
            "--config",
            str(self.base / "wrangler.toml"),
        ]
        return self.run(cmd, cwd=self.base)

    def query(self, sql: str) -> set[str]:
        found = [
            p
            for p in (self.state / "v3" / "d1").rglob("*.sqlite")
            if p.name != "metadata.sqlite"
        ]
        if len(found) != 1:
            raise Refused("expected exactly one local database file")
        con = sqlite3.connect(f"file:{found[0]}?mode=ro", uri=True)
        try:
            return {row[0] for row in con.execute(sql)}
        finally:
            con.close()

    def triggers(self) -> set[str]:
        return self.query("SELECT name FROM sqlite_master WHERE type = 'trigger'")

    def applied(self) -> set[str]:
        return self.query("SELECT name FROM d1_migrations")


def _fresh(scratch: Path, name: str, run: Exec) -> Sandbox:
    box = Sandbox(Path(tempfile.mkdtemp(prefix=f"{name}-", dir=scratch)), run)
    first = box.apply()
    if first.returncode != 0:
        raise Refused("the baseline migrations did not apply")
    if box.triggers() != set(TRIGGERS):
        raise Refused("the baseline lacks the guard triggers")
    return box


def check_failing_file_rolls_back(scratch: Path, run: Exec) -> bool:
    box = _fresh(scratch, "atomic", run)
    (box.migrations / "0022_fail.sql").write_text(DROPS + FAILING)
    result = box.apply()
    return (
        result.returncode != 0
        and box.triggers() == set(TRIGGERS)
        and "0022_fail.sql" not in box.applied()
    )


def check_control_detects_split(scratch: Path, run: Exec) -> bool:
    box = _fresh(scratch, "control", run)
    (box.migrations / "0022_drop.sql").write_text(DROPS)
    (box.migrations / "0023_fail.sql").write_text(FAILING)
    result = box.apply()
    return (
        result.returncode != 0
        and "0022_drop.sql" in box.applied()
        and box.triggers() == set()
    )


CHECKS: tuple[tuple[str, Callable[[Path, Exec], bool]], ...] = (
    ("failing-file-rolls-back", check_failing_file_rolls_back),
    ("control-detects-split", check_control_detects_split),
)


def _verify_and_run(run: Exec, node: str, scratch: Path) -> int:
    version = node_version(run, node)
    if version is None or version < NODE_MIN:
        print("REFUSED: Node 22.12 or newer is required on PATH")
        return 2
    try:
        pinned = pinned_wrangler_version(LOCK)
    except Refused as err:
        print(f"REFUSED: {err}")
        return 2
    if not wrangler_matches_pin(run, pinned):
        print("REFUSED: the installed Wrangler is not the version pinned in the lock")
        return 2
    results: list[bool] = []
    for name, check in CHECKS:
        try:
            ok = check(scratch, run)
        except (Refused, OSError, subprocess.SubprocessError, sqlite3.Error):
            ok = False
        print(f"{'PASS' if ok else 'FAIL'} {name}")
        results.append(ok)
    return 0 if all(results) else 1


def main(
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
    which: Callable[[str], str | None] = shutil.which,
    remover: Callable[[Path], None] = shutil.rmtree,
) -> int:
    env = os.environ if env is None else env
    runner = subprocess.run if runner is None else runner
    marker = ci_marker(env)
    if marker is not None and env.get(OWNER_RUN) != "1":
        print(f"REFUSED: {marker} is set; this check is for hand runs ({OWNER_RUN}=1)")
        return 2
    if not WRANGLER.exists():
        print("REFUSED: worker/node_modules is missing")
        print("Run: cd worker && npm ci --ignore-scripts")
        return 2
    # Resolve Node once, from the caller's PATH, before any child process exists.
    node = which("node")
    if node is None or not Path(node).is_absolute():
        print("REFUSED: Node 22.12 or newer is required on PATH")
        return 2
    parent = SCRATCH_PARENT if SCRATCH_PARENT.is_dir() else None
    scratch = Path(tempfile.mkdtemp(prefix="rollback-check-", dir=parent))
    code = 0
    try:
        home = scratch / "home"
        home.mkdir()
        run = make_exec(runner, build_env(home, str(Path(node).parent)), scratch)
        code = _verify_and_run(run, node, scratch)
    finally:
        try:
            remover(scratch)
        except OSError:
            print(f"FAIL cleanup: could not remove temp directory {scratch.name}")
            code = code or 1
    return code


if __name__ == "__main__":
    sys.exit(main())
