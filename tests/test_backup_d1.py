# SPDX-License-Identifier: Apache-2.0
"""scripts/backup-d1.sh: the owner-run export of the analytics tables.

A stub stands in for ``wrangler`` and answers from a local SQLite file built
from the Worker's own migrations. Everything is synthetic."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from test_check_dump import schema_sql

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "backup-d1.sh"

STUB = """#!{python}
import json, os, sqlite3, sys, time
args = sys.argv[1:]
if os.environ.get("STUB_HANG"):
    time.sleep(30)
db = sqlite3.connect(os.environ["STUB_DB"])
if args[:2] == ["d1", "execute"]:
    sql = args[args.index("--command") + 1]
    if "d1_migrations" in sql and os.environ.get("STUB_FAIL_MIGRATIONS"):
        sys.exit(1)
    if "d1_migrations" in sql and os.environ.get("STUB_BAD_MIGRATIONS"):
        print("not json")
        sys.exit(0)
    cur = db.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    listing = sql.startswith("SELECT name FROM sqlite_master")
    if os.environ.get("STUB_PAD_LISTING") and listing:
        # Synthetic system-table names (the script accepts sqlite_*) to make the
        # listing larger than a pipe buffer.
        rows += [{{"name": f"sqlite_pad_{{i:04d}}_" + "x" * 1000}} for i in range(300)]
    print(json.dumps([{{"results": rows, "success": True}}]))
elif args[:2] == ["d1", "export"]:
    table = [a for a in args if a.startswith("--table=")][0].split("=", 1)[1]
    out = [a for a in args if a.startswith("--output=")][0].split("=", 1)[1]
    if os.environ.get("STUB_FAIL") == table:
        sys.exit(1)
    schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()[0]
    inserts = [l for l in db.iterdump() if l.startswith(f'INSERT INTO "{{table}}"')]
    with open(out, "w") as fh:
        fh.write(schema + ";\\n" + "\\n".join(inserts) + "\\n")
    print("Executing on remote database SECRET-DB-ID-0000")
else:
    sys.exit(2)
