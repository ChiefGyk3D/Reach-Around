# SPDX-License-Identifier: Apache-2.0
"""scripts/check-dump.py: a database dump must hold no address or user agent.

The checker is schema-aware: it parses CREATE TABLE and INSERT statements,
refuses any table or column outside scripts/dump-schema.json, and decodes the
supported encodings before it looks for an address.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-dump.py"
SCHEMA = ROOT / "scripts" / "dump-schema.json"
MIGRATIONS = sorted((ROOT / "worker" / "migrations").glob("*.sql"))

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/128.0.6613.84 Safari/537.36"
)


def real_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for m in MIGRATIONS:
        db.executescript(m.read_text())
    return db


def schema_dump() -> str:
    return "\n".join(real_db().iterdump()) + "\n"


SCHEMA_DUMP = schema_dump()
SCHEMA_LINES = SCHEMA_DUMP.count("\n")
GOOD_ROW = (
    "INSERT INTO view_events (ts, page, src_kind, src, bot_class, reason) "
    "VALUES (1792000000000, 'home', 'u', 'bluesky', 'h', 'ok');\n"
)


def row(src: str, col: str = "src") -> str:
    cols = {
        "ts": "1792000000000",
        "page": "'home'",
        "src_kind": "'u'",
        "src": "'bluesky'",
        "bot_class": "'h'",
        "reason": "'ok'",
    }
    cols[col] = src
    return (
        f"INSERT INTO view_events ({', '.join(cols)}) "  # noqa: S608 - synthetic
        f"VALUES ({', '.join(cols.values())});\n"
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


def test_real_schema_with_a_normal_row_passes(tmp_path: Path) -> None:
    r = run(SCHEMA_DUMP + GOOD_ROW, tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_allowlist_equals_the_real_migrations() -> None:
    # A new migration that adds a table or column must update the allowlist on
    # purpose, with review; this test is what forces that.
    assert MIGRATIONS, "no migrations collected"
    db = real_db()
    actual = {
        name: [c[1] for c in db.execute(f"PRAGMA table_info('{name}')")]  # noqa: S608
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    allow = json.loads(SCHEMA.read_text())["tables"]
    for name, cols in actual.items():
        assert allow.get(name) == cols, name
    # Only Cloudflare's own bookkeeping tables are allowed beyond the schema.
    assert set(allow) - set(actual) <= {"d1_migrations", "_cf_KV"}


# The reviewer's bypass examples, plus the encodings the checker now decodes.
ADDRESS_FORMS = [
    ("198.51.100.7", "ipv4"),
    ("0xC6336407", "ipv4"),
    ("0xc6336407", "ipv4"),
    ("3325256711", "ipv4"),
    ("198%2E51%2E100%2E7", "ipv4"),
    ("198%252E51%252E100%252E7", "ipv4"),
    ("::ffff:198.51.100.7", "ipv4"),
    ("2001:db8::7", "ipv6"),
    ("2001:db8:0:0:0:0:0:7", "ipv6"),
    ("20010db8000000000000000000000007", "ipv6"),
    ("20010DB8000000000000000000000007", "ipv6"),
    ("2001%3Adb8%3A%3A7", "ipv6"),
]


@pytest.mark.parametrize(("value", "kind"), ADDRESS_FORMS)
def test_address_forms_are_caught_and_never_printed(
    value: str, kind: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{value}'"), tmp_path=tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert kind in out
    assert f"line {SCHEMA_LINES + 1}" in out
    assert value not in out
    assert "198.51.100" not in out


def test_split_concatenation_is_joined_before_the_scan(tmp_path: Path) -> None:
    for expr in ("'198.'||'51.100.7'", "'198.51.'||'100.'||'7'", "'0xC633' || '6407'"):
        r = run(SCHEMA_DUMP + row(expr), tmp_path=tmp_path)
        assert r.returncode == 1, expr + r.stdout
        assert "ipv4" in r.stdout


@pytest.mark.parametrize(
    "literal", ["X'C6336407'", "x'c6336407'", "X'20010DB8000000000000000000000007'"]
)
def test_any_blob_is_refused(literal: str, tmp_path: Path) -> None:
    r = run(SCHEMA_DUMP + row(literal), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "blob" in r.stdout


def test_a_blob_holding_text_is_decoded_and_scanned(tmp_path: Path) -> None:
    hexed = b"198.51.100.7".hex()
    r = run(SCHEMA_DUMP + row(f"X'{hexed}'"), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "ipv4" in r.stdout


@pytest.mark.parametrize(
    "expr", ["char(49,57,56)", "replace('x','x','198.51.100.7')", "(SELECT 1)", "?"]
)
def test_an_expression_instead_of_a_literal_is_refused(
    expr: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(expr), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "unsupported" in r.stdout


@pytest.mark.parametrize(
    "value",
    [UA, "curl/8", "Googlebot/2.1", "Windows NT 10.0", "x (y) z", "a;b"],
)
def test_user_agent_shapes_are_caught(value: str, tmp_path: Path) -> None:
    r = run(SCHEMA_DUMP + row(f"'{value}'"), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout
    assert value not in r.stdout + r.stderr


def test_a_user_agent_with_no_marker_cannot_hide_in_an_enum_column(
    tmp_path: Path,
) -> None:
    # "ChiefBrowser" matches no UA pattern; the browser column only takes the
    # Worker's closed list.
    r = run(SCHEMA_DUMP + row("'ChiefBrowser'", "browser"), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "ChiefBrowser" not in r.stdout + r.stderr
    ok = run(SCHEMA_DUMP + row("'chrome'", "browser"), tmp_path=tmp_path)
    assert ok.returncode == 0, ok.stdout


@pytest.mark.parametrize(
    "col", ["peer_ip", "client_address", "http_user_agent", "request_headers", "ip"]
)
@pytest.mark.parametrize("quote", ["{}", '"{}"', "`{}`", "[{}]"])
def test_a_renamed_column_is_refused(col: str, quote: str, tmp_path: Path) -> None:
    dump = SCHEMA_DUMP.replace(
        "country TEXT,\n  site", "country TEXT,\n  site"
    ).replace(
        "CREATE TABLE view_meta (",
        f"CREATE TABLE view_meta ({quote.format(col)} TEXT, ",
        1,
    )
    assert col in dump
    r = run(dump + GOOD_ROW, tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout
    assert "unexpected column" in r.stdout
    assert col not in r.stdout + r.stderr or col.isidentifier()


def test_a_column_added_to_an_existing_table_is_refused(tmp_path: Path) -> None:
    dump = SCHEMA_DUMP.replace(
        "CREATE TABLE view_meta (", "CREATE TABLE view_meta (peer_ip TEXT, ", 1
    )
    r = run(dump, tmp_path=tmp_path)
    assert r.returncode == 1
    assert "unexpected column" in r.stdout


def test_an_unexpected_table_is_refused(tmp_path: Path) -> None:
    dump = SCHEMA_DUMP + "CREATE TABLE request_log (id INTEGER, note TEXT);\n"
    r = run(dump + GOOD_ROW, tmp_path=tmp_path)
    assert r.returncode == 1
    assert "unexpected table" in r.stdout


def test_a_table_created_by_select_a_view_and_a_trigger_are_refused(
    tmp_path: Path,
) -> None:
    for stmt in (
        "CREATE TABLE copy AS SELECT * FROM view_events;",
        "CREATE VIEW v AS SELECT * FROM view_events;",
        "CREATE TRIGGER t AFTER INSERT ON view_events BEGIN SELECT 1; END;",
    ):
        r = run(SCHEMA_DUMP + stmt + "\n" + GOOD_ROW, tmp_path=tmp_path)
        assert r.returncode == 1, stmt + r.stdout
        assert "unexpected" in r.stdout


def test_an_insert_into_an_unexpected_column_or_table_is_refused(
    tmp_path: Path,
) -> None:
    bad_col = "INSERT INTO view_events (ts, peer_ip) VALUES (1, 'x');\n"
    bad_tab = "INSERT INTO request_log VALUES (1, 'x');\n"
    for stmt in (bad_col, bad_tab):
        r = run(SCHEMA_DUMP + stmt, tmp_path=tmp_path)
        assert r.returncode == 1, stmt
        assert "unexpected" in r.stdout


def test_positional_insert_is_mapped_and_counted(tmp_path: Path) -> None:
    ok = "INSERT INTO view_meta VALUES ('k', 'v');\n"
    assert run(SCHEMA_DUMP + ok, tmp_path=tmp_path).returncode == 0
    short = "INSERT INTO view_meta VALUES ('k');\n"
    long = "INSERT INTO view_meta VALUES ('k', 'v', 'w');\n"
    for stmt in (short, long):
        r = run(SCHEMA_DUMP + stmt, tmp_path=tmp_path)
        assert r.returncode == 1
        assert "value count" in r.stdout


def test_hex_ids_are_allowed_only_in_their_own_columns(tmp_path: Path) -> None:
    h = "0123456789abcdef0123456789abcdef"
    ok = f"INSERT INTO visitor_seen VALUES ('2027-03-14', '{h}');\n"  # noqa: S608 - synthetic dump text
    assert run(SCHEMA_DUMP + ok, tmp_path=tmp_path).returncode == 0
    bad = row(f"'{h}'")
    r = run(SCHEMA_DUMP + bad, tmp_path=tmp_path)
    assert r.returncode == 1
    assert "ipv6" in r.stdout


def test_forbid_literal_is_caught_decoded_and_never_printed(tmp_path: Path) -> None:
    r = run(
        SCHEMA_DUMP + row("'sentinel-abc'"),
        "--forbid",
        "sentinel-abc",
        tmp_path=tmp_path,
    )
    assert r.returncode == 1
    assert "sentinel-abc" not in r.stdout + r.stderr
    assert "forbidden value" in r.stdout
    enc = run(
        SCHEMA_DUMP + row("'sentinel%2Dabc'"),
        "--forbid",
        "sentinel-abc",
        tmp_path=tmp_path,
    )
    assert enc.returncode == 1


def test_a_dump_with_no_rows_fails_unless_allowed(tmp_path: Path) -> None:
    assert run(SCHEMA_DUMP, tmp_path=tmp_path).returncode == 1
    assert run(SCHEMA_DUMP, "--allow-empty", tmp_path=tmp_path).returncode == 0
    assert run("", tmp_path=tmp_path).returncode == 1


def test_quotes_comments_and_semicolons_in_values_do_not_confuse_the_parser(
    tmp_path: Path,
) -> None:
    tricky = "INSERT INTO view_meta VALUES ('a''b', 'c;d -- e');\n"
    r = run(SCHEMA_DUMP + tricky, tmp_path=tmp_path)
    # The value has a semicolon, which is an unexpected character: caught as a
    # value finding on the right line, not a parser crash.
    assert r.returncode == 1
    assert "Traceback" not in r.stderr
    assert f"line {SCHEMA_LINES + 1}" in r.stdout


def test_stdin_works() -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), "-"],
        input=SCHEMA_DUMP + GOOD_ROW,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_missing_file_is_a_usage_error(tmp_path: Path) -> None:
    r = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-I", str(SCRIPT), str(tmp_path / "nope.sql")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 2


def test_a_dump_filled_through_real_tables_is_clean(tmp_path: Path) -> None:
    db = real_db()
    db.execute(
        "INSERT INTO view_events (ts, page, src_kind, src, bot_class, reason, "
        "browser, os, device, lang, country) VALUES (1792000000000, 'home', 'u', "
        "'bluesky', 'h', 'ok', 'chrome', 'linux', 'desktop', 'en', 'US')"
    )
    db.execute(
        "INSERT INTO click_events (ts, link_id, country) VALUES (1, 'a-b', 'US')"
    )
    db.commit()
    dump = "\n".join(db.iterdump()) + "\n"
    r = run(dump, tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    db.execute("UPDATE view_events SET src = '198.51.100.7'")
    assert run("\n".join(db.iterdump()) + "\n", tmp_path=tmp_path).returncode == 1
