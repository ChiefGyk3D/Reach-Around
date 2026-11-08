# SPDX-License-Identifier: Apache-2.0
"""scripts/check-dump.py: a database dump must hold no address or user agent."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-dump.py"
MIGRATIONS = sorted((ROOT / "worker" / "migrations").glob("*.sql"))

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/128.0.6613.84 Safari/537.36"
)

CLEAN = (
    "CREATE TABLE view_events "
    "(id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, page TEXT, country TEXT);\n"
    "INSERT INTO view_events VALUES (1, 1792000000000, 'home', 'US');\n"
    "INSERT INTO view_meta VALUES ('net_status', 'ok 12:00:30');\n"
)


def run(dump: str, *args: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    f = tmp_path / "dump.sql"
    f.write_text(dump)
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), *args, str(f)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_clean_dump_passes(tmp_path: Path) -> None:
    r = run(CLEAN, tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("198.51.100.7", "ipv4"),
        ("2001:db8::7", "ipv6"),
        ("2001:db8:0:0:0:0:0:7", "ipv6"),
        ("::ffff:198.51.100.7", "ipv4"),
        (UA, "user agent"),
        ("curl/8.5.0", "user agent"),
        ("Googlebot/2.1", "user agent"),
        ("Windows NT 10.0", "user agent"),
    ],
)
def test_each_leak_is_caught_and_never_printed(
    value: str, kind: str, tmp_path: Path
) -> None:
    dump = CLEAN + f"INSERT INTO view_events VALUES (2, 1, 'x{value}y', 'US');\n"  # noqa: S608 - synthetic dump text
    r = run(dump, tmp_path=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert kind in out
    assert "line 4" in out
    # The report names the line and kind, never the value.
    assert value not in out
    assert "198.51.100" not in out


@pytest.mark.parametrize(
    "col", ["ip", "client_ip", "user_agent", "ua", "referrer", "remote_addr"]
)
def test_a_forbidden_column_is_caught(col: str, tmp_path: Path) -> None:
    dump = f"CREATE TABLE t (id INTEGER, {col} TEXT);\nINSERT INTO t VALUES (1, 'a');\n"
    r = run(dump, tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "column" in r.stdout + r.stderr


def test_forbid_literal_is_caught_without_printing_it(tmp_path: Path) -> None:
    dump = CLEAN + "INSERT INTO t VALUES (3, 'sentinel-abc');\n"  # noqa: S608 - synthetic dump text
    r = run(dump, "--forbid", "sentinel-abc", tmp_path=tmp_path)
    assert r.returncode == 1
    assert "sentinel-abc" not in r.stdout + r.stderr
    assert "forbidden value" in r.stdout + r.stderr


def test_a_dump_with_no_rows_fails_unless_allowed(tmp_path: Path) -> None:
    # An empty or truncated export must not read as "clean".
    schema_only = "CREATE TABLE t (id INTEGER);\n"
    assert run(schema_only, tmp_path=tmp_path).returncode == 1
    assert run(schema_only, "--allow-empty", tmp_path=tmp_path).returncode == 0
    assert run("", tmp_path=tmp_path).returncode == 1


def test_stdin_works() -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), "-"],
        input=CLEAN,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_file_is_a_usage_error(tmp_path: Path) -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), str(tmp_path / "nope.sql")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 2


def test_real_schema_has_no_forbidden_column_and_a_row_dump_is_clean(
    tmp_path: Path,
) -> None:
    # Apply the real migrations to an in-memory database, add rows shaped like
    # the Worker's, dump it and scan the dump.
    assert MIGRATIONS, "no migrations collected"
    db = sqlite3.connect(":memory:")
    for m in MIGRATIONS:
        db.executescript(m.read_text())
    db.execute(
        "INSERT INTO view_events (ts, page, src_kind, src, bot_class, reason) "
        "VALUES (1792000000000, 'home', 'u', 'bluesky', 'h', 'ok')"
    )
    db.commit()
    dump = "\n".join(db.iterdump()) + "\n"
    assert "CREATE TABLE view_events" in dump
    r = run(dump, tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    # And the guard bites on the same dump with one address added.
    db.execute("UPDATE view_events SET src = '198.51.100.7'")
    dump2 = "\n".join(db.iterdump()) + "\n"
    assert run(dump2, tmp_path=tmp_path).returncode == 1
