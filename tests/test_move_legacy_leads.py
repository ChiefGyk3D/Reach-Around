# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S608
# S608: statements are built from fixed names and synthetic ids only.
"""scripts/move-legacy-leads.py: the one-time move of lead rows out of analytics.

A stub stands in for ``wrangler`` and answers from two local SQLite files built from
the Worker's own migration folders (analytics and leads). Everything is synthetic
(example.test addresses)."""

from __future__ import annotations

import itertools
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from test_check_dump import schema_sql

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "move-legacy-leads.py"
HOUR = 3_600_000

BOOKMARK = "00000085-0000024c-00004c6d-8e61117bf38d7adb71b934ebbf891683"

STUB = """#!{python}
import json, os, sqlite3, sys
args = sys.argv[1:]
name = args[2] if args[:2] != ["d1", "time-travel"] else args[3]
log = os.environ.get("STUB_LOG")
if args[:2] == ["d1", "info"]:
    if os.environ.get("STUB_INFO_BAD"):
        print("not json")
        sys.exit(0)
    ids = {{
        "analytics-db": os.environ["STUB_SRC_ID"],
        "leads-db": os.environ["STUB_DEST_ID"],
    }}
    print(json.dumps({{"uuid": ids[name], "name": name, "version": "production"}}))
    sys.exit(0)
if args[:2] == ["d1", "time-travel"]:
    if log:
        open(log, "a").write("bookmark\\n")
    if os.environ.get("STUB_BOOKMARK_FAIL"):
        sys.exit(1)
    text = os.environ.get("STUB_BOOKMARK_TEXT")
    if text == "plain":
        print("The current bookmark is '" + "{bookmark}" + "'")
    elif text:
        print(text)
    else:
        print(json.dumps({{"bookmark": "{bookmark}"}}))
    sys.exit(0)
paths = {{"analytics-db": "STUB_SRC", "leads-db": "STUB_DEST"}}
path = os.environ[paths[name]]
db = sqlite3.connect(path)
db.execute("PRAGMA foreign_keys = ON")
blocks = None
is_file = any(a.startswith("--file=") for a in args)
if is_file:
    sql = open([a for a in args if a.startswith("--file=")][0].split("=", 1)[1]).read()
else:
    sql = args[args.index("--command") + 1]
# A statement that changes rows: a statement file, or one DELETE through --command.
writes = is_file or sql.lstrip().startswith("DELETE FROM")
if os.environ.get("STUB_FAIL_FILE") and writes:
    sys.exit(1)
mark = os.environ.get("STUB_MARK")
if mark and is_file and os.path.exists(mark):
    sys.exit(1)  # every file fails once the changed row was deleted
if os.environ.get("CRASH_AT") and writes:
    counter = os.environ["STUB_COUNTER"]
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    if n + 1 == int(os.environ["CRASH_AT"]):
        sys.exit(1)
if writes:
    target = "DELETE FROM leads WHERE id IS '" + format(2, "032x") + "'"
    if os.environ.get("STUB_INJECT") == "email" and sql.startswith(target):
        # An older Worker changes the address after the tool read the row.
        db.execute(
            "UPDATE leads SET email = 'changed@example.test' WHERE id = '"
            + format(2, "032x")
            + "'"
        )
        db.commit()
        if os.environ.get("STUB_MARK"):
            open(os.environ["STUB_MARK"], "w").write("x")
    if log and name == "analytics-db" and "DELETE FROM" in sql:
        open(log, "a").write("delete-file\\n" if is_file else "delete\\n")
    inject = os.environ.get("STUB_INJECT")
    done = db.execute("SELECT 1 FROM leads WHERE id = '" + "f" * 32 + "'").fetchone()
    if inject and name == "analytics-db" and "DELETE FROM" in sql and not done:
        # An older Worker writes analytics between the copy and the delete. The freeze
        # would refuse an insert, so "insert" stands for a freeze someone removed.
        if inject in ("insert", "unfreeze"):
            saved = db.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN ('leads_frozen', 'kit_events_frozen')"
            ).fetchall()
            db.execute("DROP TRIGGER IF EXISTS leads_frozen")
            db.execute("DROP TRIGGER IF EXISTS kit_events_frozen")
        if inject == "insert":
            db.execute(
                "INSERT INTO leads (id, email, created_ts, last_seen_ts, source) "
                "VALUES ('" + "f" * 32 + "', 'late@example.test', 1, 1, 'gate')"
            )
            db.execute(
                "INSERT INTO kit_events (ts, lead_id, kind) VALUES (7200000, '"
                + format(1, "032x")
                + "', 'view')"
            )
            for (trigger_sql,) in saved:  # frozen again, as the freeze would have it
                db.execute(trigger_sql)
        if inject == "edit":
            db.execute(
                "UPDATE leads SET last_seen_ts = 99999 WHERE id = '"
                + format(2, "032x")
                + "'"
            )
        db.commit()
    if is_file:
        # Real wrangler 4.162.0 (remote, owner's check 2026-11-27): ONE block whose
        # results hold a single summary row, never the rows of any statement.
        db.executescript(sql)
        db.commit()
        blocks = [
            {{
                "results": [
                    {{
                        "Total queries executed": sql.count(";"),
                        "Rows read": 0,
                        "Rows written": 0,
                        "Database size (MB)": "0.05",
                    }}
                ],
                "success": True,
            }}
        ]
    elif os.environ.get("STUB_OTHER_CLIENT") and name == "analytics-db":
        # Another client removes the rows; this statement matches nothing.
        db.execute("DELETE FROM " + sql.split()[2] + " WHERE 1")
        db.commit()
        blocks = [{{"results": [], "success": True}}]
    else:  # one DELETE ... RETURNING: its own block holds the rows it removed
        cur = db.execute(sql)
        cols = [c[0] for c in cur.description or []]
        got = cur.fetchall()
        db.commit()
        blocks = [{{"results": [dict(zip(cols, r)) for r in got], "success": True}}]
    lost = os.environ.get("STUB_DELETE_THEN_FAIL")
    if lost and name == "analytics-db" and "DELETE FROM" in sql:
        sys.exit(1)  # applied, but the caller never hears it
else:
    cur = db.execute(sql)
    rows = []
    if cur.description:
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    db.commit()
print(json.dumps(blocks or [{{"results": rows, "success": True}}]))
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
    stub.write_text(STUB.format(python=sys.executable, bookmark=BOOKMARK))
    stub.chmod(0o700)
    return {
        "PATH": "/usr/bin:/bin",
        "WRANGLER": str(stub),
        "STUB_SRC": str(src),
        "STUB_DEST": str(dest),
        "STUB_SRC_ID": str(uuid.uuid4()),
        "STUB_DEST_ID": str(uuid.uuid4()),
        "MOVE_SETTLE": "0",
    }


def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def ids_of(env: dict[str, str]) -> dict[str, str]:
    return {"analytics-db": env["STUB_SRC_ID"], "leads-db": env["STUB_DEST_ID"]}


def run(
    env: dict[str, str],
    work: Path,
    *flags: str,
    source: str = "analytics-db",
    dest: str = "leads-db",
    window: bool = True,
    source_id: str | None = None,
    dest_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    known = ids_of(env)
    extra = ["--locked-window-confirmed"] if window and "--purge" in flags else []
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--source",
            source,
            "--source-id",
            source_id or known.get(source, str(uuid.uuid4())),
            "--dest",
            dest,
            "--dest-id",
            dest_id or known.get(dest, str(uuid.uuid4())),
            "--workdir",
            str(work),
            *flags,
            *extra,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def purge(env: dict[str, str], work: Path) -> subprocess.CompletedProcess[str]:
    """--purge needs the freeze first (it refuses otherwise)."""
    assert run(env, work, "--freeze").returncode == 0
    return run(env, work, "--purge")


def moved(env: dict[str, str], work: Path) -> None:
    """Copy through the script, then empty analytics by hand (a purge's end state)."""
    assert run(env, work, "--copy").returncode == 0
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DELETE FROM kit_events")
        db.execute("DELETE FROM leads")
        db.commit()


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
    r = purge(env, workdir(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "cutover verified" in r.stdout  # the final check ran in the same run
    assert count(env["STUB_SRC"], "leads") == 0
    assert count(env["STUB_SRC"], "kit_events") == 0
    assert rows(env["STUB_DEST"], "leads") == before_leads
    assert rows(env["STUB_DEST"], "kit_events") == before_events
    assert "example.test" not in r.stdout + r.stderr  # counts only, never an address
    # Statement files are gone; the private manifest and recovery export remain.
    kept = sorted((tmp_path / "work").iterdir())
    assert [path.name for path in kept if path.name == "move-manifest.json"] == [
        "move-manifest.json"
    ]
    exports = [path for path in kept if path.name.startswith("lead-recovery-")]
    assert len(exports) == 1
    assert exports[0].name.endswith(".json")
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in kept)
    manifest = tmp_path / "work" / "move-manifest.json"
    assert "example.test" not in manifest.read_text()


def test_a_second_run_finds_nothing_to_move(tmp_path: Path) -> None:
    env = make(tmp_path)
    assert purge(env, workdir(tmp_path)).returncode == 0
    again = run(env, tmp_path / "work", "--purge")
    assert again.returncode == 0
    assert "cutover verified" in again.stdout  # done only because verify passed
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
    r = purge(env, workdir(tmp_path))
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
    r = purge(env, workdir(tmp_path))
    assert r.returncode == 4  # some chunks were deleted, the rest were not
    assert "partial purge" in r.stderr
    assert "stop older Workers and run again" in r.stderr
    late = "f" * 32
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        ids = {row[0] for row in db.execute("SELECT id FROM leads")}
        events = db.execute("SELECT lead_id, kind FROM kit_events").fetchall()
    # The lead added after the copy is still there, and so is its (never copied) event
    # on lead 1, which keeps lead 1 too.
    one, two, three = (format(n, "032x") for n in (1, 2, 3))
    if inject == "insert":
        assert late in ids
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
    assert purge(env, workdir(tmp_path)).returncode == 4
    del env["STUB_INJECT"]
    again = purge(env, tmp_path / "work")  # freezes again, then moves what was left
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
    r = purge(env, work)
    assert r.returncode == 0, r.stderr
    assert victim.read_text() == "keep me"
    names = sorted(
        p.name for p in work.iterdir() if p.name != LOCK_FILE
    )  # r13: permanent lock file
    recovery = [name for name in names if name.startswith("lead-recovery-")]
    assert len(recovery) == 1
    assert recovery[0].endswith(".json")
    assert names == sorted(
        [
            "move-legacy-leads.sql",
            "move-manifest.json",
            "statements.sql",
            recovery[0],
        ]
    )
    assert stat.S_IMODE((work / recovery[0]).stat().st_mode) == 0o600


def test_the_statement_file_is_private_and_removed_when_a_step_fails(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_FAIL_FILE"] = "1"
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 1
    assert [p.name for p in work.iterdir() if p.name != LOCK_FILE] == [
        "move-manifest.json"
    ]  # no sql file (r13: the lock file is permanent)


def test_a_failed_copy_deletes_nothing(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_FAIL_FILE"] = "1"
    r = purge(env, workdir(tmp_path))
    assert r.returncode == 1
    assert count(env["STUB_SRC"], "leads") == 3
    assert [p.name for p in (tmp_path / "work").iterdir() if p.name != LOCK_FILE] == [
        "move-manifest.json"
    ]  # r13: the lock file is permanent


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


_MODES = ("--copy", "--purge", "--freeze", "--verify")


@pytest.mark.parametrize(("first", "second"), list(itertools.combinations(_MODES, 2)))
def test_conflicting_modes_are_refused_before_any_write(
    tmp_path: Path, first: str, second: str
) -> None:
    # Round 6: only one mode used to run, with exit 0, and the other was ignored.
    env = make(tmp_path)
    before_src = count(env["STUB_SRC"], "leads")
    before_dest = count(env["STUB_DEST"], "leads")
    work = workdir(tmp_path)
    for order in ((first, second), (second, first)):
        r = run(env, work, *order)
        assert r.returncode == 2, r.stdout
        assert "not allowed with" in r.stderr
        assert r.stdout == ""
    assert count(env["STUB_SRC"], "leads") == before_src
    assert count(env["STUB_DEST"], "leads") == before_dest


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
    moved(env, work)  # empty and complete, but an older Worker could still insert
    open_ = run(env, work, "--verify")
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
    moved(env, work)
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
    moved(env, work)
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


# ---- round 8: --verify proves the destination, --purge needs a freeze, chunks ----


def src_rows(env: dict[str, str]) -> int:
    return count(env["STUB_SRC"], "leads") + count(env["STUB_SRC"], "kit_events")


def frozen_and_moved(env: dict[str, str], work: Path) -> None:
    moved(env, work)
    assert run(env, work, "--freeze").returncode == 0
    assert run(env, work, "--verify").returncode == 0  # the baseline the tests break


def dest_exec(env: dict[str, str], *statements: str) -> None:
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        db.execute("PRAGMA foreign_keys = ON")
        for sql in statements:
            db.execute(sql)
        db.commit()


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing", "2 missing"),
        ("changed", "1 different"),
        ("extra", "2 extra"),
    ],
)
def test_verify_fails_unless_dest_holds_exactly_the_moved_rows(
    tmp_path: Path, case: str, message: str
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    frozen_and_moved(env, work)
    one = format(1, "032x")
    if case == "missing":
        # kit_events cascades with the lead, so the lead's one event goes as well.
        dest_exec(env, f"DELETE FROM kit_events WHERE lead_id = '{one}'")
        dest_exec(env, f"DELETE FROM leads WHERE id = '{one}'")
    elif case == "changed":
        dest_exec(env, f"UPDATE leads SET last_seen_ts = 424242 WHERE id = '{one}'")
    else:
        with closing(sqlite3.connect(env["STUB_DEST"])) as db:
            lead(db, 50)
            db.commit()
    r = run(env, work, "--verify")
    assert r.returncode == 1
    assert "does not hold exactly the moved rows" in r.stderr
    assert message in r.stdout  # a lead and its event each count
    assert "example.test" not in r.stdout + r.stderr


def test_verify_fails_when_only_part_was_copied_then_analytics_emptied(
    tmp_path: Path,
) -> None:
    # The round 7 attack: copy some rows, empty analytics by hand, then verify.
    env = make(tmp_path)
    work = workdir(tmp_path)
    moved(env, work)
    for n in (2, 3):
        i = format(n, "032x")
        dest_exec(env, f"DELETE FROM kit_events WHERE lead_id = '{i}'")
        dest_exec(env, f"DELETE FROM leads WHERE id = '{i}'")
    assert run(env, work, "--freeze").returncode == 0
    r = run(env, work, "--verify")
    assert r.returncode == 1
    assert "4 missing" in r.stdout
    assert count(env["STUB_DEST"], "leads") == 1


def test_verify_with_no_manifest_cannot_prove_a_filled_dest(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DELETE FROM kit_events")
        db.execute("DELETE FROM leads")
        db.commit()
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        lead(db, 60)
        db.commit()
    assert run(env, work, "--freeze").returncode == 0
    r = run(env, work, "--verify")
    assert r.returncode == 1
    assert "no manifest" in r.stderr


def test_verify_refuses_a_manifest_that_is_unfinished_or_for_other_names(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    frozen_and_moved(env, work)
    manifest = work / "move-manifest.json"
    good = manifest.read_text()
    manifest.write_text(good.replace('"state": "copied"', '"state": "copying"'))
    r = run(env, work, "--verify")
    assert r.returncode == 1
    assert "did not finish" in r.stderr
    manifest.write_text(good.replace('"source": "analytics-db"', '"source": "other"'))
    other = run(env, work, "--verify")
    assert other.returncode == 3
    manifest.write_text("not json")
    assert run(env, work, "--verify").returncode == 3


def test_purge_refuses_unless_the_source_is_frozen_before_any_write(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    r = run(env, work, "--purge")
    assert r.returncode == 3
    assert "--freeze first" in r.stderr
    assert src_rows(env) == 6
    assert count(env["STUB_DEST"], "leads") == 0  # not even the copy ran
    assert [
        p for p in work.iterdir() if p.name != LOCK_FILE
    ] == []  # no manifest (r13: permanent lock file)


def test_purge_refuses_when_only_one_table_is_frozen_or_the_freeze_is_fake(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    plant(env, "leads_frozen", "leads")  # right name, does not refuse an insert
    r = run(env, work, "--purge")
    assert r.returncode == 3
    assert src_rows(env) == 6
    assert count(env["STUB_DEST"], "leads") == 0


def test_purge_refuses_before_any_delete_when_dest_lost_a_row_after_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    real = mod.copy_chunks

    def lossy(*a: Any, **k: Any) -> None:
        real(*a, **k)
        i = format(1, "032x")
        dest_exec(env, f"DELETE FROM kit_events WHERE lead_id = '{i}'")
        dest_exec(env, f"DELETE FROM leads WHERE id = '{i}'")

    monkeypatch.setattr(mod, "copy_chunks", lossy)
    assert mod.main(main_args(work, "--purge")) == 1
    assert src_rows(env) == 6  # nothing was deleted


def test_purge_runs_the_final_verification_in_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    def bad_verify(*a: Any, **k: Any) -> int:
        raise mod.Failed("final check failed")

    monkeypatch.setattr(mod, "verify", bad_verify)
    # Everything was deleted, then the final check failed: a delete happened and the
    # outcome is not proved, so this is its own exit code (5), never "done".
    assert mod.main(main_args(work, "--purge")) == 5


def test_bounded_chunks_copy_and_purge_everything(tmp_path: Path) -> None:
    env = make(tmp_path, count=7)
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode == 0, r.stderr
    assert src_rows(env) == 0
    assert count(env["STUB_DEST"], "leads") == 7
    assert count(env["STUB_DEST"], "kit_events") == 7
    assert run(env, work, "--verify").returncode == 0


@pytest.mark.parametrize("bad", ["0", "1001", "x", ""])
def test_the_batch_size_is_bounded(tmp_path: Path, bad: str) -> None:
    env = make(tmp_path)
    env["MOVE_BATCH"] = bad
    r = run(env, workdir(tmp_path), "--copy")
    assert r.returncode != 0
    assert "MOVE_BATCH" in r.stderr
    assert count(env["STUB_DEST"], "leads") == 0


def test_a_crash_mid_copy_resumes_from_the_cursor_and_is_idempotent(
    tmp_path: Path,
) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    # Crash: the stub fails on its 2nd statement file (the second leads chunk).
    env["STUB_COUNTER"] = str(tmp_path / "calls")
    env["CRASH_AT"] = "2"
    r = run(env, work, "--copy")
    assert r.returncode == 1
    manifest = (work / "move-manifest.json").read_text()
    assert '"copying"' in manifest
    assert count(env["STUB_DEST"], "leads") == 2  # one chunk landed
    del env["CRASH_AT"]
    again = run(env, work, "--copy")
    assert again.returncode == 0, again.stderr
    assert "resuming" in again.stdout
    assert rows(env["STUB_SRC"], "leads") == rows(env["STUB_DEST"], "leads")
    assert rows(env["STUB_SRC"], "kit_events") == rows(env["STUB_DEST"], "kit_events")
    once = run(env, work, "--copy")  # idempotent: a third run changes nothing
    assert once.returncode == 0
    assert count(env["STUB_DEST"], "leads") == 5


# ---- round 9: delete is conditional on the destination and the freeze at delete -----


def load_with_env(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return load_script()


def main_args(work: Path, *flags: str) -> list[str]:
    extra = ["--locked-window-confirmed"] if "--purge" in flags else []
    return [
        "--source",
        "analytics-db",
        "--source-id",
        os.environ["STUB_SRC_ID"],
        "--dest",
        "leads-db",
        "--dest-id",
        os.environ["STUB_DEST_ID"],
        "--workdir",
        str(work),
        *flags,
        *extra,
    ]


def test_a_dest_row_lost_after_the_proof_stops_before_that_rows_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)
    real = mod.check_dest

    def then_lose(*a: Any, **k: Any) -> None:
        real(*a, **k)  # the whole-destination proof passes ...
        i = format(1, "032x")  # ... and then another process removes a row
        dest_exec(env, f"DELETE FROM kit_events WHERE lead_id = '{i}'")
        dest_exec(env, f"DELETE FROM leads WHERE id = '{i}'")

    monkeypatch.setattr(mod, "check_dest", then_lose)
    assert mod.main(main_args(work, "--purge")) == 1
    assert src_rows(env) == 6  # nothing was deleted: the lost row still has a copy


def test_a_dest_row_altered_between_chunks_stops_the_purge_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path, count=4)
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)
    real = mod.Wrangler.execute
    deletes: list[str] = []

    def spy(self: Any, db: str, *, command: str | None = None, file: Any = None) -> Any:
        out = real(self, db, command=command, file=file)
        if db == "analytics-db" and (command or "").startswith("DELETE"):
            deletes.append(command or "")
            if len(deletes) == 2:  # after the first chunk (2 rows): alter a later row
                dest_exec(
                    env,
                    "UPDATE kit_events SET ts = ts + 1 WHERE lead_id = '"
                    + format(4, "032x")
                    + "'",
                )
        return out

    monkeypatch.setattr(mod.Wrangler, "execute", spy)
    assert mod.main(main_args(work, "--purge")) == 4  # partial, safe to resume
    assert len(deletes) == 2  # no delete was sent after the change
    assert count(env["STUB_SRC"], "kit_events") == 2  # the second chunk survives
    assert count(env["STUB_SRC"], "leads") == 4


def test_the_freeze_removed_just_before_the_delete_deletes_nothing(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_INJECT"] = "unfreeze"  # the triggers vanish as the DELETE arrives
    r = purge(env, workdir(tmp_path))
    assert r.returncode == 1
    assert "freeze is gone" in r.stderr
    assert src_rows(env) == 6
    assert count(env["STUB_DEST"], "leads") == 3


def test_the_freeze_is_inside_every_delete_statement() -> None:
    mod = load_script()
    guard = mod.guard_sql({"leads", "kit_events"})
    (sql,) = mod.delete_statements("leads", [{"id": "a" * 32}], guard)
    assert sql.count(guard) == 1 and "sqlite_master" in sql
    assert "leads_frozen" in guard and "kit_events_frozen" in guard
    (events,) = mod.delete_statements("kit_events", [{"id": 1}], guard)
    assert "sqlite_master" in events


@pytest.mark.parametrize(
    "case", ["unfrozen", "missing", "extra", "unfinished", "nomanifest"]
)
def test_purge_of_an_empty_source_still_needs_the_freeze_and_the_proof(
    tmp_path: Path, case: str
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    moved(env, work)
    if case != "unfrozen":
        assert run(env, work, "--freeze").returncode == 0
    one = format(1, "032x")
    if case == "missing":
        dest_exec(env, f"DELETE FROM kit_events WHERE lead_id = '{one}'")
        dest_exec(env, f"DELETE FROM leads WHERE id = '{one}'")
    if case == "extra":
        with closing(sqlite3.connect(env["STUB_DEST"])) as db:
            lead(db, 50)
            db.commit()
    manifest = work / "move-manifest.json"
    if case == "unfinished":
        manifest.write_text(
            manifest.read_text().replace('"state": "copied"', '"state": "copying"')
        )
    if case == "nomanifest":
        manifest.unlink()
    r = run(env, work, "--purge")
    assert r.returncode in (1, 3), r.stdout
    assert "done:" not in r.stdout
    assert "example.test" not in r.stdout + r.stderr


def test_purge_of_an_empty_source_reports_done_only_when_the_proof_passes(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    moved(env, work)
    assert run(env, work, "--freeze").returncode == 0
    r = run(env, work, "--purge")
    assert r.returncode == 0, r.stderr
    assert "cutover verified" in r.stdout and "done:" in r.stdout


def two_runs(tmp_path: Path) -> tuple[dict[str, str], dict[str, str], Path, Path]:
    one, two = tmp_path / "a", tmp_path / "b"
    one.mkdir(mode=0o700)
    two.mkdir(mode=0o700)
    ea, eb = make(one), make(two)
    wa, wb = workdir(one), workdir(two)
    for e, w in ((ea, wa), (eb, wb)):
        moved(e, w)
        assert run(e, w, "--freeze").returncode == 0
        assert run(e, w, "--verify").returncode == 0
    return ea, eb, wa, wb


def test_a_manifest_from_another_run_or_database_is_refused(tmp_path: Path) -> None:
    # Same names, same rows, same schema: only the databases' own ids differ.
    ea, eb, wa, wb = two_runs(tmp_path)
    (wa / "move-manifest.json").unlink()
    (wa / "move-manifest.json").write_text((wb / "move-manifest.json").read_text())
    (wa / "move-manifest.json").chmod(0o600)
    r = run(ea, wa, "--verify")
    assert r.returncode == 3
    assert "platform ids differ" in r.stderr


# ---- round 9: manifest file and directory ownership --------------------------------


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o400, 0o700])
def test_an_existing_manifest_must_be_mode_600(tmp_path: Path, mode: int) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    frozen_and_moved(env, work)
    (work / "move-manifest.json").chmod(mode)
    r = run(env, work, "--verify")
    assert r.returncode == 3
    assert "mode 600" in r.stderr


def test_a_manifest_that_is_a_symlink_or_a_hard_link_is_refused(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    frozen_and_moved(env, work)
    path = work / "move-manifest.json"
    real = tmp_path / "real.json"
    real.write_text(path.read_text())
    real.chmod(0o600)
    path.unlink()
    path.symlink_to(real)
    assert run(env, work, "--verify").returncode == 3
    path.unlink()
    path.hardlink_to(real)
    r = run(env, work, "--verify")
    assert r.returncode == 3
    assert "plain regular file" in r.stderr


def test_a_manifest_owned_by_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    frozen_and_moved(env, work)
    mod = load_script()
    uid = os.getuid()
    monkeypatch.setattr(mod.os, "getuid", lambda: uid + 1)
    m = mod.Manifest(
        work, "analytics-db", "leads-db", str(uuid.uuid4()), str(uuid.uuid4())
    )
    with pytest.raises(mod.Refused, match="not owned by you"):
        m.load()


def test_a_workdir_under_a_group_writable_parent_is_refused(tmp_path: Path) -> None:
    env = make(tmp_path)
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o700)
    work = parent / "work"
    work.mkdir(mode=0o700)
    parent.chmod(0o770)
    r = run(env, work, "--purge")
    assert r.returncode == 3
    assert "writable by group or others" in r.stderr
    assert src_rows(env) == 6


def test_a_workdir_reached_through_a_symlinked_parent_is_refused(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    (real / "work").mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(real)
    r = run(env, link / "work", "--purge")
    assert r.returncode == 3
    assert "symlink" in r.stderr
    assert src_rows(env) == 6


def test_a_workdir_under_a_parent_owned_by_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    uid = os.getuid()
    monkeypatch.setattr(mod.os, "getuid", lambda: uid + 12345)
    with pytest.raises(mod.Refused, match="owned by another user"):
        mod.check_ancestors(str(work))


# ---- round 10: a locked, recoverable cutover -------------------------------------


def manifest_of(work: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((work / "move-manifest.json").read_text())
    return data


def test_purge_refuses_without_the_locked_window_flag_before_any_write(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    r = run(env, work, "--purge", window=False)
    assert r.returncode == 3
    assert "--locked-window-confirmed" in r.stderr
    assert src_rows(env) == 6
    assert count(env["STUB_DEST"], "leads") == 0  # not even the copy ran
    assert [
        p for p in work.iterdir() if p.name != LOCK_FILE
    ] == []  # r13: permanent lock file


def test_the_window_flag_is_for_purge_only(tmp_path: Path) -> None:
    env = make(tmp_path)
    r = run(env, workdir(tmp_path), "--copy", "--locked-window-confirmed")
    assert r.returncode == 2
    assert count(env["STUB_DEST"], "leads") == 0


def test_a_bookmark_is_recorded_before_the_first_delete_and_the_restore_is_printed(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_LOG"] = str(tmp_path / "calls.log")
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode == 0, r.stderr
    calls = (tmp_path / "calls.log").read_text().split()
    assert calls[0] == "bookmark" and calls.count("bookmark") == 1
    assert "delete" in calls[1:]
    assert manifest_of(work)["bookmarks"] == [BOOKMARK]
    assert (
        f"npx wrangler d1 time-travel restore analytics-db --bookmark={BOOKMARK}"
        in r.stdout
    )
    assert "example.test" not in r.stdout + r.stderr


def test_the_bookmark_is_read_from_plain_text_output_too(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_BOOKMARK_TEXT"] = "plain"
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    assert manifest_of(work)["bookmarks"] == [BOOKMARK]


@pytest.mark.parametrize("case", ["fails", "garbage", "two"])
def test_no_usable_bookmark_means_nothing_is_deleted(tmp_path: Path, case: str) -> None:
    env = make(tmp_path)
    if case == "fails":
        env["STUB_BOOKMARK_FAIL"] = "1"
    elif case == "garbage":
        env["STUB_BOOKMARK_TEXT"] = "no bookmark here"
    else:
        env["STUB_BOOKMARK_TEXT"] = BOOKMARK + " " + BOOKMARK.replace("85", "86", 1)
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode == 1
    assert "partial" not in r.stderr  # nothing was deleted: the plain exit
    assert src_rows(env) == 6
    assert manifest_of(work)["bookmarks"] == []


def test_the_purge_itself_refuses_without_a_recorded_bookmark(tmp_path: Path) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    m = mod.Manifest(
        work, "analytics-db", "leads-db", str(uuid.uuid4()), str(uuid.uuid4())
    )

    class Boom:
        def execute(self, *a: Any, **k: Any) -> list[dict[str, Any]]:
            raise AssertionError(
                "a purge without a bookmark must not touch the database"
            )

    with pytest.raises(mod.Refused, match="bookmark"):
        mod.purge_chunks(Boom(), object(), work, m, mod.Progress())


def test_the_settle_wait_is_taken_between_two_reads_and_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path)
    env["MOVE_SETTLE"] = "7"
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)
    waits: list[float] = []
    monkeypatch.setattr(
        mod,
        "time",
        SimpleNamespace(
            sleep=waits.append,
            strftime=time.strftime,
            gmtime=time.gmtime,
        ),
    )
    assert mod.main(main_args(work, "--purge")) == 0
    assert waits == [7]
    for bad in ("61", "x", "-1", ""):
        env["MOVE_SETTLE"] = bad
        r = run(env, tmp_path / "work", "--purge")
        assert r.returncode != 0 and "MOVE_SETTLE" in r.stderr, bad


@pytest.mark.parametrize("which", ["src", "dest"])
def test_a_database_that_changes_during_the_settle_wait_stops_the_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    env = make(tmp_path)
    env["STUB_LOG"] = str(tmp_path / "calls.log")
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)
    one = format(1, "032x")

    def writer(_: float) -> None:
        # The freeze blocks inserts, not an UPDATE by an older Worker.
        target = env["STUB_SRC"] if which == "src" else env["STUB_DEST"]
        with closing(sqlite3.connect(target)) as db:
            db.execute(f"UPDATE leads SET last_seen_ts = 31337 WHERE id = '{one}'")
            db.commit()

    # Only the script's own sleep: subprocess polls with time.sleep too.
    monkeypatch.setattr(mod, "time", SimpleNamespace(sleep=writer))
    assert mod.main(main_args(work, "--purge")) == 3  # refused, nothing deleted
    assert src_rows(env) == 6
    assert not (tmp_path / "calls.log").exists()  # no bookmark, no delete either


def test_a_source_row_changed_after_the_manifest_is_moved_by_the_rerun(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_INJECT"] = "edit"
    work = workdir(tmp_path)
    first = purge(env, work)
    assert first.returncode == 4
    two = format(2, "032x")
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        assert db.execute("SELECT last_seen_ts FROM leads").fetchall() == [(99999,)]
    del env["STUB_INJECT"]
    again = purge(env, work)  # the documented rerun
    assert again.returncode == 0, again.stderr
    assert "re-copied 1 rows that changed" in again.stdout
    assert src_rows(env) == 0
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        got = db.execute(
            "SELECT last_seen_ts FROM leads WHERE id = ?", (two,)
        ).fetchone()
    assert got == (99999,)  # the destination row was updated, not skipped
    assert count(env["STUB_DEST"], "leads") == 3
    assert count(env["STUB_DEST"], "kit_events") == 3
    assert "example.test" not in again.stdout + again.stderr


def test_a_copy_rerun_updates_a_changed_row_and_its_digest(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--copy").returncode == 0
    one = format(1, "032x")
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute(f"UPDATE leads SET last_seen_ts = 5555 WHERE id = '{one}'")
        db.commit()
    assert run(env, work, "--copy").returncode == 0
    assert rows(env["STUB_SRC"], "leads") == rows(env["STUB_DEST"], "leads")
    assert run(env, work, "--freeze").returncode == 0
    moved_rows = run(env, work, "--purge")
    assert moved_rows.returncode == 0, moved_rows.stderr


def test_a_destination_row_that_is_not_the_recorded_one_is_never_overwritten(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--copy").returncode == 0
    one = format(1, "032x")
    dest_exec(env, f"UPDATE leads SET last_seen_ts = 777 WHERE id = '{one}'")
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute(f"UPDATE leads SET last_seen_ts = 5555 WHERE id = '{one}'")
        db.commit()
    r = run(env, work, "--copy")
    assert r.returncode == 1
    assert "recorded version" in r.stderr
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        got = db.execute("SELECT last_seen_ts FROM leads WHERE id = ?", (one,))
        assert got.fetchone() == (777,)  # left for a hand decision


# ---- round 10: identity is read from the platform, never from the data --------------


def test_no_identity_table_is_left_in_either_database(tmp_path: Path) -> None:
    env = make(tmp_path)
    assert purge(env, workdir(tmp_path)).returncode == 0
    for path in (env["STUB_SRC"], env["STUB_DEST"]):
        with closing(sqlite3.connect(path)) as db:
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        assert "move_identity" not in names


def test_a_cloned_database_cannot_borrow_the_recorded_identity(
    tmp_path: Path,
) -> None:
    # A clone carries every row and table of the original, but the platform gives it
    # an id of its own: the manifest of the original run does not apply to it.
    env = make(tmp_path)
    work = workdir(tmp_path)
    frozen_and_moved(env, work)
    clone = tmp_path / "clone.sqlite"
    shutil.copy(env["STUB_DEST"], clone)
    cloned = dict(env, STUB_DEST=str(clone), STUB_DEST_ID=str(uuid.uuid4()))
    r = run(cloned, work, "--verify")
    assert r.returncode == 3
    assert "platform ids differ" in r.stderr
    same = run(env, work, "--verify")  # the original still verifies
    assert same.returncode == 0, same.stderr


@pytest.mark.parametrize("side", ["source", "dest"])
def test_an_id_that_is_not_the_platforms_answer_is_refused(
    tmp_path: Path, side: str
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    wrong = str(uuid.uuid4())
    r = run(
        env,
        work,
        "--copy",
        source_id=wrong if side == "source" else None,
        dest_id=wrong if side == "dest" else None,
    )
    assert r.returncode == 3
    assert "not the database you named" in r.stderr
    assert count(env["STUB_DEST"], "leads") == 0
    assert [
        p for p in work.iterdir() if p.name != LOCK_FILE
    ] == []  # r13: permanent lock file


def test_two_names_with_one_platform_id_are_refused(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_DEST_ID"] = env["STUB_SRC_ID"]
    r = run(env, workdir(tmp_path), "--copy")
    assert r.returncode == 3
    assert "same platform id" in r.stderr


def test_an_unreadable_platform_answer_and_a_bad_id_argument_fail_closed(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    bad = dict(env, STUB_INFO_BAD="1")
    assert run(bad, work, "--copy").returncode == 1
    assert run(env, work, "--copy", source_id="not-a-uuid").returncode == 2
    assert count(env["STUB_DEST"], "leads") == 0


# ---- round 10: the backup dump check passes before and after the move -------------


def dump_check(env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    # The backup never exports the lead tables, so their rows are left out; the schema
    # (and so every trigger the tool added) is checked as it stands.
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        keep = [
            ln
            for ln in db.iterdump()
            if not ln.startswith(('INSERT INTO "leads"', 'INSERT INTO "kit_events"'))
        ]
    f = tmp_path / "analytics-dump.sql"
    f.write_text("\n".join(keep) + "\n")
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(ROOT / "scripts" / "check-dump.py"), str(f)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_backup_dump_check_passes_before_and_after_the_move(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    before = dump_check(env, tmp_path)
    assert before.returncode == 0, before.stdout
    assert run(env, work, "--freeze").returncode == 0
    frozen = dump_check(env, tmp_path)
    assert frozen.returncode == 0, frozen.stdout
    assert "leads_frozen" in "".join(
        r[0] or ""
        for r in sqlite3.connect(env["STUB_SRC"]).execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger'"
        )
    )
    assert run(env, work, "--purge").returncode == 0
    after = dump_check(env, tmp_path)
    assert after.returncode == 0, after.stdout


# ---- round 10: distinct exit codes for a purge that stops part way ------------------


def test_a_delete_whose_answer_is_lost_requires_verification_not_a_retry(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_DELETE_THEN_FAIL"] = "1"
    r = purge(env, workdir(tmp_path))
    assert r.returncode == 5
    assert "failure after a delete" in r.stderr and "--verify" in r.stderr
    assert src_rows(env) < 6  # the delete was applied although the call failed
    assert "example.test" not in r.stdout + r.stderr


def test_each_failure_has_its_own_exit_code_and_message(tmp_path: Path) -> None:
    # 1: nothing deleted. 4: some chunks deleted, safe to resume. 5: verify required.
    env = make(tmp_path)
    env["STUB_BOOKMARK_FAIL"] = "1"
    nothing = purge(env, workdir(tmp_path))
    assert nothing.returncode == 1 and "partial" not in nothing.stderr
    assert src_rows(env) == 6


# ---- round 10: the scratch file is checked again right before wrangler runs it -------


class SwapSpy:
    """Runs `act` once, right after the statement file was written."""

    def __init__(self, mod: Any, act: Any) -> None:
        self.real = mod.os.fsync
        self.act = act
        self.done = False

    def __call__(self, fd: int) -> None:
        self.real(fd)
        if not self.done:
            self.done = True
            self.act()


def test_a_statement_file_swapped_before_wrangler_runs_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    seen: list[Path] = []
    real_mkdtemp = mod.tempfile.mkdtemp

    def mk(**k: Any) -> str:
        made = real_mkdtemp(**k)
        seen.append(Path(made))
        return str(made)

    monkeypatch.setattr(mod.tempfile, "mkdtemp", mk)

    def swap() -> None:
        target = seen[0] / "statements.sql"
        target.unlink()
        target.write_text("DROP TABLE leads;\n")  # another file at the same name

    monkeypatch.setattr(mod.os, "fsync", SwapSpy(mod, swap))
    w = RecordingWrangler()
    with pytest.raises(mod.Refused, match="changed before it ran"):
        mod.run_file(w, "db", work, "SELECT 1;\n")
    assert w.seen == []  # nothing was executed
    assert list(work.iterdir()) == []  # and the scratch directory is gone


def test_a_scratch_directory_swapped_before_wrangler_runs_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    seen: list[Path] = []
    real_mkdtemp = mod.tempfile.mkdtemp

    def mk(**k: Any) -> str:
        made = real_mkdtemp(**k)
        seen.append(Path(made))
        return str(made)

    monkeypatch.setattr(mod.tempfile, "mkdtemp", mk)

    def swap() -> None:
        d = seen[0]
        d.rename(tmp_path / "moved-away")  # the directory the file lives in
        d.mkdir(mode=0o700)
        (d / "statements.sql").write_text("DROP TABLE leads;\n")
        (d / "statements.sql").chmod(0o600)

    monkeypatch.setattr(mod.os, "fsync", SwapSpy(mod, swap))
    w = RecordingWrangler()
    with pytest.raises(mod.Refused, match="changed before it ran"):
        mod.run_file(w, "db", work, "SELECT 1;\n")
    assert w.seen == []


def test_a_scratch_directory_that_is_not_private_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    open_dir = work / "open-scratch"
    open_dir.mkdir()
    open_dir.chmod(0o755)
    monkeypatch.setattr(mod.tempfile, "mkdtemp", lambda **_: str(open_dir))
    w = RecordingWrangler()
    with pytest.raises(mod.Refused, match="not a private directory"):
        mod.run_file(w, "db", work, "SELECT 1;\n")
    assert w.seen == []


# ---- recovery export and per-attempt bookmarks ------------------------------------


def recovery_files(work: Path) -> list[Path]:
    return sorted(work.glob("lead-recovery-*.json"))


def test_export_is_private_bound_and_written_before_first_delete(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    log = tmp_path / "calls.log"
    env["STUB_LOG"] = str(log)
    result = purge(env, work)
    assert result.returncode == 0, result.stderr
    files = recovery_files(work)
    assert len(files) == 1
    export = files[0]
    assert export.parent == work
    assert not export.is_relative_to(ROOT)
    assert stat.S_IMODE(export.stat().st_mode) == 0o600
    manifest = json.loads((work / "move-manifest.json").read_text())
    data = json.loads(export.read_text())
    assert data["run_id"] == manifest["run_id"]
    assert data["src_id"] == manifest["src_id"]
    assert data["dst_id"] == manifest["dst_id"]
    assert manifest["recovery_exports"] == [export.name]
    assert log.read_text().splitlines()[-1] == "delete"
    assert export.stat().st_mtime_ns <= log.stat().st_mtime_ns


def test_purge_refuses_before_delete_when_export_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    log = tmp_path / "calls.log"
    env["STUB_LOG"] = str(log)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)
    monkeypatch.setattr(
        mod,
        "write_recovery_export",
        lambda *_: (_ for _ in ()).throw(mod.Failed("a database call failed")),
    )
    assert mod.main(main_args(work, "--purge")) == 3
    assert "delete" not in log.read_text().splitlines()
    assert src_rows(env) == 6


def test_restore_from_export_restores_rows_and_is_idempotent(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    first = purge(env, work)
    assert first.returncode == 0, first.stderr
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DROP TRIGGER IF EXISTS leads_frozen")
        db.execute("DROP TRIGGER IF EXISTS kit_events_frozen")
        db.commit()
    restored = run(env, work, "--restore-from-export")
    assert restored.returncode == 0, restored.stderr
    assert src_rows(env) == 6
    again = run(env, work, "--restore-from-export")
    assert again.returncode == 0, again.stderr
    assert "0 missing rows restored" in again.stdout
    assert src_rows(env) == 6


def test_restore_refuses_a_conflicting_source_row(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    export = json.loads(recovery_files(work)[0].read_text())
    lead_row = next(item["row"] for item in export["rows"] if item["table"] == "leads")
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DROP TRIGGER IF EXISTS leads_frozen")
        db.execute("DROP TRIGGER IF EXISTS kit_events_frozen")
        columns = tuple(lead_row)
        changed = dict(lead_row)
        changed["email"] = "conflict@example.test"
        db.execute(
            "INSERT INTO leads "
            f"({', '.join(columns)}) "
            f"VALUES ({', '.join('?' for _ in columns)})",
            tuple(changed[column] for column in columns),
        )
        db.commit()
    result = run(env, work, "--restore-from-export")
    assert result.returncode == 3
    assert "primary key but different data" in result.stderr


def test_current_attempt_bookmark_is_printed_not_the_oldest(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    assert run(env, work, "--copy").returncode == 0
    manifest_path = work / "move-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    old = "00000001-00000002-00000003-" + "4" * 32
    manifest["bookmarks"] = [old]
    manifest["bookmark_attempts"] = [
        {"bookmark": old, "timestamp": "2026-01-01T00:00:00Z"}
    ]
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)
    result = run(env, work, "--purge")
    assert result.returncode == 0, result.stderr
    restore_lines = [
        line for line in result.stdout.splitlines() if "--bookmark=" in line
    ]
    assert restore_lines == [
        f"  npx wrangler d1 time-travel restore analytics-db --bookmark={BOOKMARK}"
    ]
    assert f"earlier recovery bookmark: {old}" in result.stdout


def test_export_contents_never_appear_in_output(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    result = purge(env, work)
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "person1@example.test" not in output
    assert format(1, "032x") not in output


def test_verify_reports_moved_rows_absent_from_source_and_recovery_exports(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    result = purge(env, work)
    assert result.returncode == 0, result.stderr
    manifest_path = work / "move-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["recovery_exports"] = []
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)
    checked = run(env, work, "--verify")
    assert checked.returncode == 1
    assert "removed at source or unknown: 6" in checked.stdout
    assert "operator must decide" in checked.stderr
    assert "person1@example.test" not in checked.stdout + checked.stderr
    assert format(1, "032x") not in checked.stdout + checked.stderr


def test_verify_accepts_rows_accounted_for_by_the_recovery_export(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    result = purge(env, work)
    assert result.returncode == 0, result.stderr
    checked = run(env, work, "--verify")
    assert checked.returncode == 0, checked.stderr
    assert "removed at source or unknown: 0" in checked.stdout
    assert count(env["STUB_SRC"], "leads") == 0
    assert count(env["STUB_DEST"], "leads") == 3


def test_resume_refuses_a_tampered_destination_prefix(tmp_path: Path) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    env["STUB_COUNTER"] = str(tmp_path / "calls")
    env["CRASH_AT"] = "2"
    work = workdir(tmp_path)
    first = run(env, work, "--copy")
    assert first.returncode == 1
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        db.execute(
            "UPDATE leads SET email = ? WHERE id = ?",
            ("tampered-prefix@example.test", format(1, "032x")),
        )
        db.commit()
    del env["CRASH_AT"]
    resumed = run(env, work, "--copy")
    assert resumed.returncode == 3
    assert "destination prefix at or before the saved cursor" in resumed.stderr
    assert "--restart-copy" in resumed.stderr
    assert count(env["STUB_DEST"], "leads") == 2


def test_resume_with_an_intact_destination_prefix_continues(
    tmp_path: Path,
) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    env["STUB_COUNTER"] = str(tmp_path / "calls")
    env["CRASH_AT"] = "2"
    work = workdir(tmp_path)
    first = run(env, work, "--copy")
    assert first.returncode == 1
    del env["CRASH_AT"]
    resumed = run(env, work, "--copy")
    assert resumed.returncode == 0, resumed.stderr
    assert "resuming an unfinished copy from the recorded cursor" in resumed.stdout
    assert rows(env["STUB_SRC"], "leads") == rows(env["STUB_DEST"], "leads")
    assert rows(env["STUB_SRC"], "kit_events") == rows(env["STUB_DEST"], "kit_events")


def test_an_export_made_before_a_row_was_re_moved_is_stale_not_foreign(
    tmp_path: Path,
) -> None:
    mod = load_script()
    uid = "a" * 8 + "-aaaa-aaaa-aaaa-" + "a" * 12
    m = mod.Manifest(tmp_path, "src", "dst", uid, uid)
    old_row = {c: None for c in mod.COLUMNS["leads"]} | {"id": "1" * 32}
    new_row = old_row | {"last_seen_ts": 99999}
    m.record("leads", old_row)
    # No re-move yet: an export holding a changed row is foreign and is refused.
    stale = {"table": "leads", "row": old_row, "digest": mod.digest("leads", old_row)}
    data = {"version": 1, "run_id": m.run_id, "src_id": uid, "dst_id": uid}
    m.record("leads", new_row)  # the rerun re-moves the changed row
    assert mod.digest("leads", old_row) in m.superseded["leads"]["1" * 32]
    assert len(mod.validate_export(data | {"rows": [stale]}, m)) == 1
    forged_row = old_row | {"last_seen_ts": 7}
    forged = {
        "table": "leads",
        "row": forged_row,
        "digest": mod.digest("leads", forged_row),
    }
    with pytest.raises(mod.Refused):
        mod.validate_export(data | {"rows": [forged]}, m)
    lied = stale | {"row": forged_row}  # row text altered, digest kept
    with pytest.raises(mod.Refused):
        mod.validate_export(data | {"rows": [lied]}, m)


# ---- interruption and atomic state writes (round 10, finding 4) -------------------

HOLD = """#!{python}
import os, sys
args = sys.argv[1:]
text = " ".join(args)
for a in args:
    if a.startswith("--file="):
        text += open(a.split("=", 1)[1]).read()
if os.environ["HOLD_MATCH"] in text:
    counter = os.environ["HOLD_COUNTER"]
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    if n + 1 == int(os.environ["HOLD_NTH"]):
        open(os.environ["HOLD_MARK"], "w").write("held")
        for _ in range(300):
            if os.path.exists(os.environ["HOLD_GO"]):
                break
            import time
            time.sleep(0.1)
os.execv(os.environ["STUB_REAL"], [os.environ["STUB_REAL"], *args])
"""


def held_env(tmp_path: Path, env: dict[str, str], match: str, nth: int) -> Path:
    """Route wrangler through a wrapper that holds one named call until released."""
    wrapper = tmp_path / "hold-wrapper"
    wrapper.write_text(HOLD.format(python=sys.executable))
    wrapper.chmod(0o700)
    env["STUB_REAL"] = env["WRANGLER"]
    env["WRANGLER"] = str(wrapper)
    env["HOLD_MATCH"] = match
    env["HOLD_NTH"] = str(nth)
    env["HOLD_COUNTER"] = str(tmp_path / "hold-count")
    env["HOLD_MARK"] = str(tmp_path / "hold-mark")
    env["HOLD_GO"] = str(tmp_path / "hold-go")
    return Path(env["HOLD_GO"])


def start(env: dict[str, str], work: Path, *flags: str) -> subprocess.Popen[str]:
    known = ids_of(env)
    extra = ["--locked-window-confirmed"] if "--purge" in flags else []
    return subprocess.Popen(  # noqa: S603 - fixed argv, synthetic input
        [
            sys.executable,
            "-I",
            str(SCRIPT),
            "--source",
            "analytics-db",
            "--source-id",
            known["analytics-db"],
            "--dest",
            "leads-db",
            "--dest-id",
            known["leads-db"],
            "--workdir",
            str(work),
            *flags,
            *extra,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def signal_when_held(
    proc: subprocess.Popen[str], env: dict[str, str], signum: int, *, kill: bool = False
) -> tuple[int, str, str]:
    """Wait until the held call is reached, signal the tool, release, collect."""
    mark = Path(env["HOLD_MARK"])
    for _ in range(300):
        if mark.exists():
            break
        time.sleep(0.1)
    assert mark.exists(), "the held call was never reached"
    proc.send_signal(signum)
    time.sleep(0.3)  # the handler has run; the held call is still in flight
    Path(env["HOLD_GO"]).write_text("go")
    out, err = proc.communicate(timeout=60)
    return proc.returncode, out, err


def saved_manifest(work: Path) -> dict[str, Any]:
    path = work / "move-manifest.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def leftovers(work: Path) -> list[str]:
    return sorted(
        p.name
        for p in work.iterdir()
        if p.name.startswith("move-")
        and p.name != "move-manifest.json"
        or p.name.endswith(".tmp")
    )


@pytest.mark.parametrize("signum", [2, 15, 1])  # SIGINT, SIGTERM, SIGHUP
def test_a_signal_during_copy_stops_at_a_safe_point_and_a_rerun_resumes(
    tmp_path: Path, signum: int
) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    held_env(tmp_path, env, "INSERT", 2)  # the second leads chunk
    work = workdir(tmp_path)
    code, out, err = signal_when_held(start(env, work, "--copy"), env, signum)
    assert code == 6, (code, err)
    assert "interrupted" in err and "Traceback" not in err
    data = saved_manifest(work)  # whole, mode 600
    assert data["state"] == "copying" and data["cursor"]["leads"]
    assert leftovers(work) == []  # no scratch directory, no temporary file
    assert count(env["STUB_SRC"], "leads") == 5  # nothing deleted
    for key in ("HOLD_MATCH",):
        env[key] = "no-such-text"
    resumed = run(env, work, "--copy")
    assert resumed.returncode == 0, resumed.stderr
    assert "resuming an unfinished copy" in resumed.stdout
    assert rows(env["STUB_SRC"], "leads") == rows(env["STUB_DEST"], "leads")
    assert rows(env["STUB_SRC"], "kit_events") == rows(env["STUB_DEST"], "kit_events")


@pytest.mark.parametrize("signum", [2, 15, 1])
def test_a_signal_after_a_delete_exits_4_and_a_rerun_finishes(
    tmp_path: Path, signum: int
) -> None:
    env = make(tmp_path, count=3)
    env["MOVE_BATCH"] = "1"
    assert run(env, workdir(tmp_path), "--freeze").returncode == 0
    held_env(tmp_path, env, "DELETE FROM", 1)  # the first source delete
    work = tmp_path / "work"
    code, out, err = signal_when_held(start(env, work, "--purge"), env, signum)
    assert code == 4, (code, err)
    assert "partial purge" in err and "interrupted" in err
    assert "Traceback" not in err
    assert 0 < src_rows(env) < 6  # the in-flight delete finished, the rest did not
    assert saved_manifest(work)["purge_open"] is True
    env["HOLD_MATCH"] = "no-such-text"
    again = run(env, work, "--purge")
    assert again.returncode == 0, again.stderr
    assert src_rows(env) == 0
    assert saved_manifest(work)["purge_open"] is False  # the final verify cleared it


def test_a_signal_before_the_first_delete_exits_6_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    held_env(tmp_path, env, "time-travel", 1)  # after the settle reads
    code, out, err = signal_when_held(start(env, work, "--purge"), env, 15)
    assert code == 6, (code, err)
    assert "deleted nothing" in err
    assert src_rows(env) == 6
    assert saved_manifest(work)["purge_open"] is False  # no delete was ever possible
    env["HOLD_MATCH"] = "no-such-text"
    assert run(env, work, "--purge").returncode == 0


def test_a_killed_purge_leaves_a_durable_verify_first_marker(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["MOVE_BATCH"] = "1"
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    held_env(tmp_path, env, "DELETE FROM", 1)
    proc = start(env, work, "--purge")
    code, _out, _err = signal_when_held(proc, env, 9)  # SIGKILL: no handler can run
    assert code == -9
    assert saved_manifest(work)["purge_open"] is True
    time.sleep(1)  # the orphaned statement finishes on its own
    env["HOLD_MATCH"] = "no-such-text"
    # Round 11 (finding 4): the marker is a gate, so the direct rerun is refused until
    # a --verify attempt is recorded; then the rerun finishes the move.
    gated = run(env, work, "--purge")
    assert gated.returncode == 3, gated.stderr
    assert "run --verify first" in gated.stderr
    assert saved_manifest(work)["purge_open"] is True
    assert run(env, work, "--verify").returncode == 1  # rows remain; attempt recorded
    # The killed delete is not in the deleted set, so its row is unaccounted until the
    # recovery export puts it back (docs/deploy.md); then the rerun finishes the move.
    restore_unfrozen(env, work)
    assert run(env, work, "--purge").returncode == 0
    assert saved_manifest(work)["purge_open"] is False


def test_the_manifest_write_is_atomic_durable_and_mode_set_before_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    uid = str(uuid.uuid4())
    m = mod.Manifest(work, "src", "dst", uid, uid)
    m.save()
    before = (work / "move-manifest.json").read_text()
    calls: list[str] = []
    real_rename, real_fsync = mod.os.rename, mod.os.fsync

    def rename(*a: Any, **k: Any) -> None:
        calls.append("rename")
        real_rename(*a, **k)

    def fsync(fd: int) -> None:
        calls.append("dir-fsync" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync")
        real_fsync(fd)

    monkeypatch.setattr(mod.os, "rename", rename)
    monkeypatch.setattr(mod.os, "fsync", fsync)
    old = os.umask(0o277)  # would leave the file at mode 400 without an explicit chmod
    try:
        m.cursor = {"leads": "x"}
        m.save()
    finally:
        os.umask(old)
    assert calls == ["fsync", "rename", "dir-fsync"]  # file, rename, then the directory
    assert stat.S_IMODE((work / "move-manifest.json").stat().st_mode) == 0o600
    assert (work / "move-manifest.json").read_text() != before
    # A failure at the rename leaves the old manifest whole and no temporary file.
    monkeypatch.setattr(
        mod.os, "rename", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    m.cursor = {"leads": "y"}
    with pytest.raises(OSError):
        m.save()
    assert json.loads((work / "move-manifest.json").read_text())["cursor"] == {
        "leads": "x"
    }
    assert [p.name for p in work.iterdir()] == ["move-manifest.json"]


def test_a_killed_runs_temporary_files_are_removed_by_the_next_write(
    tmp_path: Path,
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    uid = str(uuid.uuid4())
    for name in (".move-manifest.json.aaaa.tmp", ".lead-recovery-x.json.bbbb.tmp"):
        (work / name).write_text("half")
        (work / name).chmod(0o600)
    mod.Manifest(work, "src", "dst", uid, uid).save()
    names = sorted(p.name for p in work.iterdir())
    assert names == [".lead-recovery-x.json.bbbb.tmp", "move-manifest.json"]
    dfd = os.open(work, os.O_RDONLY | os.O_DIRECTORY)
    try:
        mod.sweep_stale(dfd, mod.EXPORT_PREFIX)
    finally:
        os.close(dfd)
    assert [p.name for p in work.iterdir()] == ["move-manifest.json"]


def test_a_recovery_export_never_exists_half_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    uid = str(uuid.uuid4())
    m = mod.Manifest(work, "src", "dst", uid, uid)
    monkeypatch.setattr(mod, "recovery_rows", lambda *a: [])
    args = SimpleNamespace(source="src")
    monkeypatch.setattr(
        mod.os, "rename", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        mod.write_recovery_export(None, args, work, m)
    assert list(work.iterdir()) == []  # not under the final name, no temporary file
    assert m.recovery_exports == []


# ---- round 11: one run at a time, deleted set, restore order, purge gate ------------

LOCK_FILE = ".move-run.lock"


def ns(env: dict[str, str], work: Path) -> SimpleNamespace:
    return SimpleNamespace(
        source="analytics-db",
        dest="leads-db",
        source_id=env["STUB_SRC_ID"],
        dest_id=env["STUB_DEST_ID"],
        workdir=str(work),
    )


def wait_held(env: dict[str, str]) -> None:
    for _ in range(300):
        if Path(env["HOLD_MARK"]).exists():
            return
        time.sleep(0.1)
    raise AssertionError("the held call was never reached")


def refused_while_held(env: dict[str, str], work: Path) -> None:
    """A second run in any mode is refused at once (exit 7) and changes nothing."""
    before = (work / "move-manifest.json").read_bytes()
    for flag in ("--copy", "--purge", "--verify"):
        r = run(env, work, flag)
        assert r.returncode == 7, (flag, r.returncode, r.stderr)
        assert "another run holds the lock" in r.stderr
        assert "Traceback" not in r.stderr
        assert r.stdout == ""  # it did nothing, not even read a count
    assert (work / "move-manifest.json").read_bytes() == before


def test_a_second_run_cannot_load_the_manifest_while_the_first_holds_the_lock(
    tmp_path: Path,
) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    held_env(tmp_path, env, "INSERT", 2)
    proc = start(env, work, "--copy")
    wait_held(env)
    lock = work / LOCK_FILE
    assert lock.exists() and stat.S_IMODE(lock.stat().st_mode) == 0o600
    refused_while_held(env, work)
    Path(env["HOLD_GO"]).write_text("go")
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, err
    assert lock.exists()  # round 13: the lock file is permanent, never removed
    env["HOLD_MATCH"] = "no-such-text"
    assert run(env, work, "--copy").returncode == 0  # free again


@pytest.mark.parametrize("signum", [15, 2, 1])
def test_the_lock_is_released_after_a_signal_that_ends_the_run(
    tmp_path: Path, signum: int
) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    held_env(tmp_path, env, "INSERT", 2)
    proc = start(env, work, "--copy")
    wait_held(env)
    refused_while_held(env, work)
    proc.send_signal(signum)
    time.sleep(0.3)
    Path(env["HOLD_GO"]).write_text("go")
    _out, err = proc.communicate(timeout=60)
    assert proc.returncode == 6, err
    assert (work / LOCK_FILE).exists()  # r13: permanent; the lock itself is free
    env["HOLD_MATCH"] = "no-such-text"
    again = run(env, work, "--copy")
    assert again.returncode == 0, again.stderr


def test_the_lock_is_released_after_sigkill_and_the_leftover_file_is_reused(
    tmp_path: Path,
) -> None:
    env = make(tmp_path, count=5)
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    held_env(tmp_path, env, "INSERT", 2)
    proc = start(env, work, "--copy")
    wait_held(env)
    refused_while_held(env, work)
    proc.send_signal(9)  # no handler can run
    time.sleep(0.3)
    Path(env["HOLD_GO"]).write_text("go")
    proc.communicate(timeout=60)
    assert proc.returncode == -9
    assert (work / LOCK_FILE).exists()  # a killed run leaves the file
    env["HOLD_MATCH"] = "no-such-text"
    # Round 12: the orphaned call holds the lock until it ends, so poll for it to
    # finish instead of sleeping; the lock then frees and the file is reused.
    again = run(env, work, "--copy")
    for _ in range(100):
        if again.returncode != 7:
            break
        time.sleep(0.1)
        again = run(env, work, "--copy")
    assert again.returncode == 0, again.stderr
    assert (work / LOCK_FILE).exists()  # r13: permanent file, reused


@pytest.mark.parametrize("case", ["mode", "symlink", "hardlink", "directory"])
def test_a_bad_lock_file_is_refused_before_the_manifest_is_touched(
    tmp_path: Path, case: str
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    victim.chmod(0o644)
    lock = work / LOCK_FILE
    if case == "mode":
        lock.write_text("")
        lock.chmod(0o644)
    elif case == "symlink":
        lock.symlink_to(victim)
    elif case == "hardlink":
        lock.write_text("")
        lock.chmod(0o600)
        os.link(lock, tmp_path / "other-name")
    else:
        lock.mkdir()
    r = run(env, work, "--copy")
    assert r.returncode == 3, r.stderr
    assert "run lock file" in r.stderr
    assert "Traceback" not in r.stderr
    assert not (work / "move-manifest.json").exists()
    assert victim.read_text() == "keep me"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644


def test_the_manifest_save_refuses_when_the_file_changed_under_the_lock(
    tmp_path: Path,
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    uid = str(uuid.uuid4())
    lock = mod.RunLock(work)
    lock.acquire()
    mod.HELD["lock"] = lock
    try:
        m = mod.Manifest(work, "src", "dst", uid, uid)
        m.save()
        other = mod.Manifest(work, "src", "dst", uid, uid)
        other.load()
        other.cursor = {"leads": "x"}
        other.save()  # a writer that got in between: a lost update if allowed
        m.cursor = {"leads": "y"}
        with pytest.raises(mod.Refused):
            m.save()
        assert json.loads((work / "move-manifest.json").read_text())["cursor"] == {
            "leads": "x"
        }
        # And a lock file that was removed or replaced is not ours any more.
        os.unlink(work / LOCK_FILE)
        with pytest.raises(mod.Refused):
            lock.check()
    finally:
        mod.HELD["lock"] = None
        lock.release()


def test_a_source_row_removed_by_another_process_after_the_export_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    assert run(env, work, "--copy").returncode == 0
    mod = load_with_env(env, monkeypatch)
    w, a = mod.Wrangler(), ns(env, work)
    m = mod.new_manifest(work, a)
    m.load()
    m.bookmarks.append(BOOKMARK)
    m.save()
    mod.write_recovery_export(w, a, work, m)  # the export holds every row, event 2 too
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DELETE FROM kit_events WHERE id = 2")  # another process
        db.commit()
    mod.purge_chunks(w, a, work, m, mod.Progress())
    assert src_rows(env) == 0
    capsys.readouterr()
    with pytest.raises(mod.Failed, match="removed at source or unknown"):
        mod.verify(w, a, work)
    assert "removed at source or unknown: 1" in capsys.readouterr().out
    gone = saved_manifest(work)["deleted"]
    assert sum(len(v) for v in gone.values()) == 5  # the rows this tool deleted
    assert "person1@example.test" not in json.dumps(gone)


def test_rows_the_tool_deleted_itself_pass_the_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    data = saved_manifest(work)
    assert sum(len(v) for v in data["deleted"].values()) == 6
    assert all(len(e) == 3 for v in data["deleted"].values() for e in v)
    checked = run(env, work, "--verify")
    assert checked.returncode == 0, checked.stderr
    assert "removed at source or unknown: 0" in checked.stdout


def test_a_deleted_row_whose_export_is_gone_or_whose_outcome_was_lost_is_unaccounted(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_DELETE_THEN_FAIL"] = "1"  # applied, but the caller never hears it
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 5
    assert saved_manifest(work)["deleted"] == {}  # an unknown outcome is not recorded


def test_restore_puts_every_lead_back_before_any_event_across_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    assert run(env, work, "--copy").returncode == 0
    mod = load_with_env(env, monkeypatch)
    w, a = mod.Wrangler(), ns(env, work)
    first = mod.new_manifest(work, a)
    first.load()
    first.bookmarks.append(BOOKMARK)
    first.save()
    mod.write_recovery_export(w, a, work, first)  # export 1: lead 1, its event, ...
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute(
            "UPDATE leads SET last_seen_ts = 99999 WHERE id = ?", (format(1, "032x"),)
        )
        db.commit()
    assert run(env, work, "--copy").returncode == 0  # lead 1 is re-moved: new digest
    second = mod.new_manifest(work, a)
    second.load()
    mod.write_recovery_export(w, a, work, second)  # export 2: new lead 1, same events
    assert len(second.recovery_exports) == 2
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DELETE FROM kit_events")
        db.execute("DELETE FROM leads")
        db.execute("DROP TRIGGER leads_frozen")
        db.execute("DROP TRIGGER kit_events_frozen")
        db.commit()
    r = run(env, work, "--restore-from-export")
    assert r.returncode == 0, r.stderr
    assert "6 missing rows restored" in r.stdout
    assert rows(env["STUB_SRC"], "leads") == rows(env["STUB_DEST"], "leads")
    assert rows(env["STUB_SRC"], "kit_events") == rows(env["STUB_DEST"], "kit_events")
    again = run(env, work, "--restore-from-export")
    assert again.returncode == 0 and "0 missing rows restored" in again.stdout


def restore_unfrozen(env: dict[str, str], work: Path) -> None:
    """The documented recovery: lift the freeze, restore, freeze again."""
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        db.execute("DROP TRIGGER IF EXISTS leads_frozen")
        db.execute("DROP TRIGGER IF EXISTS kit_events_frozen")
        db.commit()
    assert run(env, work, "--restore-from-export").returncode == 0
    assert run(env, work, "--freeze").returncode == 0


def test_a_purge_that_was_killed_is_gated_until_a_verify_attempt_is_recorded(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["MOVE_BATCH"] = "1"
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    held_env(tmp_path, env, "DELETE FROM", 1)
    code, _out, _err = signal_when_held(start(env, work, "--purge"), env, 9)
    assert code == -9
    time.sleep(1)  # the orphaned statement finishes on its own
    env["HOLD_MATCH"] = "no-such-text"
    left = src_rows(env)
    data = saved_manifest(work)
    assert data["purge_open"] is True and data["purge_checked"] is False
    for _ in range(2):  # refused every time, before any copy or delete
        gated = run(env, work, "--purge")
        assert gated.returncode == 3, gated.stderr
        assert "run --verify first" in gated.stderr
        assert src_rows(env) == left
    assert run(env, work, "--copy").returncode == 0  # not destructive: not gated
    failed = run(env, work, "--verify")  # rows remain: it fails, and it is recorded
    assert failed.returncode == 1
    data = saved_manifest(work)
    assert data["purge_open"] is True and data["purge_checked"] is True
    # The killed delete is unaccounted: restore it from the export before purging again.
    restore_unfrozen(env, work)
    # A new purge start clears the record: killed again, it is gated again.
    for name in ("HOLD_MARK", "HOLD_GO"):
        Path(env[name]).unlink(missing_ok=True)
    env["HOLD_MATCH"], env["HOLD_NTH"] = "DELETE FROM", "2"
    code, _out, _err = signal_when_held(start(env, work, "--purge"), env, 9)
    assert code == -9
    time.sleep(1)
    env["HOLD_MATCH"] = "no-such-text"
    data = saved_manifest(work)
    assert data["purge_open"] is True and data["purge_checked"] is False
    assert run(env, work, "--purge").returncode == 3
    assert run(env, work, "--verify").returncode == 1
    restore_unfrozen(env, work)
    assert run(env, work, "--purge").returncode == 0
    assert saved_manifest(work)["purge_open"] is False
    assert src_rows(env) == 0


def test_a_purge_gate_survives_a_lost_delete_answer_until_verified(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["MOVE_BATCH"] = "1"
    env["STUB_DELETE_THEN_FAIL"] = "1"
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 5  # outcome unknown: the run cannot say
    data = saved_manifest(work)
    assert data["purge_open"] is True and data["purge_checked"] is False
    del env["STUB_DELETE_THEN_FAIL"]
    assert run(env, work, "--purge").returncode == 3
    assert run(env, work, "--verify").returncode == 1
    restore_unfrozen(env, work)  # unaccounted delete
    assert run(env, work, "--purge").returncode == 0


def test_resume_compares_event_ids_as_numbers_not_text(tmp_path: Path) -> None:
    env = make(tmp_path, count=12)  # event ids 1 to 12 cross 9 and 10
    env["MOVE_BATCH"] = "2"
    work = workdir(tmp_path)
    assert run(env, work, "--copy").returncode == 0  # every digest is recorded
    env["STUB_COUNTER"] = str(tmp_path / "calls")
    env["CRASH_AT"] = "8"  # six lead chunks, event chunk 1-2, then the crash
    first = run(env, work, "--copy", "--restart-copy")
    assert first.returncode == 1
    manifest = saved_manifest(work)
    assert manifest["cursor"]["kit_events"] == 2  # "10" sorts before "2" as text
    assert "10" in manifest["digests"]["kit_events"]
    del env["CRASH_AT"]
    resumed = run(env, work, "--copy")
    assert resumed.returncode == 0, resumed.stderr
    assert "resuming an unfinished copy from the recorded cursor" in resumed.stdout
    assert rows(env["STUB_SRC"], "kit_events") == rows(env["STUB_DEST"], "kit_events")


def test_resume_refuses_a_manifest_with_a_non_integer_event_key(
    tmp_path: Path,
) -> None:
    mod = load_script()
    uid = str(uuid.uuid4())
    m = mod.Manifest(tmp_path, "src", "dst", uid, uid)
    m.digests["kit_events"] = {"1": "a" * 64, "x": "b" * 64}
    with pytest.raises(mod.Refused):
        mod.at_or_before("kit_events", "x", 5)
    with pytest.raises(mod.Refused):
        mod.at_or_before("kit_events", "1", "5")
    assert mod.at_or_before("kit_events", "10", 9) is False
    assert mod.at_or_before("kit_events", "2", 10) is True
    assert mod.at_or_before("leads", "0" * 31 + "a", "0" * 31 + "b") is True


def test_resume_accepts_only_canonical_sqlite_event_ids() -> None:
    mod = load_script()
    maximum = 2**63 - 1
    assert mod.at_or_before("kit_events", str(maximum), maximum) is True
    for key in ("-1", "01", str(maximum + 1)):
        with pytest.raises(mod.Refused):
            mod.at_or_before("kit_events", key, maximum)
    for cursor in (-1, maximum + 1, True):
        with pytest.raises(mod.Refused):
            mod.at_or_before("kit_events", "1", cursor)
    assert mod.at_or_before("kit_events", "0", 0) is True
    assert mod.at_or_before("kit_events", "10", 9) is False
    assert mod.at_or_before("kit_events", "9", 10) is True


# ---- round 12: orphaned calls keep the lock, DELETE ... RETURNING, one recovery ----


def test_a_killed_run_keeps_the_lock_while_its_database_call_is_still_running(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["MOVE_BATCH"] = "1"
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    held_env(tmp_path, env, "DELETE FROM", 1)
    proc = start(env, work, "--purge")
    wait_held(env)
    proc.send_signal(9)  # the wrangler child lives on in its own session
    proc.wait(timeout=30)
    assert proc.returncode == -9
    before = src_rows(env)
    for flag in ("--copy", "--purge", "--verify"):  # the orphan still holds the lock
        r = run(env, work, flag)
        assert r.returncode == 7, (flag, r.returncode, r.stderr)
        assert r.stdout == ""
    assert src_rows(env) == before  # nothing ran next to the orphan
    Path(env["HOLD_GO"]).write_text("go")
    env["HOLD_MATCH"] = "no-such-text"
    r = run(env, work, "--verify")
    for _ in range(100):  # the orphan ends; only then is the lock free
        if r.returncode != 7:
            break
        time.sleep(0.1)
        r = run(env, work, "--verify")
    assert r.returncode != 7
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def test_a_timed_out_call_keeps_the_lock_file_for_a_surviving_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    work = workdir(tmp_path)
    lock = mod.RunLock(work)
    lock.acquire()
    fd = lock.fd
    mod.HELD["lock"] = lock
    seen: dict[str, Any] = {}

    def timeout(*_a: Any, **k: Any) -> None:
        seen.update(k)
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(mod.subprocess, "run", timeout)
    try:
        with pytest.raises(mod.Failed):
            mod.Wrangler().call(["d1", "list"])
    finally:
        mod.HELD["lock"] = None
        lock.release()
    assert seen["pass_fds"] == (fd,)
    assert (work / LOCK_FILE).exists()  # a child may still hold this very file


def test_a_row_removed_by_another_client_is_not_recorded_as_deleted(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_OTHER_CLIENT"] = "1"  # the DELETE removes nothing; the rows vanish anyway
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode != 0  # nothing proves the tool deleted them: verify fails
    assert src_rows(env) == 0
    assert saved_manifest(work)["deleted"] == {}


def test_only_rows_the_delete_returned_are_recorded() -> None:
    mod = load_script()
    uid = str(uuid.uuid4())
    m = mod.Manifest(Path("."), "src", "dst", uid, uid)
    saved: list[int] = []
    m.save = lambda: saved.append(1)
    sent = [{"id": 1, "ts": 5, "lead_id": "a" * 32, "kind": "view"}]
    sent.append({"id": 2, "ts": 6, "lead_id": "a" * 32, "kind": "view"})
    progress = mod.Progress()
    changed = dict(sent[0]) | {"ts": 99}  # another version of row 1 came back
    mod.acknowledge_deleted("kit_events", sent, [changed], m, progress)
    assert m.deleted == {} and saved == []
    mod.acknowledge_deleted("kit_events", sent, [sent[1], sent[1]], m, progress)
    assert [e[1] for e in m.deleted[progress.attempt]] == ["2"]


def test_every_delete_statement_asks_for_the_deleted_rows_back() -> None:
    mod = load_script()
    guard = mod.guard_sql({"leads", "kit_events"})
    for table, row in (("leads", {"id": "a" * 32}), ("kit_events", {"id": 1})):
        (sql,) = mod.delete_statements(table, [row], guard)
        assert sql.endswith("RETURNING " + ", ".join(mod.COLUMNS[table]) + ";")


# ---- wrangler 4.162.0 shapes, owner's check on real D1 (2026-11-27) ----------------
# `--command` of one statement: ONE block, `results` holds the rows a RETURNING gave.
# `--file` of several statements: ONE block whose `results` hold a single summary row
# (Database size (MB), Rows read, Rows written, Total queries executed) and no
# per-statement results. The tool must take deleted rows only from the first shape.


def test_purge_sends_each_delete_as_its_own_command_and_records_the_rows(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_LOG"] = str(tmp_path / "calls.log")
    work = workdir(tmp_path)
    total = src_rows(env)
    assert purge(env, work).returncode == 0
    calls = (tmp_path / "calls.log").read_text().split()
    assert "delete-file" not in calls  # a --file run returns no RETURNING rows
    assert calls.count("delete") == total  # one --command per verified row
    kept = [row for rows_ in saved_manifest(work)["deleted"].values() for row in rows_]
    assert len(kept) == total  # every one is acknowledged by its own statement


def test_a_file_run_summary_row_is_not_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = load_script()
    summary = [
        {
            "results": [
                {
                    "Database size (MB)": "0.05",
                    "Rows read": 4,
                    "Rows written": 4,
                    "Total queries executed": 2,
                }
            ],
            "success": True,
            "meta": {},
        }
    ]
    w = mod.Wrangler()
    monkeypatch.setattr(w, "call", lambda _args: json.dumps(summary))
    assert w.execute("db", file=tmp_path / "x.sql") == []
    assert not hasattr(w, "file_rows")
    one = [{"results": [{"id": 1}], "success": True, "meta": {}}]
    monkeypatch.setattr(w, "call", lambda _args: json.dumps(one))
    assert w.execute("db", command="DELETE FROM t WHERE id = 1 RETURNING id;") == [
        {"id": 1}
    ]


def test_delete_sql_is_one_statement_per_row() -> None:
    mod = load_script()
    guard = mod.guard_sql({"leads", "kit_events"})
    rows = [{"id": "a" * 32}, {"id": "b" * 32}]
    out = mod.delete_statements("leads", rows, guard)
    assert len(out) == 2
    for stmt, row in zip(out, rows, strict=True):
        assert stmt.count("DELETE FROM") == 1 and stmt.endswith(";")
        assert row["id"] in stmt


# ---- no row data on a command line (security finding, #772) -----------------------

SPY = """#!{python}
import os, sys
with open(os.environ["ARGV_LOG"], "a") as fh:
    fh.write(repr(sys.argv[1:]) + "\\n")
os.execv(os.environ["STUB_REAL"], [os.environ["STUB_REAL"], *sys.argv[1:]])
"""
PII = ("@example.test", "'US'", "person")


def spy_env(tmp_path: Path, env: dict[str, str]) -> Path:
    """Route wrangler through a wrapper that records every argument it is given."""
    wrapper = tmp_path / "argv-spy"
    wrapper.write_text(SPY.format(python=sys.executable))
    wrapper.chmod(0o700)
    env["STUB_REAL"] = env["WRANGLER"]
    env["WRANGLER"] = str(wrapper)
    env["ARGV_LOG"] = str(tmp_path / "argv.log")
    return Path(env["ARGV_LOG"])


def assert_clean(argv_log: Path, *outputs: str) -> None:
    text = argv_log.read_text() if argv_log.exists() else ""
    for needle in PII:
        assert needle not in text, needle
        for out in outputs:
            assert needle not in out, needle


def test_no_row_value_reaches_a_command_line_in_any_mode(tmp_path: Path) -> None:
    env = make(tmp_path)
    log = spy_env(tmp_path, env)
    work = workdir(tmp_path)
    outs = []
    for flags in (("--freeze",), ("--copy",)):
        r = run(env, work, *flags)
        assert r.returncode == 0, r.stderr
        outs += [r.stdout, r.stderr]
    r = purge(env, work)
    assert r.returncode == 0, r.stderr
    outs += [r.stdout, r.stderr]
    r = run(env, work, "--verify")
    outs += [r.stdout, r.stderr]
    r = run(env, work, "--recover-from-export")
    assert r.returncode == 0, r.stderr
    outs += [r.stdout, r.stderr]
    assert "DELETE FROM" in log.read_text()  # the spy saw the deletes
    assert "length(" not in log.read_text()  # nor anything derived from a value
    assert_clean(log, *outs)


@pytest.mark.parametrize("failure", ["STUB_DELETE_THEN_FAIL", "STUB_FAIL_FILE"])
def test_no_row_value_reaches_a_command_line_when_a_call_fails(
    tmp_path: Path, failure: str
) -> None:
    env = make(tmp_path)
    log = spy_env(tmp_path, env)
    env[failure] = "1"
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode != 0
    assert_clean(log, r.stdout, r.stderr)


def test_a_row_changed_after_it_was_read_is_kept_in_dest_not_lost(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    log = spy_env(tmp_path, env)
    env["STUB_INJECT"] = "email"  # the address changes right before its DELETE
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode == 0, r.stderr
    assert src_rows(env) == 0
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        got = db.execute(
            "SELECT email FROM leads WHERE id = ?", (format(2, "032x"),)
        ).fetchone()
    # the deleted version came back and was kept
    assert got == ("changed@example.test",)
    assert "changed@example.test" not in log.read_text()
    assert run(env, work, "--verify").returncode == 0


def test_a_changed_row_survives_in_a_private_file_when_keeping_it_fails(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    log = spy_env(tmp_path, env)
    env["STUB_INJECT"] = "email"  # the address changes right before its DELETE
    env["STUB_MARK"] = str(tmp_path / "changed")  # then every file call fails
    work = workdir(tmp_path)
    r = purge(env, work)
    assert r.returncode == 5, (r.returncode, r.stderr)  # VerifyRequired
    saved = [p for p in work.iterdir() if p.name.startswith("lead-returned-")]
    assert len(saved) == 1
    assert stat.S_IMODE(saved[0].stat().st_mode) == 0o600
    data = json.loads(saved[0].read_text())
    assert data["row"]["email"] == "changed@example.test"  # the only copy is kept
    assert str(saved[0]) in r.stderr  # the path is named
    for out in (r.stdout, r.stderr, log.read_text()):
        assert "changed@example.test" not in out
    assert "partial purge" not in r.stderr


def test_a_delete_statement_carries_nothing_derived_from_a_personal_column() -> None:
    mod = load_script()
    guard = mod.guard_sql({"leads", "kit_events"})
    base = {c: None for c in mod.COLUMNS["leads"]} | {"id": "a" * 32, "created_ts": 5}
    short = base | {"email": "a@b.test", "country": "US"}
    long = base | {"email": "much.longer.address@example.test", "country": None}
    (one,) = mod.delete_statements("leads", [short], guard)
    (two,) = mod.delete_statements("leads", [long], guard)
    assert one == two  # no value, length or null-ness of email or country is in it
    # (the RETURNING list names the columns; no condition on them is sent)
    assert "length(" not in one and "email IS" not in one and "country IS" not in one


class Crash(BaseException):
    """A simulated SIGKILL or power loss: nothing in the tool catches it."""


def crash_after(mod: Any, name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make function `name` run, then crash right after it returns. For the export
    writer only the call that keeps a changed row counts (it is given the rows)."""
    real = getattr(mod, name)

    def wrapper(*a: Any, **k: Any) -> Any:
        out = real(*a, **k)
        if name != "write_recovery_export" or len(a) > 4 or "rows" in k:
            raise Crash
        return out

    monkeypatch.setattr(mod, name, wrapper)


def crash_in_record(mod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Crash when the changed row is recorded (after its write to DEST)."""
    real = mod.Manifest.record

    def record(self: Any, table: str, row: dict[str, Any]) -> None:
        if row.get("email") == "changed@example.test":
            raise Crash
        real(self, table, row)

    monkeypatch.setattr(mod.Manifest, "record", record)


def crash_before_export(mod: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Crash after the manifest saved the changed row, before its export is written."""
    real = mod.write_recovery_export

    def wrapper(*a: Any, **k: Any) -> Any:
        if len(a) > 4 or "rows" in k:
            raise Crash
        return real(*a, **k)

    monkeypatch.setattr(mod, "write_recovery_export", wrapper)


def dest_email(env: dict[str, str], lead_number: int) -> str | None:
    with closing(sqlite3.connect(env["STUB_DEST"])) as db:
        row = db.execute(
            "SELECT email FROM leads WHERE id = ?", (format(lead_number, "032x"),)
        ).fetchone()
    return None if row is None else str(row[0])


@pytest.mark.parametrize(
    "stage", ["rescue-written", "dest-written", "manifest-saved", "export-written"]
)
def test_a_crash_after_each_stage_of_keeping_a_changed_row_loses_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    env = make(tmp_path)
    env["STUB_INJECT"] = "email"  # the address changes right before its DELETE
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)
    # Each boundary is a crash the tool cannot catch, right after that stage.
    if stage == "rescue-written":
        crash_after(mod, "save_returned_row", monkeypatch)
    elif stage == "dest-written":
        # record() follows the dest write and its read-back
        crash_in_record(mod, monkeypatch)
    elif stage == "manifest-saved":
        crash_before_export(mod, monkeypatch)
    else:
        crash_after(mod, "write_recovery_export", monkeypatch)
    with pytest.raises(Crash):
        mod.main(main_args(work, "--purge"))
    monkeypatch.undo()
    # The rescue file is registered durably, the changed row is on disk.
    manifest = saved_manifest(work)
    names = [p.name for p in work.iterdir() if p.name.startswith("lead-returned-")]
    assert names and set(names) <= set(manifest["rescue_files"]), stage
    # Recovery consumes it: the changed row is back in SOURCE and in DEST, the file
    # and its registration are gone, and the freeze is in place again.
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    r = run(env, work, "--recover-from-export")
    assert r.returncode == 0, (stage, r.stderr)
    assert [p for p in work.iterdir() if p.name.startswith("lead-returned-")] == []
    assert saved_manifest(work)["rescue_files"] == []
    assert dest_email(env, 2) == "changed@example.test", stage
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        got = db.execute(
            "SELECT email FROM leads WHERE id = ?", (format(2, "032x"),)
        ).fetchone()
    assert got == ("changed@example.test",), stage
    assert frozen_names(env) == {"leads_frozen", "kit_events_frozen"}


def test_a_rescue_file_that_could_not_be_written_still_reaches_dest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(tmp_path)
    env["STUB_INJECT"] = "email"
    work = workdir(tmp_path)
    assert run(env, work, "--freeze").returncode == 0
    mod = load_with_env(env, monkeypatch)

    def no_space(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mod, "save_returned_row", no_space)
    assert mod.main(main_args(work, "--purge")) == 0
    assert dest_email(env, 2) == "changed@example.test"
    assert saved_manifest(work)["rescue_files"] == []
    assert [p for p in work.iterdir() if p.name.startswith("lead-returned-")] == []


def test_the_normal_path_consumes_its_rescue_file(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_INJECT"] = "email"
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    assert saved_manifest(work)["rescue_files"] == []
    assert [p for p in work.iterdir() if p.name.startswith("lead-returned-")] == []


def frozen_names(env: dict[str, str]) -> set[str]:
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        return {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN ('leads_frozen', 'kit_events_frozen')"
            )
        }


def test_one_recovery_command_restores_and_freezes_again(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    before = rows(env["STUB_SRC"], "leads")
    assert purge(env, work).returncode == 0
    assert src_rows(env) == 0
    r = run(env, work, "--recover-from-export")
    assert r.returncode == 0, r.stderr
    assert rows(env["STUB_SRC"], "leads") == before
    assert frozen_names(env) == {"leads_frozen", "kit_events_frozen"}
    again = run(env, work, "--recover-from-export")  # safe to repeat
    assert again.returncode == 0, again.stderr
    assert frozen_names(env) == {"leads_frozen", "kit_events_frozen"}


def test_a_failed_recovery_still_freezes_again(tmp_path: Path) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    env["STUB_FAIL_FILE"] = "1"  # every statement file fails: the restore fails
    r = run(env, work, "--recover-from-export")
    assert r.returncode == 1
    assert frozen_names(env) == {"leads_frozen", "kit_events_frozen"}


def test_a_bad_export_stops_the_recovery_before_the_freeze_is_lifted(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    (export,) = [p for p in work.iterdir() if p.name.startswith("lead-recovery-")]
    export.write_text("not json")
    r = run(env, work, "--recover-from-export")
    assert r.returncode == 3
    assert frozen_names(env) == {"leads_frozen", "kit_events_frozen"}


# ---- round 13: a permanent lock file, recovery always freezes again ---------------

DETACH = """#!{python}
import os, sys, time
args = sys.argv[1:]
flag = os.environ["DETACH_FLAG"]
if not os.path.exists(flag):
    open(flag, "w").write("1")
    if os.fork() == 0:  # a detached grandchild that keeps the inherited descriptors
        for n in (0, 1, 2):
            os.close(n)
        for _ in range(300):
            if os.path.exists(os.environ["DETACH_GO"]):
                break
            time.sleep(0.1)
        os._exit(0)
os.execv(os.environ["STUB_REAL"], [os.environ["STUB_REAL"], *args])
"""

FAIL_NTH_DROP = """#!{python}
import os, sys
args = sys.argv[1:]
if "--command" in args and "DROP TRIGGER" in args[args.index("--command") + 1]:
    counter = os.environ["DROP_COUNTER"]
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    if n + 1 == 2:
        sys.exit(1)
os.execv(os.environ["STUB_REAL"], [os.environ["STUB_REAL"], *args])
"""


def test_a_surviving_detached_grandchild_keeps_the_lock_after_a_normal_end(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    wrapper = tmp_path / "detach-wrapper"
    wrapper.write_text(DETACH.format(python=sys.executable))
    wrapper.chmod(0o700)
    env["STUB_REAL"] = env["WRANGLER"]
    env["WRANGLER"] = str(wrapper)
    env["DETACH_FLAG"] = str(tmp_path / "detach-flag")
    env["DETACH_GO"] = str(tmp_path / "detach-go")
    first = run(env, work, "--freeze")
    assert first.returncode == 0, first.stderr  # the direct wrapper exited 0
    assert (work / LOCK_FILE).exists()
    second = run(env, work, "--freeze")  # the grandchild still holds the same file
    assert second.returncode == 7, second.stderr
    assert "another run holds the lock" in second.stderr
    Path(env["DETACH_GO"]).write_text("go")
    r = run(env, work, "--freeze")
    for _ in range(100):  # the grandchild ends; only then is the lock free
        if r.returncode != 7:
            break
        time.sleep(0.1)
        r = run(env, work, "--freeze")
    assert r.returncode == 0, r.stderr


def test_a_failure_after_the_first_trigger_drop_still_freezes_again(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    work = workdir(tmp_path)
    assert purge(env, work).returncode == 0
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        before = sorted(
            db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN ('leads_frozen', 'kit_events_frozen')"
            ).fetchall()
        )
    assert len(before) == 2
    wrapper = tmp_path / "drop-wrapper"
    wrapper.write_text(FAIL_NTH_DROP.format(python=sys.executable))
    wrapper.chmod(0o700)
    env["STUB_REAL"] = env["WRANGLER"]
    env["WRANGLER"] = str(wrapper)
    env["DROP_COUNTER"] = str(tmp_path / "drop-count")
    r = run(env, work, "--recover-from-export")
    assert r.returncode != 0  # the second DROP failed
    assert "Traceback" not in r.stderr
    with closing(sqlite3.connect(env["STUB_SRC"])) as db:
        after = sorted(
            db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name IN ('leads_frozen', 'kit_events_frozen')"
            ).fetchall()
        )
    assert after == before  # both exact definitions are back
