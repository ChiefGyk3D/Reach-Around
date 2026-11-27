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
LISTING_SQL = "SELECT name, sql FROM sqlite_master WHERE type = 'table'"

STUB = """#!{python}
import json, os, sqlite3, sys, time
args = sys.argv[1:]
def listing_q(sql):
    return sql.startswith("SELECT name, sql FROM sqlite_master")
if os.environ.get("STUB_HANG"):
    time.sleep(30)
log = os.environ.get("STUB_TRACE")
db = sqlite3.connect(os.environ["STUB_DB"])
if args[:2] == ["d1", "execute"]:
    sql = args[args.index("--command") + 1]
    if log:
        with open(log, "a") as fh:
            fh.write("execute: " + sql + "\\n")
    if os.environ.get("STUB_BAD_ANSWER") and listing_q(sql):
        print(os.environ["STUB_BAD_ANSWER"])
        sys.exit(0)
    envelope = os.environ.get("STUB_ENVELOPE")
    cur = db.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    listing = sql.startswith("SELECT name, sql FROM sqlite_master")
    if os.environ.get("STUB_PAD_LISTING") and listing:
        # Synthetic system-table names (the script accepts sqlite_*) to make the
        # listing larger than a pipe buffer.
        rows += [{{"name": f"sqlite_pad_{{i:04d}}_" + "x" * 1000}} for i in range(300)]
    if envelope and listing:
        good = {{"results": rows, "success": True}}
        answer = {{
            "failed": [{{"results": rows, "success": False}}],
            "no-success": [{{"results": rows}}],
            "two-results": [good, {{"results": [], "success": True}}],
            "bare-failed": {{"results": rows, "success": False}},
            "bad-name": [{{"results": rows + [{{"name": 5}}], "success": True}}],
        }}[envelope]
        print(json.dumps(answer))
        sys.exit(0)
    count_mode = os.environ.get("STUB_COUNT_MODE")
    if count_mode and sql.startswith("SELECT '"):
        if count_mode == "duplicate":
            rows = rows + [{{"name": rows[0]["name"], "n": 1000000}}]
        elif count_mode == "extra":
            rows = rows + [{{"name": "leads", "n": 1}}]
        elif count_mode == "missing":
            rows = rows[1:]
    print(json.dumps([{{"results": rows, "success": True}}]))
elif args[:2] == ["d1", "export"]:
    table = [a for a in args if a.startswith("--table=")][0].split("=", 1)[1]
    out = [a for a in args if a.startswith("--output=")][0].split("=", 1)[1]
    if log:
        with open(log, "a") as fh:
            fh.write("export: " + table + "\\n")
    if os.environ.get("STUB_FAIL") == table:
        sys.exit(1)
    schema = db.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()[0]
    inserts = [l for l in db.iterdump() if l.startswith(f'INSERT INTO "{{table}}"')]
    if os.environ.get("STUB_TRUNCATE") == table:
        inserts = inserts[:-1]
    if os.environ.get("STUB_DUP_ROW") == table:
        inserts = inserts + inserts[-1:]
    with open(out, "w") as fh:
        fh.write(schema + ";\\n" + "\\n".join(inserts) + "\\n")
    if os.environ.get("STUB_REPLACE") == table:
        # The table is replaced after its export (a race with the listing).
        db.execute(f'ALTER TABLE "{{table}}" ADD COLUMN raced TEXT')
        db.commit()
    if os.environ.get("STUB_APPEND"):
        with open(out, "a") as fh:
            fh.write(os.environ["STUB_APPEND"] + "\\n")
    if os.environ.get("STUB_BIG_LINE"):
        with open(out, "a") as fh:
            fh.write("-- " + "x" * 2000000 + "\\n")
    if os.environ.get("STUB_DELETE_AFTER") == table:
        db.execute(f'DELETE FROM "{{table}}"')
        db.commit()
    if os.environ.get("STUB_GROW") == table:
        db.execute(
            "INSERT INTO view_hour_daily (site, day, hour, bot_class, views,"
            " clicks) VALUES ('links', '2026-11-02', 3, 'h', 1, 0)"
        )
        db.commit()
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


def test_the_dry_run_names_the_database_and_says_leads_are_not_exported(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    dry = run(env, "reach-db", str(d))
    assert dry.returncode == 0, dry.stderr
    assert "database: reach-db" in dry.stdout
    assert "leads database is never exported" in dry.stdout


def test_the_finished_run_names_the_database_it_exported(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    done = run(env, "reach-db", str(d), "--apply")
    assert done.returncode == 0, done.stderr
    assert "analytics database reach-db" in done.stdout


@pytest.mark.parametrize("name", ["reach-around-leads", "LEADS_DB", "my.Leads.db"])
def test_an_analytics_database_with_leads_in_its_name_is_still_exported(
    tmp_path: Path, name: str
) -> None:
    # Round 6: a name is not an identity. The database answers for itself, so a
    # valid analytics database is never refused for what it is called.
    env = make(tmp_path)
    d = dest(tmp_path)
    dry = run(env, name, str(d))
    assert dry.returncode == 0, dry.stderr
    done = run(env, name, str(d), "--apply")
    assert done.returncode == 0, done.stderr
    assert f"analytics database {name}" in done.stdout


@pytest.mark.parametrize("name", ["reach-around-leads", "LEADS_DB", "my.Leads.db"])
def test_a_leads_identity_is_refused_whatever_the_name_says(
    tmp_path: Path, name: str
) -> None:
    env = make(tmp_path)
    set_identity(env, "leads")
    d = dest(tmp_path)
    r = run(env, name, str(d), "--apply")
    assert r.returncode == 3, r.stdout
    assert "did not identify itself as the analytics database" in r.stderr
    assert list(d.iterdir()) == []


def set_identity(env: dict[str, str], role: str | None) -> None:
    """Make the stub database say what it is (None: the table is not there)."""
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        if role is None:
            db.execute("DROP TABLE db_identity")
        else:
            db.execute("UPDATE db_identity SET role = ?", (role,))
        db.commit()


@pytest.mark.parametrize(
    "name", ["contacts", "reach-db", "0123abcd-4567-89ab-cdef-0123456789ab"]
)
def test_a_leads_database_is_refused_by_what_it_is_not_by_what_it_is_called(
    tmp_path: Path, name: str
) -> None:
    # Issue #772 review: the refusal was a substring check on the name. The
    # database now answers for itself.
    env = make(tmp_path)
    set_identity(env, "leads")
    d = dest(tmp_path)
    r = run(env, name, str(d), "--apply")
    assert r.returncode == 3, r.stdout
    assert "did not identify itself as the analytics database" in r.stderr
    assert "leads" in r.stderr  # what it answered, never a row
    assert list(d.iterdir()) == []


@pytest.mark.parametrize(
    ("rows", "klass"),
    [
        (["SECRET-example.test"], "unexpected"),
        (["line one\nSECRET-two"], "unexpected"),
        (["analytics", "SECRET-other"], "multiple"),
        ([], "none"),
    ],
)
def test_the_refusal_never_echoes_what_the_database_returned(
    tmp_path: Path, rows: list[str], klass: str
) -> None:
    # Round 2: the role text came from the database and was printed back.
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DROP TABLE db_identity")
        db.execute("CREATE TABLE db_identity (role TEXT)")
        db.executemany(
            "INSERT INTO db_identity (role) VALUES (?)", [(x,) for x in rows]
        )
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 3, r.stdout
    assert "SECRET" not in r.stderr + r.stdout
    assert f"class: {klass}" in r.stderr
    assert list(d.iterdir()) == []


def test_a_database_that_cannot_say_what_it_is_is_refused(tmp_path: Path) -> None:
    env = make(tmp_path)
    set_identity(env, None)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    # A step failed (like any unreadable database), and nothing is written.
    assert r.returncode == 1, r.stdout
    assert "could not read the database identity" in r.stderr
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
    assert "missing" not in r.stderr
    (out,) = list(d.iterdir())
    assert (out / "view_daily.sql").exists()
    assert (out / "click_events.sql").exists()


def test_migration_history_is_part_of_the_backup(tmp_path: Path) -> None:
    env = make(tmp_path)
    d = dest(tmp_path)
    assert run(env, "reach-db", str(d), "--apply").returncode == 0
    (out,) = list(d.iterdir())
    assert "0001_click_events.sql" in (out / "d1_migrations.sql").read_text()


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


def _sh_is_bash() -> bool:
    """True when /bin/sh is itself bash (then a forged version cannot show the
    difference: bash resets the variable at startup)."""
    r = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/sh", "-c", "echo ${BASH_VERSION:-no}"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        timeout=30,
        check=False,
    )
    return r.stdout.strip() != "no"


def test_the_script_refuses_a_bash_older_than_four() -> None:
    text = SCRIPT.read_text()
    assert "bash 4.0" in text
    assert "bash 4.0" in (ROOT / "docs" / "backup-restore.md").read_text()
    guard = _guard_block()
    code = [line for line in guard.splitlines() if not line.startswith("#")]
    # POSIX syntax only: no [[ ]]; an array only inside the one eval string that
    # a non-bash shell fails in a subshell.
    assert not any("[[" in line for line in code)
    for line in code:
        if "eval" not in line:
            assert not re.search(r"\$\{[A-Za-z_]+\[", line), line
    # The version is read from the shell's own read-only array, never from the
    # BASH_VERSION variable that an environment can set.
    assert "BASH_VERSINFO" in guard
    assert not any("BASH_VERSION" in line for line in code)


@pytest.mark.parametrize("version", ["3.2.57(1)-release", "", "5.2.0(1)-release"])
def test_the_guard_does_not_trust_a_forged_bash_version(version: str) -> None:
    """The guard block, run by a plain POSIX shell with a forged BASH_VERSION
    (an environment can set it): a shell that is not bash exits 2 with the bash
    message whatever the variable says. Residual: a real bash 3.2 is not
    available here."""
    if _sh_is_bash():
        pytest.skip("/bin/sh is bash here")
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/bin/sh", "-c", _guard_block() + "\nexit 0\n"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "BASH_VERSION": version},
        timeout=30,
        check=False,
    )
    assert r.returncode == 2, r.stderr
    assert "bash 4.0 or newer is required" in r.stderr


@pytest.mark.parametrize("version", ["3.2.57(1)-release", ""])
def test_the_guard_passes_a_real_bash_whatever_the_variable_says(version: str) -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/usr/bin/bash", "-c", _guard_block() + "\nexit 0\n"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "BASH_VERSION": version},
        timeout=30,
        check=False,
    )
    assert r.returncode == 0, r.stderr


def test_running_the_script_under_sh_with_a_forged_version_is_refused() -> None:
    """`BASH_VERSION=5.2 sh scripts/backup-d1.sh`: a non-bash sh gets the bash
    diagnostic (exit 2), not a shell syntax error from bash-only syntax."""
    if _sh_is_bash():
        pytest.skip("/bin/sh is bash here")
    r = subprocess.run(  # noqa: S603
        ["/bin/sh", str(SCRIPT)],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "BASH_VERSION": "5.2.0(1)-release"},
        timeout=30,
        check=False,
    )
    assert r.returncode == 2, r.stderr
    assert "bash 4.0 or newer is required" in r.stderr
    assert "yntax error" not in r.stderr


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


def _log(env: dict[str, str], tmp_path: Path) -> Path:
    log = tmp_path / "stub.log"
    env["STUB_TRACE"] = str(log)
    return log


def _older_history(env: dict[str, str]) -> None:
    """An older migration record: 0013 and everything after it is not recorded."""
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DELETE FROM d1_migrations WHERE name >= '0013'")
        db.commit()


@pytest.mark.parametrize("history", ["intact", "older"])
@pytest.mark.parametrize(
    "table",
    [
        "alert_quota",
        "alert_dedup",
        "analytics_coverage",
        "view_hour_daily",
        "view_city_daily",
        "view_daily",
        "click_events",
        "click_landing_state",
        "kit_meta",
        "d1_migrations",
    ],
)
def test_a_database_missing_any_inventory_table_fails_before_any_export(
    tmp_path: Path, table: str, history: str
) -> None:
    """Every inventory table is always required, whatever the migration record in
    the same database says (it can be damaged or edited): the run fails with exit
    1, names the table, and exports nothing."""
    env = make(tmp_path)
    log = _log(env, tmp_path)
    if history == "older" and table != "d1_migrations":
        _older_history(env)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute(f"DROP TABLE {table}")  # noqa: S608 - fixed names
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "required table(s) missing" in r.stderr
    assert table in r.stderr
    assert "apply the migrations first" in r.stderr
    assert "done:" not in r.stdout
    assert "skipped" not in r.stdout
    assert list(d.iterdir()) == []
    assert "export:" not in log.read_text()


def test_a_truncated_migration_history_does_not_excuse_a_missing_table(
    tmp_path: Path,
) -> None:
    """The record is cut from its end and the matching table is dropped: the
    remaining record is a clean prefix, which must not make the table optional."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DELETE FROM d1_migrations WHERE name >= '0019_view_city_daily.sql'")
        db.execute("DROP TABLE view_city_daily")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "view_city_daily" in r.stderr
    assert "done:" not in r.stdout
    assert list(d.iterdir()) == []


