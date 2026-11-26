#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Hand-run check: a failed D1 migration file is rolled back whole (#907).

Migration 0022 drops the two kit_meta floor guard triggers and creates them again
in one file. That is safe only if ``wrangler d1 migrations apply`` rolls a failing
file back as a unit. The Worker pool test helper runs statements one at a time, so
nothing else exercises the property. This script does, on the pinned Wrangler
(worker/node_modules, 4.162.x), against a disposable LOCAL D1 database in a temp
directory it creates and removes.

It is NOT part of pytest or CI, on purpose: the test guard forbids any test from
starting Wrangler. Run it by hand after ``cd worker && npm ci --ignore-scripts``,
with Node 22.12 or newer on PATH:

    python3 scripts/check-d1-migration-rollback.py

Checks (one PASS or FAIL line each, nothing else is printed):
  failing-file-rolls-back   a file that drops both triggers and then fails leaves
                            both triggers in place and is not recorded as applied
  control-detects-split     the drop as its own migration followed by a failing one
                            shows the triggers gone (proves the check can detect it)

Hermetic: ``--local`` with a temp persist directory, a minimal temp Wrangler
config, a minimal environment (PATH and a temp HOME only, no account or token
variables) and a timeout on every command. Exit 0 only when both checks hold.
Refuses to run when a CI marker is set unless RA_ROLLBACK_CHECK_OWNER_RUN=1.
"""

from __future__ import annotations

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
WRANGLER_SERIES = "4.162."
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


class Refused(Exception):
    """The environment is not fit to run the check."""


def ci_marker(env: Mapping[str, str]) -> str | None:
    """Name of the first CI marker that is set, or None."""
    for name in CI_MARKERS:
        if env.get(name):
            return name
    return None


def node_version(runner: Runner) -> tuple[int, int] | None:
    node = shutil.which("node")
    if node is None:
        return None
    try:
        out = runner(
            [node, "--version"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.match(r"v(\d+)\.(\d+)", out.strip())
    return (int(found.group(1)), int(found.group(2))) if found else None


class Sandbox:
    """One disposable local D1 database with its own copy of migrations up to 0021."""

    def __init__(self, base: Path, runner: Runner) -> None:
        self.base = base
        self.runner = runner
        self.home = base / "home"
        self.state = base / "state"
        self.migrations = base / "migrations"
        self.home.mkdir()
        self.migrations.mkdir()
        (base / "wrangler.toml").write_text(CONFIG)
        (base / "index.js").write_text("export default {};\n")
        for path in sorted(MIG_DIR.glob("*.sql")):
            if path.name < "0022":
                shutil.copy(path, self.migrations / path.name)

    def apply(self) -> subprocess.CompletedProcess[str]:
        node = shutil.which("node")
        if node is None:
            raise Refused("node is not on PATH")
        env = {
            "PATH": f"{Path(node).parent}:/usr/bin:/bin",
            "HOME": str(self.home),
            "WRANGLER_SEND_METRICS": "false",
        }
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
        return self.runner(
            cmd,
            cwd=self.base,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )

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


def _fresh(scratch: Path, name: str, runner: Runner) -> Sandbox:
    box = Sandbox(Path(tempfile.mkdtemp(prefix=f"{name}-", dir=scratch)), runner)
    first = box.apply()
    if first.returncode != 0:
        raise Refused("the baseline migrations did not apply")
    if box.triggers() != set(TRIGGERS):
        raise Refused("the baseline lacks the guard triggers")
    return box


def check_failing_file_rolls_back(scratch: Path, runner: Runner) -> bool:
    box = _fresh(scratch, "atomic", runner)
    (box.migrations / "0022_fail.sql").write_text(DROPS + FAILING)
    result = box.apply()
    return (
        result.returncode != 0
        and box.triggers() == set(TRIGGERS)
        and "0022_fail.sql" not in box.applied()
    )


def check_control_detects_split(scratch: Path, runner: Runner) -> bool:
    box = _fresh(scratch, "control", runner)
    (box.migrations / "0022_drop.sql").write_text(DROPS)
    (box.migrations / "0023_fail.sql").write_text(FAILING)
    result = box.apply()
    return (
        result.returncode != 0
        and "0022_drop.sql" in box.applied()
        and box.triggers() == set()
    )


CHECKS: tuple[tuple[str, Callable[[Path, Runner], bool]], ...] = (
    ("failing-file-rolls-back", check_failing_file_rolls_back),
    ("control-detects-split", check_control_detects_split),
)


def main(
    env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
) -> int:
    env = os.environ if env is None else env
    run: Runner = subprocess.run if runner is None else runner
    marker = ci_marker(env)
    if marker is not None and env.get(OWNER_RUN) != "1":
        print(f"REFUSED: {marker} is set; this check is for hand runs ({OWNER_RUN}=1)")
        return 2
    if not WRANGLER.exists():
        print("REFUSED: worker/node_modules is missing")
        print("Run: cd worker && npm ci --ignore-scripts")
        return 2
    node = node_version(run)
    if node is None or node < NODE_MIN:
        print("REFUSED: Node 22.12 or newer is required on PATH")
        return 2
    parent = SCRATCH_PARENT if SCRATCH_PARENT.is_dir() else None
    scratch = Path(tempfile.mkdtemp(prefix="rollback-check-", dir=parent))
    results: list[bool] = []
    try:
        for name, check in CHECKS:
            try:
                ok = check(scratch, run)
            except (Refused, OSError, subprocess.SubprocessError, sqlite3.Error):
                ok = False
            print(f"{'PASS' if ok else 'FAIL'} {name}")
            results.append(ok)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
