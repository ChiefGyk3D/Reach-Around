# SPDX-License-Identifier: Apache-2.0
"""The row-count verifier inside scripts/backup-d1.sh, run on its own.

The fixtures use the export grammar verified on 2026-11-27 against real D1
exports (wrangler 4.162.0, synthetic data): a PRAGMA line, CREATE TABLE with a
bare name, one INSERT per row on one line with a column list, a value that holds
a newline escaped on the same line, statements not grouped by table, no INSERT
line for an empty table. Everything is synthetic."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_backup_d1 import SCRIPT, dest, make, run

PRAGMA = b"PRAGMA defer_foreign_keys=TRUE;\n"
CREATE = b"CREATE TABLE view_daily (site TEXT, day TEXT, views INTEGER);\n"
HEAD = b'INSERT INTO "view_daily" ("site","day","views") '
ROW = HEAD + b"VALUES('links','2026-11-01',9);\n"
ROW_SPACE = HEAD + b"VALUES ('links','2026-11-02',4);\n"
# A value with a newline: escaped, so the statement stays on one physical line.
ROW_NEWLINE = (
    b'INSERT INTO "view_daily" ("site","day","views") '
    b"VALUES('links',replace('line one\\nline two','\\n',char(10)),1);\n"
)


def _verifier_source() -> str:
    text = SCRIPT.read_text()
    m = re.search(r"python3 -I - \"\$out\".*?<<'PY'\n(.*?)\nPY\n", text, re.DOTALL)
    assert m, "row-count verifier not found in backup-d1.sh"
    return m.group(1)


def _verify(
    tmp_path: Path,
    content: bytes,
    before: int,
    after: int | None = None,
    *,
    max_bytes: int = 1 << 30,
    fake_small_size: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the script's own verifier on one file for the table view_daily."""
    work = tmp_path / "v"
    work.mkdir(exist_ok=True)
    (work / "view_daily.sql").write_bytes(content)
    (work / ".counts-before").write_text(f"view_daily {before}\n")
    (work / ".counts-after").write_text(
        f"view_daily {before if after is None else after}\n"
    )
    src = work / "verifier.py"
    src.write_text(_verifier_source())
    runner = work / "run.py"
    runner.write_text(
        "import os, types\n"
        "real = os.fstat\n"
        "if os.environ.get('FAKE_SMALL'):\n"
        "    def fake(fd):\n"
        "        st = real(fd)\n"
        "        return types.SimpleNamespace(st_mode=st.st_mode, st_size=1)\n"
        "    os.fstat = fake\n"
        "exec(compile(open(os.environ['VSRC']).read(), 'v', 'exec'))\n"
    )
    env = {
        "BACKUP_MAX_BYTES": str(max_bytes),
        "VSRC": str(src),
        "PATH": "/usr/bin:/bin",
    }
    if fake_small_size:
        env["FAKE_SMALL"] = "1"
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(runner), str(work), "view_daily"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )


def test_the_real_export_grammar_is_counted_exactly(tmp_path: Path) -> None:
    body = PRAGMA + ROW + CREATE + ROW_SPACE + ROW_NEWLINE  # CREATE after an INSERT
    assert _verify(tmp_path, body, 3).returncode == 0
    assert _verify(tmp_path, body, 2).returncode == 1  # more rows than the database
    assert _verify(tmp_path, body, 4).returncode == 1  # fewer rows than the database


def test_an_empty_table_has_no_insert_line(tmp_path: Path) -> None:
    assert _verify(tmp_path, PRAGMA + CREATE, 0).returncode == 0
    assert _verify(tmp_path, PRAGMA + CREATE, 1).returncode == 1


def test_a_value_with_an_escaped_newline_is_one_row(tmp_path: Path) -> None:
    assert _verify(tmp_path, PRAGMA + CREATE + ROW_NEWLINE, 1).returncode == 0
    assert _verify(tmp_path, PRAGMA + CREATE + ROW_NEWLINE, 2).returncode == 1


def test_the_export_may_hold_rows_added_during_the_run(tmp_path: Path) -> None:
    body = PRAGMA + CREATE + ROW + ROW_SPACE
    assert _verify(tmp_path, body, 1, 2).returncode == 0
    assert _verify(tmp_path, body, 2, 2).returncode == 0
    assert _verify(tmp_path, body, 0, 1).returncode == 1  # more than the after count
    assert _verify(tmp_path, body, 3, 4).returncode == 1  # fewer than the before count


def test_a_table_that_lost_rows_fails_with_the_rerun_message(tmp_path: Path) -> None:
    r = _verify(tmp_path, PRAGMA + CREATE + ROW, 2, 1)
    assert r.returncode == 1
    assert "rows decreased" in r.stderr


def test_crlf_line_ends_are_still_counted_exactly(tmp_path: Path) -> None:
    body = (PRAGMA + CREATE + ROW + ROW_SPACE).replace(b"\n", b"\r\n")
    assert _verify(tmp_path, body, 2).returncode == 0
    assert _verify(tmp_path, body, 3).returncode == 1