def test_an_older_schema_fails_with_a_clear_message(tmp_path: Path) -> None:
    """A database that never applied migration 0013 (no alert tables, record cut
    before it): not a partial success, a failure that says what to do."""
    env = make(tmp_path)
    _older_history(env)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DROP TABLE alert_quota")
        db.execute("DROP TABLE alert_dedup")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "alert_quota" in r.stderr
    assert "alert_dedup" in r.stderr
    assert "older schema" in r.stderr
    assert "apply the migrations first" in r.stderr
    assert "done:" not in r.stdout
    assert list(d.iterdir()) == []


def test_the_full_inventory_passes_whatever_the_migration_record_holds(
    tmp_path: Path,
) -> None:
    """The record plays no part: every table exists, so the run succeeds with an
    empty, cut or odd record."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute("DELETE FROM d1_migrations")
        db.execute("INSERT INTO d1_migrations (name) VALUES ('')")
        db.execute("INSERT INTO d1_migrations (name) VALUES ('unknown_file.sql')")
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr
    (out,) = list(d.iterdir())
    for table in _script_array("tables"):
        assert (out / f"{table}.sql").exists(), table


def test_the_run_makes_only_listing_and_count_queries(tmp_path: Path) -> None:
    """Read-only queries only (the listing twice, the row counts twice) plus one
    export per table: the migration history is never read."""
    env = make(tmp_path)
    log = _log(env, tmp_path)
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 0, r.stderr
    lines = log.read_text().splitlines()
    queries = [ln for ln in lines if ln.startswith("execute:")]
    # The first query asks the database who it is (#772): only 'analytics' is exported.
    assert queries.pop(0) == "execute: SELECT role FROM db_identity ORDER BY role"
    assert len(queries) == 4
    assert queries[0] == queries[3] == f"execute: {LISTING_SQL}"
    assert queries[1] == queries[2]
    assert queries[1].startswith("execute: SELECT '")
    assert all(re.match(r"execute: (SELECT )", q) for q in queries)
    assert not any(w in " ".join(queries) for w in ("INSERT", "UPDATE", "DELETE"))
    # Order: listing, counts, every export, counts, listing.
    kinds = [ln.split(":")[0] for ln in lines]
    first, last = kinds.index("export"), len(kinds) - 1 - kinds[::-1].index("export")
    # identity, listing, counts
    assert kinds[:first] == ["execute", "execute", "execute"]
    assert kinds[last + 1 :] == ["execute", "execute"]
    exports = [ln for ln in lines if ln.startswith("export:")]
    assert len(exports) == len(_script_array("tables"))


def test_a_long_missing_list_is_bounded(tmp_path: Path) -> None:
    env = make(tmp_path)
    inventory = _script_array("tables")
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        for table in inventory[:12]:
            db.execute(f"DROP TABLE {table}")  # noqa: S608 - fixed names
        db.commit()
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 1
    assert inventory[9] in r.stderr
    assert inventory[10] not in r.stderr
    assert "... and 2 more" in r.stderr
    assert len(r.stderr) < 2000


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


def test_a_table_name_with_a_carriage_return_is_refused(tmp_path: Path) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute('CREATE TABLE "zz\ryy" (a)')
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "invalid table name" in r.stderr
    assert list(d.iterdir()) == []


def _migration_tables(tmp_path: Path) -> set[str]:
    """Every table the migrations create, run in order."""
    names: set[str] = set()
    with closing(sqlite3.connect(tmp_path / "m.sqlite")) as db:
        for f in sorted((ROOT / "worker" / "migrations").glob("*.sql")):
            db.executescript(schema_sql(f.read_text()))
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ):
            if not name.startswith("sqlite_"):
                names.add(name)
    return names


def test_the_inventory_matches_the_migrations(tmp_path: Path) -> None:
    """Every table the migrations create is in the script's `tables` or in its
    `never_exported`, and nothing else is (wrangler's own d1_migrations table
    comes from wrangler, not from a migration). A new migration table fails here
    until the inventory is updated."""
    derived = _migration_tables(tmp_path)
    derived.add("d1_migrations")
    never = {"leads", "kit_events", "visitor_seen", "view_events"}
    tables = set(_script_array("tables"))
    assert len(tables) == len(_script_array("tables")), "a table is listed twice"
    assert not (tables & never)
    assert derived == tables | never
    # The two aggregate views are exported on purpose (#932, owner decision).
    assert {"view_hour_daily", "view_city_daily"} <= tables


def test_a_populated_table_outside_the_inventory_fails_the_run(
    tmp_path: Path,
) -> None:
    """An existing table the script does not know would be silently left out of
    the backup: the run fails before any export and names it (not its rows)."""
    env = make(tmp_path)
    log = _log(env, tmp_path)
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
    assert "export:" not in log.read_text()


def test_a_long_unexpected_table_list_is_bounded(tmp_path: Path) -> None:
    """A hostile or damaged database with hundreds of unknown tables cannot flood
    the diagnostic: ten names, then the count of the rest."""
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        for i in range(500):
            db.execute(f'CREATE TABLE "unk_{i:04d}" (a)')
        db.commit()
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 1
    assert "unk_0000" in r.stderr
    assert "unk_0009" in r.stderr
    assert "unk_0010" not in r.stderr
    assert "... and 490 more" in r.stderr
    assert len(r.stderr) < 2000
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


def _assert_clean(text: str) -> None:
    for ch in text:
        assert ch == "\n" or (0x20 <= ord(ch) < 0x7F), repr(ch)


HOSTILE = "ev\x1b[2Jil\x07\x7f\x9b\tz"


def test_database_names_are_sanitized_before_they_are_printed(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute(f'CREATE TABLE "t{HOSTILE}" (a)')
        db.commit()
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 1
    assert "tev??2Jil?????z" in r.stderr
    _assert_clean(r.stderr + r.stdout)


def test_a_very_long_database_name_is_truncated(tmp_path: Path) -> None:
    env = make(tmp_path)
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        db.execute(f'CREATE TABLE "{"a" * 200}" (a)')
        db.commit()
    r = run(env, "reach-db", str(dest(tmp_path)), "--apply")
    assert r.returncode == 1
    assert "a" * 80 + "..." in r.stderr
    assert "a" * 81 not in r.stderr


def test_the_lists_in_the_script_match_the_repository_and_each_other() -> None:
    text = SCRIPT.read_text()
    assert _script_array("never_exported") == [
        "leads",
        "kit_events",
        "visitor_seen",
        "view_events",
    ]
    assert "never exported: leads, kit_events, visitor_seen, view_events" in text
    assert _script_array("ignored_tables") == ["_cf_KV"]
    assert "sqlite_*) return 0" in text
    # No migration-record logic: nothing the database says may excuse a table.
    for gone in ("introduced_by", "trust_record", "optional_tables", "is_applied"):
        assert gone not in text, gone
    assert "SELECT name FROM d1_migrations" not in text


def _fails_before_keeping(r: subprocess.CompletedProcess[str], d: Path) -> None:
    assert r.returncode == 1, r.stdout + r.stderr
    assert "done:" not in r.stdout
    assert list(d.iterdir()) == []


def test_a_truncated_export_is_caught(tmp_path: Path) -> None:
    """An export that succeeds but holds fewer rows than the database is not a
    backup: the run fails, removes the partial export and names the table."""
    env = make(tmp_path)
    env["STUB_TRUNCATE"] = "view_daily"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "view_daily" in r.stderr
    assert "does not match" in r.stderr


def test_a_table_replaced_during_the_run_fails(tmp_path: Path) -> None:
    """A table changed between the first listing and the end of the run (same
    name, different definition) is detected by the second listing."""
    env = make(tmp_path)
    env["STUB_REPLACE"] = "view_meta"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "changed while the export ran" in r.stderr


def test_a_table_may_grow_during_the_run(tmp_path: Path) -> None:
    """Visits and clicks keep arriving: a row added while the exports run is
    allowed (the after count is higher, the export holds the earlier rows)."""
    env = make(tmp_path)
    env["STUB_GROW"] = "view_hour_daily"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stderr


def test_a_table_that_loses_rows_during_the_run_fails(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_DELETE_AFTER"] = "view_daily"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "rows decreased" in r.stderr
    assert "view_daily" in r.stderr


def test_a_truncated_export_with_a_matching_delete_still_fails(
    tmp_path: Path,
) -> None:
    """The review's case: the export lost a row and a live delete made the after
    count match it. The count went down, so it fails."""
    env = make(tmp_path)
    env["STUB_TRUNCATE"] = "view_daily"
    env["STUB_DELETE_AFTER"] = "view_daily"
    d = dest(tmp_path)
    _fails_before_keeping(run(env, "reach-db", str(d), "--apply"), d)


def test_an_export_with_more_rows_than_the_database_ever_held_fails(
    tmp_path: Path,
) -> None:
    env = make(tmp_path)
    env["STUB_DUP_ROW"] = "view_daily"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "does not match" in r.stderr


def test_exported_sql_is_never_executed(tmp_path: Path) -> None:
    """A file that holds ATTACH (or anything else) is never run. The run itself
    also fails (the line is outside the export grammar) and removes the export."""
    env = make(tmp_path)
    marker = tmp_path / "probe.db"
    env["STUB_APPEND"] = f"ATTACH DATABASE '{marker}' AS probe;"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert not marker.exists()
    assert "sqlite3" not in SCRIPT.read_text()
    assert r.returncode == 1, r.stdout + r.stderr
    assert "done:" not in r.stdout
    assert "partial export was removed" in r.stderr
    assert list(d.iterdir()) == []


# A python3 stand-in for the verifier and the dump check. It runs the real
# program, but first makes the sqlite3 module unimportable and every way of
# starting another process raise (leaving a note), so a script that runs the
# dump through Python sqlite3, a subprocess, a shell or an exec goes red.
PY_GUARD = """#!{python}
import os, runpy, sys
LOG = {log!r}
sys.modules["sqlite3"] = None
sys.modules["_sqlite3"] = None
def boom(*a, **k):
    with open(LOG, "a") as fh:
        fh.write("process start attempted\\n")
    raise SystemExit(97)
