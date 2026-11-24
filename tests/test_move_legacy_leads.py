# SPDX-License-Identifier: Apache-2.0
"""scripts/move-legacy-leads.py: the one-time move of lead rows out of analytics.

A stub stands in for ``wrangler`` and answers from two local SQLite files built from
the Worker's own migration folders (analytics and leads). Everything is synthetic
(example.test addresses)."""

from __future__ import annotations

import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from test_check_dump import schema_sql

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "move-legacy-leads.py"
HOUR = 3_600_000

STUB = """#!{python}
import json, os, sqlite3, sys
args = sys.argv[1:]
name = args[2]
paths = {{"analytics-db": "STUB_SRC", "leads-db": "STUB_DEST"}}
path = os.environ[paths[name]]
db = sqlite3.connect(path)
db.execute("PRAGMA foreign_keys = ON")
if os.environ.get("STUB_FAIL_FILE") and any(a.startswith("--file=") for a in args):
    sys.exit(1)
if any(a.startswith("--file=") for a in args):
    sql = open([a for a in args if a.startswith("--file=")][0].split("=", 1)[1]).read()
    db.executescript(sql)
    db.commit()
    rows = []
else:
    sql = args[args.index("--command") + 1]
    cur = db.execute(sql)
    rows = []
    if cur.description:
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    db.commit()
print(json.dumps([{{"results": rows, "success": True}}]))
"""


def build(path: Path, folder: str) -> None:
    with closing(sqlite3.connect(path)) as db:
        for f in sorted((ROOT / "worker" / folder).glob("*.sql")):
            db.executescript(schema_sql(f.read_text()))
        db.commit()


def lead(db: sqlite3.Connection, n: int, email: str | None = None) -> str:
    ident = f"{n:032x}"
    db.execute(
        "INSERT INTO leads (id, email, created_ts, last_seen_ts, verified_ts,"
        " source, country) VALUES (?, ?, ?, ?, ?, 'gate', 'US')",
        (
            ident,
            email or f"person{n}@example.test",
            1000 + n,
            2000 + n,
            None if n % 2 else 3000 + n,
        ),
    )
    db.execute(
        "INSERT INTO kit_events (ts, lead_id, kind) VALUES (?, ?, 'unlock')",
        (HOUR * (n + 1), ident),
    )
    return ident


def make(tmp_path: Path, count: int = 3) -> dict[str, str]:
    src = tmp_path / "src.sqlite"
    dest = tmp_path / "dest.sqlite"
    build(src, "migrations")
    build(dest, "leads-migrations")
    with closing(sqlite3.connect(src)) as db:
        for n in range(1, count + 1):
            lead(db, n)
        db.commit()
    stub = tmp_path / "wrangler-stub"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o700)
    return {
        "PATH": "/usr/bin:/bin",
        "WRANGLER": str(stub),
        "STUB_SRC": str(src),
        "STUB_DEST": str(dest),
    }