"""


def make(tmp_path: Path) -> dict[str, str]:
    db_path = tmp_path / "d1.sqlite"
    with closing(sqlite3.connect(db_path)) as db:
        for f in sorted((ROOT / "worker" / "migrations").glob("*.sql")):
            db.executescript(schema_sql(f.read_text()))
        db.execute(
            "INSERT INTO view_daily (site, day, day_tz, page, src_kind, src, country,"
            " bot_class, views, uniques) VALUES ('links', '2026-11-01', '2026-11-01',"
            " 'home', 'd', '', 'US', 'h', 9, 0)"
        )
        db.execute(
            "INSERT INTO view_hour_daily (site, day, hour, bot_class, views, clicks)"
            " VALUES ('links', '2026-11-01', 9, 'h', 4, 1)"
        )
        db.execute(
            "INSERT INTO view_city_daily (site, day, country, region, city,"
            " bot_class, views, uniques)"
            " VALUES ('links', '2026-11-01', 'US', 'NY', 'Springfield', 'h', 3, 0)"
        )
        db.execute(
            "INSERT INTO click_events (ts, link_id, site)"
            " VALUES (1792998000000, 'a', 'links')"
        )
        db.execute(
            "INSERT INTO leads (id, email, created_ts, last_seen_ts, source) VALUES ('"
            + "a" * 32
            + "', 'person@example.test', 1, 1, 'gate')"
        )
        db.execute(
            "INSERT INTO kit_events (ts, lead_id, kind) VALUES (1792998000000, '"
            + "a" * 32
            + "', 'view')"
        )
        db.execute(
            "CREATE TABLE d1_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " name TEXT UNIQUE, applied_at TIMESTAMP NOT NULL"
            " DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute("INSERT INTO d1_migrations (name) VALUES ('0001_init.sql')")
        # Wrangler records every applied migration by file name.
        for f in sorted((ROOT / "worker" / "migrations").glob("*.sql")):
            db.execute("INSERT INTO d1_migrations (name) VALUES (?)", (f.name,))
        db.commit()
    stub = tmp_path / "wrangler-stub"
    stub.write_text(STUB.format(python=sys.executable))
    stub.chmod(0o700)
    return {
        "PATH": "/usr/bin:/bin",
        "WRANGLER": str(stub),
        "STUB_DB": str(db_path),
    }


def dest(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/usr/bin/bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_database_setup_closes_its_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connections: list[sqlite3.Connection] = []
    connect = sqlite3.connect

    def retained_connect(database: str | Path) -> sqlite3.Connection:
        connection = connect(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", retained_connect)
    make(tmp_path)

    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


def test_dry_run_is_the_default_and_writes_nothing(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d))
    assert r.returncode == 0, r.stderr
    assert "dry run" in r.stdout
    assert "view_daily" in r.stdout
    assert list(d.iterdir()) == []


def test_apply_writes_private_files_without_lead_tables(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    (out,) = list(d.iterdir())
    assert stat.S_IMODE(out.stat().st_mode) == 0o700
    names = {p.name for p in out.iterdir()}
    assert "view_daily.sql" in names
    assert "click_events.sql" in names
    assert "SHA256SUMS" in names
    for banned in ("leads", "kit_events", "visitor_seen", "view_events"):
        assert f"{banned}.sql" not in names
    for p in out.iterdir():
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert "person@example.test" not in "".join(p.read_text() for p in out.iterdir())
    sums = subprocess.run(  # noqa: S603
        ["/usr/bin/sha256sum", "-c", "SHA256SUMS"],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sums.returncode == 0, sums.stdout


def test_output_never_prints_an_id_or_a_row(tmp_path: Path) -> None:
    env = make(tmp_path)
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 0, r.stderr
    both = r.stdout + r.stderr
    assert "SECRET-DB-ID" not in both
    assert "2026-11-01" not in both


def test_refuses_a_folder_inside_a_git_work_tree(tmp_path: Path) -> None:
    env = make(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["/usr/bin/git", "init", "-q", str(repo)], check=True)  # noqa: S603
    d = repo / "backups"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    for extra in ([], ["--apply"]):
        r = run(env, "reach-db", str(d), *extra)
        assert r.returncode == 3
        assert "git repository" in r.stderr
    assert list(d.iterdir()) == []


def test_refuses_an_open_folder_and_a_symlink(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    d.chmod(0o750)
    assert run(env, "reach-db", str(d)).returncode == 3
    d.chmod(0o700)
    link = tmp_path / "link"
    link.symlink_to(d)
    assert run(env, "reach-db", str(link)).returncode == 3
    assert run(env, "reach-db", str(tmp_path / "missing")).returncode == 3


def test_bad_usage_exits_two(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = str(dest(tmp_path))
    assert run(env).returncode == 2
    assert run(env, "reach-db", d, "--nope").returncode == 2
    assert run(env, "bad name;rm", d).returncode == 2
    assert run({**env, "BACKUP_TIMEOUT": "0"}, "reach-db", d).returncode == 2
    assert run({**env, "BACKUP_TIMEOUT": "9999"}, "reach-db", d).returncode == 2


def test_a_failed_export_removes_the_partial_run(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    r = run({**env, "STUB_FAIL": "view_daily"}, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert list(d.iterdir()) == []


def test_a_dump_with_an_address_is_rejected_and_removed(tmp_path: Path) -> None:
    env = make(tmp_path)
    db = sqlite3.connect(env["STUB_DB"])
    db.execute("INSERT INTO view_meta (key, value) VALUES ('note', '198.51.100.7')")
    db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "198.51.100.7" not in r.stdout + r.stderr
    assert list(d.iterdir()) == []


def test_a_hung_wrangler_is_bounded(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    r = run(
        {**env, "STUB_HANG": "1", "BACKUP_TIMEOUT": "1"},
        "reach-db",
        str(d),
        "--apply",
    )
    assert r.returncode == 1
    assert list(d.iterdir()) == []


def test_the_script_is_executable_and_has_no_em_dash() -> None:
    assert os.access(SCRIPT, os.X_OK)
    assert chr(0x2014) not in SCRIPT.read_text()


def test_the_export_restores_into_an_empty_database(tmp_path: Path) -> None:
    """The offline half of the restore drill: load the files into a fresh
    SQLite file and compare row counts with the source."""
    env = make(tmp_path)
    d = dest(tmp_path)
    assert run(env, "reach-db", str(d), "--apply").returncode == 0
    (out,) = list(d.iterdir())
    fresh = sqlite3.connect(tmp_path / "scratch.sqlite")
    for f in sorted(out.glob("*.sql")):
        fresh.executescript(f.read_text())
    source = sqlite3.connect(env["STUB_DB"])
    for table in (
        "view_daily",
        "click_events",
        "counters",
        "view_meta",
        "view_hour_daily",
        "view_city_daily",
    ):
        query = f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed names
        assert fresh.execute(query).fetchone() == source.execute(query).fetchone()
    names = {r[0] for r in fresh.execute("SELECT name FROM sqlite_master")}
    assert "leads" not in names
    assert "kit_events" not in names


def test_a_long_table_listing_never_skips_a_table_that_exists(tmp_path: Path) -> None:
    """The listing is larger than a pipe buffer, with the wanted tables first.
    A `printf | grep -q` membership test under pipefail then loses the race with
    SIGPIPE and reports an existing table as missing (seen as a flaky
    restore test on CI)."""
    env = {**make(tmp_path), "STUB_PAD_LISTING": "1"}
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    assert "skipped (not in this database): view_daily" not in r.stdout
    assert "skipped (not in this database): click_events" not in r.stdout
    (out,) = list(d.iterdir())
    assert (out / "view_daily.sql").exists()
    assert (out / "click_events.sql").exists()


def test_migration_history_is_part_of_the_backup(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    assert run(env, "reach-db", str(d), "--apply").returncode == 0
    (out,) = list(d.iterdir())
    assert "0001_init.sql" in (out / "d1_migrations.sql").read_text()


def test_row_ids_above_two_to_the_24th_do_not_fail_the_check(tmp_path: Path) -> None:
    env = make(tmp_path)
    db = sqlite3.connect(env["STUB_DB"])
    db.execute(
        "INSERT INTO click_events (id, ts, link_id, site)"
        " VALUES (20000000, 1792998000000, 'a', 'links')"
    )
    db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    assert len(list(d.iterdir())) == 1


def _make_older_than_0013(env: dict[str, str]) -> None:
    """A database that never applied migration 0013: no alert tables, and the
    migration is not recorded."""
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DROP TABLE alert_quota")
        db.execute("DROP TABLE alert_dedup")
        db.execute("DELETE FROM d1_migrations WHERE name = '0013_alerts.sql'")
        db.commit()


def test_an_older_schema_missing_a_table_still_backs_up(tmp_path: Path) -> None:
    env = make(tmp_path)
    _make_older_than_0013(env)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    assert (
        "skipped (not in this database): alert_quota (migration 0013_alerts.sql"
        " is not applied)" in r.stdout
    )
    (out,) = list(d.iterdir())
    assert not (out / "alert_quota.sql").exists()
    assert (out / "view_daily.sql").exists()


def test_a_table_whose_migration_is_applied_must_exist(tmp_path: Path) -> None:
    """Migration 0013 IS recorded as applied, so a missing alert table is lost
    data, not an old schema: the run fails and keeps nothing."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DROP TABLE alert_quota")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "required table(s) missing" in r.stderr
    assert "alert_quota" in r.stderr
    assert "done:" not in r.stdout
    assert list(d.iterdir()) == []


