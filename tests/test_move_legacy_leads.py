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
if os.environ.get("STUB_FAIL_FILE") and any(a.startswith("--file=") for a in args):
    sys.exit(1)
if os.environ.get("CRASH_AT") and any(a.startswith("--file=") for a in args):
    counter = os.environ["STUB_COUNTER"]
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    open(counter, "w").write(str(n + 1))
    if n + 1 == int(os.environ["CRASH_AT"]):
        sys.exit(1)
if any(a.startswith("--file=") for a in args):
    sql = open([a for a in args if a.startswith("--file=")][0].split("=", 1)[1]).read()
    if log and name == "analytics-db" and "DELETE FROM" in sql:
        open(log, "a").write("delete\\n")
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
    db.executescript(sql)
    db.commit()
    lost = os.environ.get("STUB_DELETE_THEN_FAIL")
    if lost and name == "analytics-db" and "DELETE FROM" in sql:
        sys.exit(1)  # applied, but the caller never hears it
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
    names = sorted(p.name for p in work.iterdir())
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
    assert [p.name for p in work.iterdir()] == ["move-manifest.json"]  # no sql file


def test_a_failed_copy_deletes_nothing(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_FAIL_FILE"] = "1"
    r = purge(env, workdir(tmp_path))
    assert r.returncode == 1
    assert count(env["STUB_SRC"], "leads") == 3
    assert [p.name for p in (tmp_path / "work").iterdir()] == ["move-manifest.json"]


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
    assert list(work.iterdir()) == []  # no manifest either


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
    real = mod.run_file
    deletes = []

    def spy(w: Any, db: str, wd: Path, sql: str) -> None:
        real(w, db, wd, sql)
        if db == "analytics-db" and sql.startswith("DELETE"):
            deletes.append(sql)
            if len(deletes) == 1:  # after the first delete chunk: alter a later row
                dest_exec(
                    env,
                    "UPDATE kit_events SET ts = ts + 1 WHERE lead_id = '"
                    + format(4, "032x")
                    + "'",
                )

    monkeypatch.setattr(mod, "run_file", spy)
    assert mod.main(main_args(work, "--purge")) == 4  # partial, safe to resume
    assert len(deletes) == 1  # no delete was sent after the change
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
    sql = mod.delete_sql("leads", [{"id": "a" * 32}], guard)
    assert sql.count(guard) == 1 and "sqlite_master" in sql
    assert "leads_frozen" in guard and "kit_events_frozen" in guard
    assert "sqlite_master" in mod.delete_sql("kit_events", [{"id": 1}], guard)


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
    assert list(work.iterdir()) == []


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
    assert list(work.iterdir()) == []


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