BAD_SHAPES = [
    # more than one row in one statement
    b"INSERT INTO \"view_daily\" (\"site\") VALUES('a'),('b');\n",
    b'INSERT INTO "view_daily" ("site") VALUES(1),(2),(3);\n',
    # a quoted value running over a physical line, the second line looking like
    # an INSERT: one statement the line count would count twice
    b'INSERT INTO "view_daily" ("site") VALUES(\'first line\n'
    b'INSERT INTO "view_daily" ("site") VALUES marker\');\n',
    # other spellings of an insert, counted as zero rows by a naive parser
    b'insert into "view_daily" ("site") VALUES(1);\n',
    b'  INSERT INTO "view_daily" ("site") VALUES(1);\n',
    b'\tINSERT INTO "view_daily" ("site") VALUES(1);\n',
    b'INSERT INTO `view_daily` ("site") VALUES(1);\n',
    b'INSERT INTO [view_daily] ("site") VALUES(1);\n',
    b'REPLACE INTO "view_daily" ("site") VALUES(1);\n',
    b'INSERT OR REPLACE INTO "view_daily" ("site") VALUES(1);\n',
    # unbalanced quote, a cut-off statement, two statements on one line
    b'INSERT INTO "view_daily" ("site") VALUES(1,\'unbalanced);\n',
    b'INSERT INTO "view_daily" ("site") VALUES(1)\n',
    b'INSERT INTO "view_daily" ("site") VALUES(1',
    b'INSERT INTO "view_daily" ("site") VALUES(1); '
    b'INSERT INTO "view_daily" ("site") VALUES(2);\n',
    b'INSERT INTO "view_daily" ("site") VALUES(1) ON CONFLICT DO NOTHING;\n',
    # an insert into some other table, a NUL byte, bad UTF-8
    b'INSERT INTO "other" ("site") VALUES(1);\n',
    b'INSERT INTO "view_daily" ("site") VALUES(1\x00);\n',
    b'INSERT INTO "view_daily" ("site") VALUES(\'\xff\');\n',
    # attached database and other statements are only text, never run
    b"ATTACH DATABASE '/nonexistent/x.db' AS probe;\n"
    b'INSERT INTO "view_daily" VALUES(1;\n',
]


@pytest.mark.parametrize("body", BAD_SHAPES)
@pytest.mark.parametrize("expected", [0, 1, 2])
def test_a_shape_outside_the_grammar_fails_closed(
    tmp_path: Path, body: bytes, expected: int
) -> None:
    r = _verify(tmp_path, PRAGMA + CREATE + body, expected)
    assert r.returncode == 1, r.stderr
    assert "cannot be counted" in r.stderr or "does not match" in r.stderr


def test_a_line_of_exactly_one_mib_passes_and_one_more_byte_fails(
    tmp_path: Path,
) -> None:
    pad = (1 << 20) - len(b"-- \n")
    ok = b"-- " + b"x" * pad + b"\n"
    assert len(ok) == 1 << 20
    assert _verify(tmp_path, PRAGMA + CREATE + ok + ROW, 1).returncode == 0
    over = _verify(
        tmp_path, PRAGMA + CREATE + b"-- " + b"x" * (pad + 1) + b"\n" + ROW, 1
    )
    assert over.returncode == 1
    assert "too large" in over.stderr


def test_the_byte_limit_counts_what_is_read_not_the_size_at_the_start(
    tmp_path: Path,
) -> None:
    """The file looked small when it was checked (the descriptor's size is faked
    to 1 byte, as if it grew afterwards) but holds more than the limit."""
    body = ROW * 20
    r = _verify(tmp_path, body, 20, max_bytes=100, fake_small_size=True)
    assert r.returncode == 1
    assert "too large" in r.stderr
    assert _verify(tmp_path, body, 20, max_bytes=1 << 20).returncode == 0


def test_a_file_over_the_limit_at_the_start_is_refused(tmp_path: Path) -> None:
    r = _verify(tmp_path, ROW * 20, 20, max_bytes=100)
    assert r.returncode == 1
    assert "too large" in r.stderr


def test_a_file_swapped_for_a_symbolic_link_is_not_followed(tmp_path: Path) -> None:
    work = tmp_path / "v"
    work.mkdir()
    real = tmp_path / "elsewhere.sql"
    real.write_bytes(ROW)
    (work / "view_daily.sql").symlink_to(real)
    for name in (".counts-before", ".counts-after"):
        (work / name).write_text("view_daily 1\n")
    src = work / "verifier.py"
    src.write_text(_verifier_source())
    r = subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(src), str(work), "view_daily"],
        capture_output=True,
        text=True,
        env={"BACKUP_MAX_BYTES": "1000000", "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,
    )
    assert r.returncode == 1
    assert "cannot be counted" in r.stderr


@pytest.mark.parametrize("value", ["0", "abc", "-5", "01", "1099511627777", "9" * 30])
def test_a_bad_byte_limit_is_a_usage_error(tmp_path: Path, value: str) -> None:
    env = make(tmp_path)
    env["BACKUP_MAX_BYTES"] = value
    d = dest(tmp_path)
    r = run(env, "reach-db", str(d), "--apply")
    assert r.returncode == 2
    assert "BACKUP_MAX_BYTES" in r.stderr
    assert list(d.iterdir()) == []
