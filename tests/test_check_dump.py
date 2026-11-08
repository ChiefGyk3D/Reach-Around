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
        "INSERT INTO click_events (ts, link_id, country) "
        "VALUES (1792000000000, 'a-b', 'US')"
    )
    db.commit()
    dump = "\n".join(db.iterdump()) + "\n"
    r = run(dump, tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    db.execute("UPDATE view_events SET src = '198.51.100.7'")
    assert run("\n".join(db.iterdump()) + "\n", tmp_path=tmp_path).returncode == 1


# ---- round 3: numeric columns, bare hex labels, nested encodings -------------

IPV4_INT = 203 * 256**3 + 113 * 256 + 211  # 3405803987, the reviewer's example


def insert(table: str, values: str, cols: str = "") -> str:
    return f"INSERT INTO {table}{cols} VALUES ({values});\n"  # noqa: S608 - synthetic


@pytest.mark.parametrize(
    "stmt",
    [
        insert("counters", f"'x', '2026-01-01', {IPV4_INT}"),
        insert("counters", "'x', '2026-01-01', 16777216"),
        insert("click_events", f"1, {IPV4_INT}, 'a', 'US', 'main'"),
        insert("click_events", f"{IPV4_INT}, 1792000000000, 'a', 'US', 'main'"),
        insert("kit_meta", f"'k', {IPV4_INT}"),
        insert("view_meta", f"'k', '{IPV4_INT}'"),
        insert("view_meta", f"'k', {IPV4_INT}"),
        insert(
            "view_daily",
            f"'m', '2026-01-01', 'UTC', 'home', 'd', '', 'US', 'h', {IPV4_INT}, 1",
        ),
        insert(
            "view_events",
            f"1, {IPV4_INT}, 'home', 'd', '', 'US', '', '', 'chrome', 1, 'linux', "
            "'desktop', 'en', '', 'unknown', 'h', 'ok', 0, 'main'",
        ),
    ],
)
def test_a_whole_number_address_in_a_numeric_column_is_caught(
    stmt: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + stmt, tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "numeric value out of range" in r.stdout
    assert str(IPV4_INT) not in r.stdout + r.stderr


@pytest.mark.parametrize(
    "stmt",
    [
        insert("counters", "'x', '2026-01-01', 1234"),
        insert("click_events", "1, 1792000000000, 'a', 'US', 'main'"),
        insert("kit_meta", "'last_purge_ts', 1792000000000"),
        insert("kit_meta", "'n', 12"),
        insert("view_meta", "'rolled_through', '2027-03-13'"),
        insert("view_meta", "'bud', '12'"),
        insert("view_meta", "'ok', '1792000000000'"),
        insert("sqlite_sequence", "'click_events', 5"),
    ],
)
def test_ordinary_numbers_pass(stmt: str, tmp_path: Path) -> None:
    r = run(SCHEMA_DUMP + stmt, tmp_path=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_timestamp_outside_the_plausible_window_is_caught(tmp_path: Path) -> None:
    for ts in ("1792000000", "5", "99999999999999"):
        r = run(
            SCHEMA_DUMP + insert("click_events", f"1, {ts}, 'a', 'US', 'main'"),
            tmp_path=tmp_path,
        )
        assert r.returncode == 1, ts
        assert "numeric value out of range" in r.stdout


def test_every_integer_column_has_a_numeric_rule_and_no_rule_is_stale() -> None:
    cfg = json.loads(SCHEMA.read_text())
    rules = cfg["numeric"]
    kinds = cfg["numeric_kinds"]
    assert set(rules.values()) <= set(kinds), "rule names an unknown kind"
    db = real_db()
    integer_cols = {
        f"{name}.{c[1]}"
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        for c in db.execute(f"PRAGMA table_info('{name}')")  # noqa: S608
        if c[2].upper() == "INTEGER"
    }
    assert integer_cols, "no integer columns collected"
    for col in integer_cols:
        assert col in rules, f"{col} has no numeric rule"
    all_cols = {f"{t}.{c}" for t, cols in cfg["tables"].items() for c in cols}
    for col in rules:
        assert col in all_cols, f"stale numeric rule {col}"


@pytest.mark.parametrize("token", ["c6336407", "C6336407", "0a0b0c0d", "deadbeef"])
@pytest.mark.parametrize("col", ["src", "page", "reason"])
def test_a_bare_eight_digit_hex_token_is_refused_in_label_columns(
    token: str, col: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{token}'", col), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "hex token" in r.stdout
    assert token.lower() not in (r.stdout + r.stderr).lower()


def test_hex_tokens_are_allowed_where_ids_live_and_ordinary_words_pass(
    tmp_path: Path,
) -> None:
    # Longer hashes live in their own columns; short hex-looking words are fine.
    for v in ("facade", "decade", "bluesky", "2027-03-14"):
        assert run(SCHEMA_DUMP + row(f"'{v}'"), tmp_path=tmp_path).returncode == 0, v
    h = "0123456789abcdef0123456789abcdef"
    ok = insert("visitor_seen", f"'2027-03-14', '{h}'")
    assert run(SCHEMA_DUMP + ok, tmp_path=tmp_path).returncode == 0


def nest(s: str, levels: int) -> str:
    for _ in range(levels):
        s = s.replace("%", "%25")
    return s


@pytest.mark.parametrize("levels", [1, 4, 5, 6, 20, 100])
def test_percent_encoding_is_decoded_to_a_fixed_point(
    levels: int, tmp_path: Path
) -> None:
    v = nest("203%2E0%2E113%2E211", levels)
    r = run(SCHEMA_DUMP + row(f"'{v}'"), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ipv4" in r.stdout


def test_absurd_nesting_is_refused_not_looped_on(tmp_path: Path) -> None:
    v = "%" * 3 + "2E"
    for _ in range(300):
        v = v.replace("%", "%25")
    assert len(v) < 200_000
    r = run(SCHEMA_DUMP + row(f"'{v}'"), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "oversize value" in r.stdout or "unstable encoding" in r.stdout


# ---- round 4: undeclared numeric columns fail closed; all-digit hex tokens ----

NEG32 = IPV4_INT - 2**32  # the same address as a signed 32-bit integer
OVER32 = IPV4_INT + 2**32


@pytest.mark.parametrize("n", [IPV4_INT, NEG32, OVER32, 7, 0, -1, 1792000000000])
@pytest.mark.parametrize(
    "stmt",
    [
        "INSERT INTO view_meta (key, value) VALUES ({n}, 'v');\n",
        "INSERT INTO sqlite_sequence VALUES ({n}, 5);\n",
        "INSERT INTO leads (id, email) VALUES ({n}, 'a@b.cc');\n",
    ],
)
def test_a_number_in_a_column_with_no_rule_is_refused(
    stmt: str, n: int, tmp_path: Path
) -> None:
    # Fail closed: a column that may hold numbers must be declared in the schema
    # file. Label columns (key, name, id of text type) take no number at all.
    r = run(SCHEMA_DUMP + stmt.format(n=n), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "numeric value out of range" in r.stdout
    assert abs(n) < 1000 or str(abs(n)) not in r.stdout + r.stderr


@pytest.mark.parametrize("n", [NEG32, OVER32, -(2**31), -(2**24), 2**32, -1])
@pytest.mark.parametrize(
    "table_values",
    [
        ("counters", "'x', '2026-01-01', {n}"),
        ("kit_meta", "'k', {n}"),
        ("click_events", "1, {n}, 'a', 'US', 'main'"),
        ("view_meta", "'k', '{n}'"),
    ],
)
def test_signed_and_wide_forms_are_refused_in_declared_columns(
    table_values: tuple[str, str], n: int, tmp_path: Path
) -> None:
    table, values = table_values
    r = run(SCHEMA_DUMP + insert(table, values.format(n=n)), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "numeric value out of range" in r.stdout


def test_sqlite_sequence_seq_has_a_rule(tmp_path: Path) -> None:
    ok = insert("sqlite_sequence", "'click_events', 5")
    assert run(SCHEMA_DUMP + ok, tmp_path=tmp_path).returncode == 0
    bad = insert("sqlite_sequence", f"'click_events', {IPV4_INT}")
    assert run(SCHEMA_DUMP + bad, tmp_path=tmp_path).returncode == 1


def test_every_numeric_or_untyped_column_has_a_rule() -> None:
    cfg = json.loads(SCHEMA.read_text())
    db = real_db()
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        for c in db.execute(f"PRAGMA table_info('{name}')"):  # noqa: S608
            if f"{name}.{c[1]}" == "sqlite_sequence.name":
                continue  # a table name: text, takes no number
            if c[2].upper() in ("INTEGER", "REAL", "NUMERIC", ""):
                assert f"{name}.{c[1]}" in cfg["numeric"], f"{name}.{c[1]}"


# Eight hex digits that are all 0-9, so they carry no a-f letter: the hex form of
# an address such as 203.0.113.211 is cb0071d3, but 10.0.0.1 is 0a000001 and
# 100.101.102.103 is 64656667. Both shapes must be refused in label columns.
DIGIT_HEX = ["64656667", "01020304", "10101010", "0xC6336407", "00000001"]


@pytest.mark.parametrize("token", DIGIT_HEX[:3] + DIGIT_HEX[4:])
@pytest.mark.parametrize("col", ["src", "page", "reason"])
def test_an_all_digit_eight_char_hex_token_is_refused_in_label_columns(
    token: str, col: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{token}'", col), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "hex token" in r.stdout
    assert token not in r.stdout + r.stderr


@pytest.mark.parametrize(
    "value", ["2027-03-14", "home", "bluesky", "news.example.test", "1234567", "ok"]
)
def test_ordinary_labels_still_pass(value: str, tmp_path: Path) -> None:
    for col in ("src", "page", "reason"):
        r = run(SCHEMA_DUMP + row(f"'{value}'", col), tmp_path=tmp_path)
        if col == "src" or col == "page":
            assert r.returncode == 0, (col, r.stdout)


def test_an_eight_digit_value_in_a_ruled_text_column_is_a_range_matter(
    tmp_path: Path,
) -> None:
    # view_meta.value is a declared counter column: 12 passes, an address-sized
    # number fails by range, and it is not also called a hex token.
    ok = insert("view_meta", "'k', '12'")
    assert run(SCHEMA_DUMP + ok, tmp_path=tmp_path).returncode == 0
    bad = insert("view_meta", "'k', '99999999'")
    r = run(SCHEMA_DUMP + bad, tmp_path=tmp_path)
    assert r.returncode == 1
    assert "numeric value out of range" in r.stdout
    assert "hex token" not in r.stdout


# ---- round 5: resolver-semantics address check, every text column, two-way ----

RESOLVER_FORMS = [
    ("0313.0.0161.0323", "ipv4"),  # legacy octal, = 203.0.113.211
    ("0xcb.0x00.0x71.0xd3", "ipv4"),  # hex components
    ("0xCB.0.113.211", "ipv4"),
    ("203.0.29139", "ipv4"),  # three components
    ("203.7405907", "ipv4"),  # two components
    ("3405803987", "ipv4"),  # one component
    ("0xcb0071d3", "ipv4"),
    ("031300070723", "ipv4"),  # one-component octal
    ("0203.0.0113.0211", "ipv4"),  # leading zeros, not valid octal
    ("0198.51.100.7", "ipv4"),
    ("198.51.25607", "ipv4"),  # reviewer examples
    ("198.3302407", "ipv4"),
    ("ip-203.0.113.211-x", "ipv4"),  # embedded
    ("see 203.0.113.211 ok", "ipv4"),
    ("a,203.0.113.211;b", "ipv4"),
    ("203.0.113.211.", "ipv4"),
    ("[2001:db8::7]", "ipv6"),
    ("fe80::1%eth0", "ipv6"),
    ("fe80::1%25eth0", "ipv6"),
    ("::ffff:203.0.113.211", "ipv4"),
    ("::ffff:cb00:71d3", "ipv6"),
    ("2001:DB8::7", "ipv6"),
    ("2001:0db8:0000:0000:0000:0000:0000:0007", "ipv6"),
    ("::1", "ipv6"),
    ("x 2001:db8::7 y", "ipv6"),
]


@pytest.mark.parametrize(("value", "kind"), RESOLVER_FORMS)
@pytest.mark.parametrize("col", ["src", "region"])
def test_anything_a_resolver_reads_as_an_address_is_refused(
    value: str, kind: str, col: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{value}'", col), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert kind in r.stdout
    assert value not in r.stdout + r.stderr


def test_every_resolver_form_also_hides_behind_percent_encoding(
    tmp_path: Path,
) -> None:
    for value, _kind in RESOLVER_FORMS[:8]:
        enc = "".join(f"%{b:02X}" for b in value.encode())
        r = run(SCHEMA_DUMP + row(f"'{enc}'"), tmp_path=tmp_path)
        assert r.returncode == 1, value
        enc2 = enc.replace("%", "%25")
        assert run(SCHEMA_DUMP + row(f"'{enc2}'"), tmp_path=tmp_path).returncode == 1


@pytest.mark.parametrize(
    "value",
    [
        "home",
        "bluesky",
        "news.example.test",
        "2027-03-14",
        "ok",
        "en",
        "v2.0-beta",
        "São Paulo",
        "a.b.c",
        "example-one",
        "1.2.3.x",
        "x.example.test",
    ],
)
def test_words_hosts_and_dates_are_not_addresses(value: str, tmp_path: Path) -> None:
    col = "region" if " " in value else "src"
    r = run(SCHEMA_DUMP + row(f"'{value}'", col), tmp_path=tmp_path)
    assert r.returncode == 0, (value, r.stdout)


@pytest.mark.parametrize("token", ["12345678", "0000000012", "99999999999", "00123456"])
@pytest.mark.parametrize("col", ["src", "page", "reason", "region", "city"])
def test_long_digit_tokens_are_refused_in_every_text_column(
    token: str, col: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{token}'", col), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "token" in r.stdout
    assert token not in r.stdout + r.stderr


@pytest.mark.parametrize("token", ["c6336407", "64656667", "0A0B0C0D", "deadbeef"])
@pytest.mark.parametrize("col", ["region", "city", "reason"])
def test_hex_tokens_are_refused_in_columns_outside_the_old_label_list(
    token: str, col: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{token}'", col), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "hex token" in r.stdout


def test_text_in_a_numeric_rule_column_is_held_to_the_token_rules(
    tmp_path: Path,
) -> None:
    # view_meta.value expects digit counters and timestamps, never a hex token.
    for v in ("3a9f8c21", "c6336407", "0xcb0071d3", "203.0.113.211"):
        r = run(SCHEMA_DUMP + insert("view_meta", f"'k', '{v}'"), tmp_path=tmp_path)
        assert r.returncode == 1, v
    # kit_meta.value is an integer column: a text value there is a token like any other.
    r = run(SCHEMA_DUMP + insert("kit_meta", "'k', '12345678'"), tmp_path=tmp_path)
    assert r.returncode == 1
    for ok in ("12", "1792000000000", "00000012"):
        r = run(SCHEMA_DUMP + insert("view_meta", f"'k', '{ok}'"), tmp_path=tmp_path)
        assert r.returncode == 0, (ok, r.stdout)


def test_the_hourly_counter_key_format_still_passes(tmp_path: Path) -> None:
    for key in ("bud:00490000:view", "cap:00490000:0123456789abcdef"):
        r = run(SCHEMA_DUMP + insert("view_meta", f"'{key}', '3'"), tmp_path=tmp_path)
        assert r.returncode == 0, (key, r.stdout)
    bad = insert("view_meta", "'bud:16909060:view', '3'")
    assert run(SCHEMA_DUMP + bad, tmp_path=tmp_path).returncode == 1


def test_an_email_local_part_of_digits_is_not_a_token_finding(tmp_path: Path) -> None:
    ok = insert(
        "leads",
        "'" + "a" * 32 + "', 'x12345678@example.cc', 1792000000000, "
        "1792000000000, NULL, 'gate', 'US', NULL, NULL, NULL, NULL",
    )
    assert run(SCHEMA_DUMP + ok, tmp_path=tmp_path).returncode == 0
    bad = ok.replace("x12345678@example.cc", "203.0.113.211@example.cc")
    assert run(SCHEMA_DUMP + bad, tmp_path=tmp_path).returncode == 1


def test_schema_comparison_is_two_way(tmp_path: Path) -> None:
    # A missing table fails.
    start = SCHEMA_DUMP.index("CREATE TABLE view_events")
    end = SCHEMA_DUMP.index(";", start) + 1
    no_table = SCHEMA_DUMP[:start] + SCHEMA_DUMP[end:]
    assert "CREATE TABLE view_events" not in no_table
    r = run(no_table + GOOD_ROW.replace("view_events", "view_meta"), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "missing table view_events" in r.stdout
    # A missing column fails.
    cut = SCHEMA_DUMP.replace(
        "key TEXT PRIMARY KEY,\n  value TEXT NOT NULL", "key TEXT PRIMARY KEY"
    )
    assert cut != SCHEMA_DUMP
    r = run(cut + insert("view_meta", "'k'"), tmp_path=tmp_path)
    assert r.returncode == 1
    assert "missing column value in view_meta" in r.stdout
    # Cloudflare's own bookkeeping tables may be absent, and a data-only dump
    # (no CREATE TABLE at all) has no schema to compare.
    assert run(SCHEMA_DUMP + GOOD_ROW, tmp_path=tmp_path).returncode == 0
    assert run(GOOD_ROW, tmp_path=tmp_path).returncode == 0


# ---- round 6: colon-joined addresses, embedded digit runs, the email column ----

COLON_FORMS = [
    ("label:203.0.113.211", "ipv4"),
    ("203.0.113.211:443", "ipv4"),
    ("host:0313.0.0161.0323:80", "ipv4"),
    ("a:3325256711", "ipv4"),
    ("ip:0xcb0071d3", "ipv4"),
    ("x:203.29139:1", "ipv4"),
    ("label:2001:db8::7", "ipv6"),
    ("label:fe80::1", "ipv6"),
    ("2001:db8::7:", "ipv6"),
    ("a:b:::ffff:203.0.113.211", "ipv4"),
]


@pytest.mark.parametrize(("value", "kind"), COLON_FORMS)
def test_an_address_joined_to_other_text_by_a_colon_is_refused(
    value: str, kind: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + row(f"'{value}'"), tmp_path=tmp_path)
    assert r.returncode == 1, r.stdout + r.stderr
    assert kind in r.stdout
    assert value not in r.stdout + r.stderr


@pytest.mark.parametrize("value", ["bud:1:view", "note:ok", "a:b:c", "12:30:45", "k:v"])
def test_ordinary_colon_text_passes(value: str, tmp_path: Path) -> None:
    r = run(SCHEMA_DUMP + row(f"'{value}'"), tmp_path=tmp_path)
    assert r.returncode == 0, (value, r.stdout)


def test_a_digit_run_embedded_in_a_numeric_column_value_is_checked(
    tmp_path: Path,
) -> None:
    for v in ("abc:3325256711", "x12345678901", "ts=1792000000000", "3325256711 "):
        r = run(SCHEMA_DUMP + insert("view_meta", f"'k', '{v}'"), tmp_path=tmp_path)
        assert r.returncode == 1, v
    # A whole value that is a number in range still passes.
    for v in ("12", "1792000000000"):
        r = run(SCHEMA_DUMP + insert("view_meta", f"'k', '{v}'"), tmp_path=tmp_path)
        assert r.returncode == 0, v


def lead(email: str) -> str:
    return insert(
        "leads",
        "'" + "a" * 32 + f"', '{email}', 1792000000000, 1792000000000, NULL, "
        "'gate', 'US', NULL, NULL, NULL, NULL",
    )


@pytest.mark.parametrize(
    "email",
    [
        "deadbeef@example.cc",
        "203.0.113.211@example.cc",
        "0313.0.0161.0323@example.cc",
        "0xcb.0x00.0x71.0xd3@example.cc",
        "203.29139@example.cc",
        "x@203.0.113.211.cc",
        "c6336407+tag@example.cc",
    ],
)
def test_the_email_column_is_not_exempt_from_the_address_and_hex_rules(
    email: str, tmp_path: Path
) -> None:
    r = run(SCHEMA_DUMP + lead(email), tmp_path=tmp_path)
    assert r.returncode == 1, (email, r.stdout)
    assert email not in r.stdout + r.stderr


@pytest.mark.parametrize(
    "email",
    [
        "alice@example.cc",
        "12345678@example.cc",  # a digits-only local part (for example a chat number)
        "3325256711@example.cc",
        "john19850312@example.cc",
        "a.b+c@mail.example.cc",
    ],
)
def test_ordinary_emails_still_pass(email: str, tmp_path: Path) -> None:
    r = run(SCHEMA_DUMP + lead(email), tmp_path=tmp_path)
    assert r.returncode == 0, (email, r.stdout)
