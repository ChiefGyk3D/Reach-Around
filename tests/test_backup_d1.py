# SPDX-License-Identifier: Apache-2.0
"""scripts/backup-d1.sh: the owner-run export of the analytics tables.

A stub stands in for ``wrangler`` and answers from a local SQLite file built
from the Worker's own migrations. Everything is synthetic."""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

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
    cur = db.execute(sql)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
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
    db = sqlite3.connect(db_path)
    for f in sorted((ROOT / "worker" / "migrations").glob("*.sql")):
        db.executescript(f.read_text())
    db.execute(
        "INSERT INTO view_daily (site, day, day_tz, page, src_kind, src, country,"
        " bot_class, views, uniques) VALUES ('links', '2026-11-01', '2026-11-01',"
        " 'home', 'd', '', 'US', 'h', 9, 0)"
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
    for table in ("view_daily", "click_events", "counters", "view_meta"):
        query = f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed names
        assert fresh.execute(query).fetchone() == source.execute(query).fetchone()
    names = {r[0] for r in fresh.execute("SELECT name FROM sqlite_master")}
    assert "leads" not in names
    assert "kit_events" not in names
