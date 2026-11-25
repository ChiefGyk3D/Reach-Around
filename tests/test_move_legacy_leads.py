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
from typing import Any

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
    inject = os.environ.get("STUB_INJECT")
    if inject and name == "analytics-db" and "DELETE FROM" in sql:
        # An older Worker writes analytics between the copy and the delete.
        db.execute(
            "INSERT INTO leads (id, email, created_ts, last_seen_ts, source) "
            "VALUES ('" + "f" * 32 + "', 'late@example.test', 1, 1, 'gate')"
        )
        db.execute(
            "INSERT INTO kit_events (ts, lead_id, kind) VALUES (7200000, '"
            + format(1, "032x")
            + "', 'view')"
        )
        if inject == "edit":
            db.execute(
                "UPDATE leads SET last_seen_ts = 99999 WHERE id = '"
                + format(2, "032x")
                + "'"
            )
        db.commit()
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


def rows(path: str, table: str) -> list[tuple[Any, ...]]:
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


@pytest.mark.parametrize("inject", ["insert", "edit"])
def test_rows_added_or_changed_after_the_copy_survive_and_are_reported(
    tmp_path: Path, inject: str
) -> None:
    # Round 2: an unconditional delete removed rows that were never copied.
    env = make(tmp_path)
    env["STUB_INJECT"] = inject
    r = run(env, workdir(tmp_path), "--purge")
    assert r.returncode == 1
    assert "stop older Workers and run again" in r.stderr
    late = "f" * 32
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        ids = {row[0] for row in db.execute("SELECT id FROM leads")}
        events = db.execute("SELECT lead_id, kind FROM kit_events").fetchall()
    # The lead added after the copy is still there, and so is its (never copied) event
    # on lead 1, which keeps lead 1 too.
    assert late in ids
    one, two, three = (format(n, "032x") for n in (1, 2, 3))
    assert (one, "view") in events
    assert one in ids
    if inject == "edit":
        assert two in ids  # changed after the copy: not the verified row
    else:
        assert two not in ids and three not in ids  # verified and moved
    # Every verified row is in the leads database exactly as it was.
    assert count(env["STUB_DEST"], "leads") == 3
    assert "example.test" not in r.stdout + r.stderr


