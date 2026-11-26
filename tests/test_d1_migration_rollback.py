# SPDX-License-Identifier: Apache-2.0
"""A failed D1 migration is rolled back whole, on the pinned Wrangler (#907).

Migration 0022 drops the two kit_meta floor guard triggers and creates them again
in one file. That is safe only if ``wrangler d1 migrations apply`` rolls a failing
file back as a unit (the Wrangler D1 command reference says so). The Worker pool
test helper runs statements one at a time, so nothing else in the suite exercises
the property. This file does: it runs the real pinned Wrangler against a
disposable LOCAL D1 database, applies the real migrations up to 0021, then applies
a deliberately failing file that drops both triggers and fails afterwards.

Hermetic: ``--local`` with a temp persist directory, a minimal temp Wrangler
config, a minimal environment (no CLOUDFLARE_* variables, HOME in a temp
directory) and a timeout on every command. Developer coverage, not a CI gate:
SKIPPED when ``worker/node_modules`` is not installed (cd worker && npm ci
--ignore-scripts) or when the Node on PATH is older than the Wrangler minimum.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MIG_DIR = ROOT / "worker" / "migrations"
WRANGLER = ROOT / "worker" / "node_modules" / ".bin" / "wrangler"
TRIGGERS = {"kit_meta_floor_insert", "kit_meta_floor_update"}
TIMEOUT = 180
NODE_MIN = (22, 12)


def _node_version() -> tuple[int, ...] | None:
    node = shutil.which("node")
    if node is None:
        return None
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv: node from PATH, --version
            [node, "--version"], capture_output=True, text=True, timeout=20, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.match(r"v(\d+)\.(\d+)", out.strip())
    return (int(found.group(1)), int(found.group(2))) if found else None


_NODE = _node_version()
_NO_DEPS = not WRANGLER.exists()
_OLD_NODE = _NODE is None or _NODE < NODE_MIN

pytestmark = [
    pytest.mark.allow_local_wrangler,
    pytest.mark.skipif(_NO_DEPS, reason="worker/node_modules not installed"),
    pytest.mark.skipif(_OLD_NODE, reason="Node 22.12+ is required by Wrangler"),
]

_CONFIG = """name = "rollback-probe"
main = "index.js"
compatibility_date = "2026-10-01"

[[d1_databases]]
binding = "DB"
database_name = "rollback-probe-db"
database_id = "00000000-0000-0000-0000-000000000000"
migrations_dir = "migrations"
"""

_DROPS = (
    "DROP TRIGGER IF EXISTS kit_meta_floor_insert;\n"
    "DROP TRIGGER IF EXISTS kit_meta_floor_update;\n"
)


class Sandbox:
    """One disposable local D1 database with its own copy of the migrations."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.home = base / "home"
        self.state = base / "state"
        self.migrations = base / "migrations"
        self.home.mkdir()
        self.migrations.mkdir()
        (base / "wrangler.toml").write_text(_CONFIG)
        (base / "index.js").write_text("export default {};\n")
        for path in sorted(MIG_DIR.glob("*.sql")):
            if path.name < "0022":
                shutil.copy(path, self.migrations / path.name)

    def apply(self) -> subprocess.CompletedProcess[str]:
        node = shutil.which("node")
        assert node is not None
        env = {
            "PATH": f"{Path(node).parent}:/usr/bin:/bin",
            "HOME": str(self.home),
            "WRANGLER_SEND_METRICS": "false",
            "CI": "1",
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
        return subprocess.run(  # noqa: S603 - pinned wrangler, --local, clean env
            cmd,
            cwd=self.base,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )

    def _db(self) -> Path:
        found = [
            p
            for p in (self.state / "v3" / "d1").rglob("*.sqlite")
            if p.name != "metadata.sqlite"
        ]
        assert len(found) == 1, found
        return found[0]

    def query(self, sql: str) -> set[str]:
        con = sqlite3.connect(f"file:{self._db()}?mode=ro", uri=True)
        try:
            return {row[0] for row in con.execute(sql)}
        finally:
            con.close()

    def triggers(self) -> set[str]:
        return self.query("SELECT name FROM sqlite_master WHERE type = 'trigger'")

    def applied(self) -> set[str]:
        return self.query("SELECT name FROM d1_migrations")


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    box = Sandbox(tmp_path)
    first = box.apply()
    assert first.returncode == 0, first.stdout[-1500:] + first.stderr[-1500:]
    assert box.triggers() == TRIGGERS
    assert "0021_kit_floor_guard.sql" in box.applied()
    return box


def test_a_failing_migration_file_is_rolled_back_whole(sandbox: Sandbox) -> None:
    """Drop both triggers, then fail inside the same file: both triggers survive."""
    failing = _DROPS + "THIS IS NOT VALID SQL;\n"
    (sandbox.migrations / "0022_fail.sql").write_text(failing)
    result = sandbox.apply()
    assert result.returncode != 0, result.stdout[-1500:]
    assert "syntax error" in result.stdout + result.stderr
    assert sandbox.triggers() == TRIGGERS
    assert "0022_fail.sql" not in sandbox.applied()


def test_the_check_detects_a_non_atomic_split(sandbox: Sandbox) -> None:
    """Control: when the drop is its own migration it commits, and the failure of the
    next file cannot bring the triggers back. This is what the test above would see
    if a failing file were not rolled back as a unit."""
    (sandbox.migrations / "0022_drop.sql").write_text(_DROPS)
    (sandbox.migrations / "0023_fail.sql").write_text("THIS IS NOT VALID SQL;\n")
    result = sandbox.apply()
    assert result.returncode != 0, result.stdout[-1500:]
    assert "0022_drop.sql" in sandbox.applied()
    assert sandbox.triggers() == set()