@pytest.mark.parametrize("breakage", ["no-table", "no-query-answer", "bad-answer"])
def test_unreadable_applied_migrations_fail_closed(
    tmp_path: Path, breakage: str
) -> None:
    """Without the migration record the script cannot tell which tables must
    exist, so it stops instead of guessing."""
    env = make(tmp_path)
    if breakage == "no-table":
        with closing(sqlite3.connect(env["STUB_DB"])) as db:
            db.execute("DROP TABLE d1_migrations")
            db.commit()
    elif breakage == "no-query-answer":
        env["STUB_FAIL_MIGRATIONS"] = "1"
    else:
        env["STUB_BAD_MIGRATIONS"] = "1"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "applied migrations" in r.stderr
    assert "done:" not in r.stdout
    assert list(d.iterdir()) == []


def test_both_views_are_part_of_the_backup(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    (out,) = list(d.iterdir())
    assert "view_hour_daily" in (out / "view_hour_daily.sql").read_text()
    assert "Springfield" in (out / "view_city_daily.sql").read_text()
    assert "never exported: leads, kit_events, visitor_seen, view_events" in (
        run(env, "reach-db", str(dest_second(tmp_path))).stdout
    )


def dest_second(tmp_path: Path) -> Path:
    d = tmp_path / "backups2"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def test_smoke_script_needs_no_bash_four_expansion() -> None:
    text = (ROOT / "scripts" / "smoke.sh").read_text()
    assert not re.search(r"\$\{[a-z_]+,,\}", text)


def _guard_block() -> str:
    text = SCRIPT.read_text()
    begin = text.index("# --- shell guard begin")
    end = text.index("# --- shell guard end ---") + len("# --- shell guard end ---")
    # Nothing but the shebang and comments may come before the guard, and the
    # guard must precede every bash-only construct.
    for line in text[:begin].splitlines():
        assert line.startswith("#") or not line.strip(), line
    assert end < text.index("set -euo pipefail")
    return text[begin:end]


def test_the_script_refuses_a_bash_older_than_four() -> None:
    text = SCRIPT.read_text()
    assert "bash 4.0" in text
    assert "bash 4.0" in (ROOT / "docs" / "backup-restore.md").read_text()
    guard = _guard_block()
    # POSIX syntax only: no arrays, no [[ ]], no ${..[..]}.
    assert "[[" not in guard
    assert not re.search(r"\$\{[A-Za-z_]+\[", guard)


@pytest.mark.parametrize(
    ("version", "code"), [("3.2.57(1)-release", 2), ("", 2), ("5.2.0(1)", 0)]
)
def test_the_guard_runs_first_on_a_posix_shell(version: str, code: int) -> None:
    """The guard block, run by a plain POSIX shell (dash where sh is dash) with a
    faked BASH_VERSION: it exits 2 for an old or missing bash and falls through
    for bash 4 or newer. Residual: a real bash 3.2 is not available here."""
    shell = "/bin/sh"
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [shell, "-c", _guard_block() + "\nexit 0\n"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "BASH_VERSION": version},
        timeout=30,
        check=False,
    )
    assert r.returncode == code, r.stderr
    if code == 2:
        assert "bash 4.0 or newer is required" in r.stderr