import subprocess
subprocess.Popen.__init__ = boom
for name in ("system", "execv", "execve", "execvp", "execvpe", "posix_spawn",
             "posix_spawnp", "spawnv", "spawnve", "spawnvp", "spawnvpe"):
    if hasattr(os, name):
        setattr(os, name, boom)
args = [a for a in sys.argv[1:] if a != "-I"]
if args[0] == "-":
    sys.argv = ["-"] + args[1:]
    exec(compile(sys.stdin.read(), "<stdin>", "exec"), {{"__name__": "__main__"}})
else:
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
"""


def test_a_passing_export_is_read_never_executed(tmp_path: Path) -> None:
    """Isolates the property. The export passes verification and holds a plain
    statement (a CREATE INDEX, which the dump check lets through) that would
    leave a mark in the database if anything ran it. Every route to running it
    is closed or watched: no sqlite3 or sqlite program on PATH, no Python
    sqlite3 module and no process started by the verifier or the dump check, and
    wrangler is only asked to export and to run listing and count queries."""
    env = make(tmp_path)
    spy_dir = tmp_path / "spy"
    spy_dir.mkdir()
    notes = tmp_path / "spy.log"
    for name in ("sqlite3", "sqlite"):
        spy = spy_dir / name
        spy.write_text(f'#!/usr/bin/bash\necho "{name}" >> "{notes}"\nexit 1\n')
        spy.chmod(0o755)
    py = spy_dir / "python3"
    py.write_text(PY_GUARD.format(python=sys.executable, log=str(notes)))
    py.chmod(0o755)
    env["PATH"] = f"{spy_dir}:{env['PATH']}"
    trace = _log(env, tmp_path)
    env["STUB_APPEND"] = "CREATE INDEX probe_idx ON view_daily (views);"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    kept = [f for f in d.rglob("*.sql") if "probe_idx" in f.read_text()]
    assert kept, "the statement must be in the kept backup, or the test proves nothing"
    assert not notes.exists(), notes.read_text()
    with closing(sqlite3.connect(env["STUB_DB"])) as db:
        marks = db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'probe_idx'"
        ).fetchall()
    assert marks == []
    for line in trace.read_text().splitlines():
        assert line.startswith(("export: ", "execute: SELECT ")), line


@pytest.mark.parametrize("mode", ["duplicate", "extra", "missing"])
def test_a_count_answer_that_is_not_the_exact_table_set_is_not_trusted(
    tmp_path: Path, mode: str
) -> None:
    """A duplicate row (last one would win), a table outside the inventory or a
    missing table: the run fails at the counts, before any export."""
    env = make(tmp_path)
    env["STUB_COUNT_MODE"] = mode
    log = _log(env, tmp_path)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "could not count the rows" in r.stderr
    assert "export:" not in log.read_text()


@pytest.mark.parametrize(
    "line",
    [
        'WITH x(v) AS (VALUES(1))\nINSERT INTO "view_daily" ("views") SELECT v FROM x;',
        'WITH x(v) AS (SELECT 1) INSERT INTO "view_daily" ("views") SELECT v FROM x;',
        'INSERT OR IGNORE INTO "view_daily" ("views") VALUES(1);',
        'SELECT 1; INSERT INTO "view_daily" ("views") VALUES(1);',
    ],
)
def test_the_shell_runs_the_verifier_and_a_cte_insert_fails_the_whole_run(
    tmp_path: Path, line: str
) -> None:
    """End to end: the backup run itself fails, with the verifier's verdict, and
    leaves nothing behind."""
    env = make(tmp_path)
    env["STUB_APPEND"] = line
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "cannot be counted" in r.stderr
    assert "could not be verified" in r.stderr


def test_an_oversized_line_or_file_fails_closed(tmp_path: Path) -> None:
    env = make(tmp_path)
    env["STUB_BIG_LINE"] = "1"
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "too large" in r.stderr
    env.pop("STUB_BIG_LINE")
    env["BACKUP_MAX_BYTES"] = "100"
    d2 = dest_second(tmp_path)
    r2 = run(env, "reach-db", str(d2), "--apply")
    assert r2.returncode == 1
    assert "too large" in r2.stderr


@pytest.mark.parametrize(
    "line",
    [
        'INSERT INTO "view_daily" VALUES(1);\nINSERT INTO "view_daily" VALUES(2',
        "INSERT INTO view_daily VALUES(1)",
    ],
)
def test_an_insert_shape_it_cannot_count_fails_closed(
    tmp_path: Path, line: str
) -> None:
    env = make(tmp_path)
    env["STUB_APPEND"] = line
    d = dest(tmp_path)
    _fails_before_keeping(run(env, "reach-db", str(d), "--apply"), d)


@pytest.mark.parametrize(
    "mode", ["failed", "no-success", "two-results", "bare-failed", "bad-name"]
)
def test_an_invalid_envelope_around_a_complete_listing_is_not_trusted(
    tmp_path: Path, mode: str
) -> None:
    """Each answer holds every required table: only the envelope is wrong. The
    run must fail at the listing, before any count query or export."""
    env = make(tmp_path)
    env["STUB_ENVELOPE"] = mode
    log = _log(env, tmp_path)
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "unexpected answer" in r.stderr
    lines = log.read_text().splitlines()
    assert lines == [
        "execute: SELECT role FROM db_identity ORDER BY role",
        f"execute: {LISTING_SQL}",
    ]


@pytest.mark.parametrize("answer", ["not json", "[]", '{"error": "boom"}'])
def test_a_listing_that_is_not_json_results_is_not_trusted(
    tmp_path: Path, answer: str
) -> None:
    env = make(tmp_path)
    env["STUB_BAD_ANSWER"] = answer
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    _fails_before_keeping(r, d)
    assert "unexpected answer" in r.stderr


def test_the_ignored_system_table_is_not_created_by_any_migration(
    tmp_path: Path,
) -> None:
    assert not any(t.lower() == "_cf_kv" for t in _migration_tables(tmp_path))
    assert "Cloudflare's own internal table" in SCRIPT.read_text()