def test_a_second_run_moves_what_the_first_left(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_INJECT"] = "insert"
    assert run(env, workdir(tmp_path), "--purge").returncode == 1
    del env["STUB_INJECT"]
    again = run(env, tmp_path / "work", "--purge")
    assert again.returncode == 0, again.stderr
    assert count(env["STUB_SRC"], "leads") == 0
    assert count(env["STUB_DEST"], "leads") == 4


def test_the_temporary_file_is_never_a_fixed_name_a_symlink_can_hijack(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    (work / "move-legacy-leads.sql").symlink_to(victim)
    (work / "statements.sql").symlink_to(victim)
    r = run(env, work, "--purge")
    assert r.returncode == 0, r.stderr
    assert victim.read_text() == "keep me"
    assert sorted(p.name for p in work.iterdir()) == [
        "move-legacy-leads.sql",
        "statements.sql",
    ]  # only the planted links remain: the script's own directory is gone


def test_the_statement_file_is_private_and_removed_when_a_step_fails(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_FAIL_FILE"] = "1"
    work = workdir(tmp_path)
    assert run(env, work, "--purge").returncode == 1
    assert list(work.iterdir()) == []


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


# ---- the cutover: freeze, then move, then verify (round 3) ----------------------


def test_freeze_makes_the_database_refuse_a_lead_from_an_older_worker(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    assert run(env, work, "--freeze").returncode == 0  # idempotent
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO leads (id, email, created_ts, last_seen_ts, source)"
                " VALUES (?, 'old@example.test', 1, 1, 'gate')",
                ("e" * 32,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO kit_events (ts, lead_id, kind)"
                " VALUES (3600000, ?, 'unlock')",
                (format(1, "032x"),),
            )
    assert count(env["STUB_SRC"], "leads") == 3  # nothing deleted by freezing


def test_verify_passes_only_when_empty_and_frozen(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    rows = run(env, work, "--verify")
    assert rows.returncode == 1
    assert "lead rows remain" in rows.stderr
    assert run(env, work, "--purge").returncode == 0
    open_ = run(env, work, "--verify")  # empty, but an older Worker could still insert
    assert open_.returncode == 1
    assert "older Worker" in open_.stderr
    assert run(env, work, "--freeze").returncode == 0
    done = run(env, work, "--verify")
    assert done.returncode == 0, done.stderr
    assert "cutover verified" in done.stdout


def test_verify_passes_when_the_lead_tables_are_gone(tmp_path: Path) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DROP TABLE kit_events")
        db.execute("DROP TABLE leads")
        db.commit()
    assert run(env, workdir(tmp_path), "--verify").returncode == 0


def test_freeze_and_verify_refuse_a_database_that_is_not_analytics(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    for flag in ("--freeze", "--verify"):
        r = run(env, work, flag, source="leads-db", dest="analytics-db")
        assert r.returncode == 3


# ---- the temporary file guarantees, proved at the call (round 3) ----------------


def load_script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("move_legacy_leads", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordingWrangler:
    """Stands in for Wrangler: records what the statement file looked like."""

    def __init__(self) -> None:
        self.seen: list[tuple[int, int, str]] = []

    def execute(
        self, db: str, *, command: str | None = None, file: Path | None = None
    ) -> list[dict[str, Any]]:
        assert file is not None
        self.seen.append(
            (
                stat.S_IMODE(file.stat().st_mode),
                stat.S_IMODE(file.parent.stat().st_mode),
                file.read_text(),
            )
        )
        return []


def test_the_statement_file_is_opened_exclusive_nofollow_and_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    mod = load_script()
    opened: list[tuple[int, int]] = []
    real_open = os.open

    def spy(path: Any, flags: int, mode: int = 0o777, *a: Any, **k: Any) -> int:
        if str(path).endswith("statements.sql"):
            opened.append((flags, mode))
        return real_open(path, flags, mode, *a, **k)

    monkeypatch.setattr(mod.os, "open", spy)
    w = RecordingWrangler()
    mod.run_file(w, "db", workdir(tmp_path), "SELECT 1;\n")
    ((flags, mode),) = opened
    assert flags & os.O_EXCL and flags & os.O_CREAT
    assert flags & os.O_NOFOLLOW
    assert not flags & os.O_TRUNC
    assert mode == 0o600
    assert w.seen == [(0o600, 0o700, "SELECT 1;\n")]
    assert list((tmp_path / "work").iterdir()) == []  # directory removed


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_file_or_symlink_already_at_the_target_is_refused_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    scratch = work / "fixed-scratch"
    scratch.mkdir(mode=0o700)
    target = scratch / "statements.sql"
    if kind == "file":
        target.write_text("keep me")
    else:
        target.symlink_to(victim)
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_: str(scratch))
    w = RecordingWrangler()
    with pytest.raises(OSError):  # noqa: PT011 - FileExistsError, or ELOOP
        mod.run_file(w, "db", work, "DROP TABLE x;\n")
    assert w.seen == []  # nothing was executed
    assert victim.read_text() == "keep me"


# ---- round 4: the freeze is proved by definition, never by name -----------------

HOSTILE = (
    "CREATE TRIGGER {name} BEFORE DELETE ON {table} "
    "BEGIN SELECT 1; END"  # same name, does not refuse an insert
)


def plant(env: dict[str, str], name: str, table: str) -> None:
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute(HOSTILE.format(name=name, table=table))
        db.commit()


@pytest.mark.parametrize(
    ("name", "table"), [("leads_frozen", "leads"), ("kit_events_frozen", "kit_events")]
)
def test_a_same_name_trigger_that_does_not_refuse_is_not_a_freeze(
    tmp_path: Path, name: str, table: str
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    plant(env, name, table)
    assert run(env, work, "--purge").returncode == 0
    frozen = run(env, work, "--freeze")
    assert frozen.returncode == 1  # fails closed, does not claim a freeze
    assert "differs" in frozen.stderr
    checked = run(env, work, "--verify")
    assert checked.returncode == 1
    assert "older Worker" in checked.stderr
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:  # an insert still lands
        db.execute(
            "INSERT INTO leads (id, email, created_ts, last_seen_ts, source)"
            " VALUES (?, 'old@example.test', 1, 1, 'gate')",
            ("d" * 32,),
        )


def test_a_same_name_trigger_on_another_table_is_not_a_freeze(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    plant(env, "leads_frozen", "kit_events")  # right names, swapped tables
    plant(env, "kit_events_frozen", "leads")
    assert run(env, work, "--purge").returncode == 0
    assert run(env, work, "--verify").returncode == 1


def test_a_local_file_error_prints_a_fixed_message_only(tmp_path: Path) -> None:
    env = make(tmp_path)
    canary = "canary-secret-dir"
    env["WRANGLER"] = str(tmp_path / canary / "missing-wrangler")  # FileNotFoundError
    r = run(env, workdir(tmp_path), "--freeze")
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert canary not in r.stdout + r.stderr
    assert "local file or process error" in r.stderr