def test_running_the_script_under_sh_gets_the_bash_diagnostic_or_usage() -> None:
    """`sh scripts/backup-d1.sh` never dies of a shell syntax error first: a
    non-bash sh reports the bash requirement (exit 2), a bash sh gets usage
    (exit 2)."""
    r = subprocess.run(  # noqa: S603
        ["/bin/sh", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        check=False,
    )
    assert r.returncode == 2, r.stderr
    assert "illegal option" not in r.stderr
    assert "bash 4.0 or newer" in r.stderr or "usage:" in r.stderr


@pytest.mark.skipif(not Path("/usr/bin/setfacl").exists(), reason="no setfacl")
@pytest.mark.parametrize("entry", ["u:nobody:rx", "d:u:nobody:rx"])
def test_refuses_a_folder_with_an_extra_acl(tmp_path: Path, entry: str) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    set_acl = subprocess.run(  # noqa: S603
        ["/usr/bin/setfacl", "-m", entry, str(d)],
        capture_output=True,
        check=False,
    )
    if set_acl.returncode != 0:
        pytest.skip("filesystem has no ACL support")
    r = run(env, "reach-db", str(d))
    assert r.returncode == 3
    assert "ACL" in r.stderr


def test_it_says_the_export_is_not_one_snapshot(tmp_path: Path) -> None:
    env = make(tmp_path)
    r = run(env, "reach-db", str(dest(tmp_path)))
    assert "not a single point-in-time snapshot" in r.stdout


def test_the_coverage_table_is_part_of_the_backup(tmp_path: Path) -> None:
    env = make(tmp_path)
    db = sqlite3.connect(env["STUB_DB"])
    db.execute(
        "INSERT INTO analytics_coverage (day, hours) VALUES ('2026-11-01', ?)",
        ("o" * 23 + "-",),
    )
    db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    (out,) = list(d.iterdir())
    assert "2026-11-01" in (out / "analytics_coverage.sql").read_text()


def _script_array(name: str) -> list[str]:
    m = re.search(rf"^{name}=\((.*?)\)", SCRIPT.read_text(), re.DOTALL | re.MULTILINE)
    assert m, f"{name} array not found in backup-d1.sh"
    return m.group(1).split()


def test_a_missing_required_table_fails_the_run(tmp_path: Path) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DROP TABLE view_daily")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "required table(s) missing" in r.stderr
    assert "view_daily" in r.stderr
    assert "done:" not in r.stdout
    assert list(d.iterdir()) == []


def test_tables_of_an_unapplied_migration_are_skipped_with_a_notice(
    tmp_path: Path,
) -> None:
    """0013 is not recorded and its tables are absent: success with a notice.
    The same absence with 0013 recorded is the failing case above."""
    env = make(tmp_path)
    _make_older_than_0013(env)
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 0, r.stderr
    assert "skipped (not in this database): alert_quota" in r.stdout
    assert "skipped (not in this database): alert_dedup" in r.stdout
    assert "0013_alerts.sql is not applied" in r.stdout


def test_a_table_name_with_a_line_break_is_refused(tmp_path: Path) -> None:
    """A name that embeds another table's name between line breaks would make a
    table that is not there look present. The listing is rejected."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DROP TABLE view_daily")
        db.execute('CREATE TABLE "zz\nview_daily\nzz" (a)')
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "invalid table name" in r.stderr
    assert list(d.iterdir()) == []


def _migration_tables(tmp_path: Path) -> dict[str, str]:
    """table -> the migration file that first created it, derived by running the
    migrations one at a time."""
    introduced: dict[str, str] = {}
    with closing(sqlite3.connect(tmp_path / "m.sqlite")) as db:
        for f in sorted((ROOT / "worker" / "migrations").glob("*.sql")):
            db.executescript(schema_sql(f.read_text()))
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ):
                if not name.startswith("sqlite_"):
                    introduced.setdefault(name, f.name)
    return introduced


def test_the_inventory_matches_the_migrations(tmp_path: Path) -> None:
    """The script's table -> introducing migration map must equal the one derived
    from the migrations, table for table. A new table, a moved table or a changed
    map fails here; so does a table that is in neither the map nor the
    never-exported list."""
    derived = _migration_tables(tmp_path)
    # Created by wrangler itself, not by a migration.
    derived["d1_migrations"] = "wrangler"
    never = {"leads", "kit_events", "visitor_seen", "view_events"}
    tables = set(_script_array("tables"))
    mapping: dict[str, str] = {}
    for entry in _script_array("introduced_by"):
        table, _, migration = entry.partition(":")
        assert table not in mapping, f"{table} appears twice in introduced_by"
        mapping[table] = migration
    assert "optional_tables" not in SCRIPT.read_text()
    assert set(mapping) == tables, "introduced_by and tables list different tables"
    assert not (tables & never)
    assert {t: m for t, m in derived.items() if t not in never} == mapping
    assert set(derived) == tables | never
    # The two aggregate views are exported on purpose (#932, owner decision).
    assert {"view_hour_daily", "view_city_daily"} <= tables
    for m in mapping.values():
        assert m == "wrangler" or (ROOT / "worker" / "migrations" / m).is_file()


def test_a_populated_table_outside_the_inventory_fails_the_run(
    tmp_path: Path,
) -> None:
    """An existing table the script does not know would be silently left out of
    the backup: the run fails before any export and names it (not its rows)."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("CREATE TABLE emergency_data (id INTEGER PRIMARY KEY, payload TEXT)")
        db.execute("INSERT INTO emergency_data VALUES (1, 'only-copy-marker')")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "emergency_data" in r.stderr
    assert "only-copy-marker" not in r.stdout + r.stderr
    assert "done:" not in r.stdout
    assert "skipped" not in r.stdout
    assert list(d.iterdir()) == []


def test_only_system_tables_outside_the_inventory_pass(tmp_path: Path) -> None:
    """The test database already holds sqlite_sequence (AUTOINCREMENT); the
    reserved _cf_KV table is the one other accepted name."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        assert "sqlite_sequence" in names
        db.execute("CREATE TABLE _cf_KV (key TEXT PRIMARY KEY, value BLOB)")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    (out,) = list(d.iterdir())
    assert not (out / "_cf_KV.sql").exists()
    assert not (out / "sqlite_sequence.sql").exists()


@pytest.mark.parametrize("name", ["_cf_OTHER", "cf_KV", "_cf_KVX", "sqlitex_a"])
def test_the_ignore_list_is_exact_names(tmp_path: Path, name: str) -> None:
    """Near misses of the ignored names are unexpected tables, not wildcards."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute(f'CREATE TABLE "{name}" (a)')
        db.commit()
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 1
    assert name in r.stderr


def test_the_never_exported_tables_are_accepted_when_present(tmp_path: Path) -> None:
    """leads and friends exist in the database and are left out on purpose; they
    must not trip the unexpected-table check."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    assert {"leads", "kit_events", "visitor_seen", "view_events"} <= names
    assert run(env, "reach-db", str(dest(tmp_path)), "--apply").returncode == 0


def test_an_existing_table_is_exported_even_if_its_migration_is_not_recorded(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DELETE FROM d1_migrations WHERE name = '0013_alerts.sql'")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    (out,) = list(d.iterdir())
    assert (out / "alert_quota.sql").exists()
    assert (out / "alert_dedup.sql").exists()
    assert "notice: table(s) exist although their migration is not recorded" in r.stderr
    assert "alert_quota(0013_alerts.sql)" in r.stderr


def test_a_recorded_migration_the_script_does_not_know_is_a_notice(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("INSERT INTO d1_migrations (name) VALUES ('0013_alerts_old.sql')")
        db.commit()
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 0, r.stderr
    assert "notice: recorded migration(s) this script does not know" in r.stderr
    assert "0013_alerts_old.sql" in r.stderr


def test_an_empty_migration_record_makes_every_table_required(
    tmp_path: Path,
) -> None:
    """A database never migrated through wrangler: nothing recorded cannot mean
    "older schema", so a missing table fails the run."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DELETE FROM d1_migrations")
        db.execute("DROP TABLE alert_quota")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "no applied migration is recorded" in r.stderr
    assert "required table(s) missing" in r.stderr
    assert "alert_quota" in r.stderr
    assert list(d.iterdir()) == []


def test_the_lists_in_the_script_match_the_repository_and_each_other() -> None:
    migration_files = {p.name for p in (ROOT / "worker" / "migrations").glob("*.sql")}
    assert set(_script_array("migrations")) == migration_files
    assert len(_script_array("migrations")) == len(migration_files)
    for entry in _script_array("introduced_by"):
        migration = entry.partition(":")[2]
        assert migration == "wrangler" or migration in migration_files
    assert _script_array("never_exported") == [
        "leads",
        "kit_events",
        "visitor_seen",
        "view_events",
    ]
    assert "never exported: leads, kit_events, visitor_seen, view_events" in (
        SCRIPT.read_text()
    )
    assert _script_array("ignored_tables") == ["_cf_KV"]
    assert "sqlite_*) return 0" in SCRIPT.read_text()