def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def run(
    env: dict[str, str],
    work: Path,
    *flags: str,
    source: str = "analytics-db",
    dest: str = "leads-db",
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--source",
            source,
            "--dest",
            dest,
            "--workdir",
            str(work),
            *flags,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def count(path: str, table: str) -> int:
    with closing(sqlite3.connect(path)) as db:
        return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608 - fixed names


def rows(path: str, table: str) -> list[tuple]:
    with closing(sqlite3.connect(path)) as db:
        return db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()  # noqa: S608 - fixed names


def test_the_dry_run_counts_and_changes_nothing(tmp_path: Path) -> None:
    env = make(tmp_path)
    r = run(env, workdir(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "source holds 3 leads and 3 kit events" in r.stdout
    assert "dry run" in r.stdout
    assert count(env["STUB_SRC"], "leads") == 3
    assert count(env["STUB_DEST"], "leads") == 0
    assert count(env["STUB_DEST"], "kit_events") == 0


def test_copy_verifies_and_deletes_nothing(tmp_path: Path) -> None:
    env = make(tmp_path)
    r = run(env, workdir(tmp_path), "--copy")
    assert r.returncode == 0, r.stderr
    assert "0 leads and 0 kit events are not in dest" in r.stdout
    assert rows(env["STUB_SRC"], "leads") == rows(env["STUB_DEST"], "leads")
    assert rows(env["STUB_SRC"], "kit_events") == rows(env["STUB_DEST"], "kit_events")
    assert count(env["STUB_SRC"], "leads") == 3  # untouched


def test_purge_moves_every_row_then_empties_analytics(tmp_path: Path) -> None:
    env = make(tmp_path)
    before_leads = rows(env["STUB_SRC"], "leads")
    before_events = rows(env["STUB_SRC"], "kit_events")
    r = run(env, workdir(tmp_path), "--purge")
    assert r.returncode == 0, r.stderr
    assert count(env["STUB_SRC"], "leads") == 0
    assert count(env["STUB_SRC"], "kit_events") == 0
    assert rows(env["STUB_DEST"], "leads") == before_leads
    assert rows(env["STUB_DEST"], "kit_events") == before_events
    assert "example.test" not in r.stdout + r.stderr  # counts only, never an address
    assert list((tmp_path / "work").iterdir()) == []  # the private file is gone


def test_a_second_run_finds_nothing_to_move(tmp_path: Path) -> None:
    env = make(tmp_path)
    assert run(env, workdir(tmp_path), "--purge").returncode == 0
    again = run(env, tmp_path / "work", "--purge")
    assert again.returncode == 0
    assert "nothing to move" in again.stdout
    assert count(env["STUB_DEST"], "leads") == 3


def test_an_address_dest_already_holds_under_another_id_stops_before_any_delete(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        lead(
            db, 99, email="person1@example.test"
        )  # the same address as source lead 1, another id
        db.commit()
    r = run(env, workdir(tmp_path), "--purge")
    assert r.returncode == 1
    assert "nothing was deleted" in r.stderr
    assert count(env["STUB_SRC"], "leads") == 3
    assert count(env["STUB_SRC"], "kit_events") == 3


def test_a_failed_copy_deletes_nothing(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_FAIL_FILE"] = "1"
    r = run(env, workdir(tmp_path), "--purge")
    assert r.returncode == 1
    assert count(env["STUB_SRC"], "leads") == 3
    assert list((tmp_path / "work").iterdir()) == []


def test_identity_is_read_from_the_databases_not_from_their_names(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    swapped = run(
        env, workdir(tmp_path), "--purge", source="leads-db", dest="analytics-db"
    )
    assert swapped.returncode == 3
    assert "did not identify itself" in swapped.stderr
    same = run(
        env, tmp_path / "work", "--purge", source="analytics-db", dest="analytics-db"
    )
    assert same.returncode == 3
    assert count(env["STUB_SRC"], "leads") == 3


def test_a_dest_that_never_said_what_it_is_is_refused(tmp_path: Path) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        db.execute("DROP TABLE db_identity")
        db.commit()
    r = run(env, workdir(tmp_path), "--purge")
    assert r.returncode == 3
    assert count(env["STUB_SRC"], "leads") == 3


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o707])
def test_an_open_workdir_is_refused(tmp_path: Path, mode: int) -> None:
    env = make(tmp_path)
    d = workdir(tmp_path)
    d.chmod(mode)
    r = run(env, d, "--purge")
    assert r.returncode == 3
    assert stat.S_IMODE(d.stat().st_mode) == mode
    assert count(env["STUB_SRC"], "leads") == 3


def test_a_workdir_inside_a_git_work_tree_is_refused(tmp_path: Path) -> None:
    env = make(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # noqa: S603, S607
    inner = repo / "work"
    inner.mkdir(mode=0o700)
    inner.chmod(0o700)
    r = run(env, inner, "--purge")
    assert r.returncode == 3
    assert count(env["STUB_SRC"], "leads") == 3


def test_a_bad_database_name_is_a_usage_error(tmp_path: Path) -> None:
    env = make(tmp_path)
    r = run(env, workdir(tmp_path), source="a b")
    assert r.returncode == 2
