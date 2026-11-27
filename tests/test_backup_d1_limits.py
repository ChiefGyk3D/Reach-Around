# SPDX-License-Identifier: Apache-2.0
"""scripts/backup-d1.sh verifier: the shared byte budget, quoting and the
INSERT-token rule. Everything is synthetic."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from test_backup_d1_verifier import (
    CREATE,
    HEAD,
    PRAGMA,
    ROW,
    _verifier_source,
    _verify,
)


def _verify_tables(
    tmp_path: Path, files: dict[str, bytes], max_bytes: int
) -> subprocess.CompletedProcess[str]:
    """Run the verifier over several tables, one row per file."""
    work = tmp_path / "multi"
    work.mkdir(exist_ok=True)
    lines = "".join(f"{t} 1\n" for t in files)
    for name in (".counts-before", ".counts-after"):
        (work / name).write_text(lines)
    for t, body in files.items():
        (work / f"{t}.sql").write_bytes(body)
    src = work / "verifier.py"
    src.write_text(_verifier_source())
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(src), str(work), *files],
        capture_output=True,
        text=True,
        env={"BACKUP_MAX_BYTES": str(max_bytes), "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,
    )


def _file_for(table: str, pad: int) -> bytes:
    row = f'INSERT INTO "{table}" ("a") VALUES(1);\n'.encode()
    return f"CREATE TABLE {table} (a);\n".encode() + b"-- " + b"x" * pad + b"\n" + row


def test_the_byte_budget_is_shared_by_all_the_export_files(tmp_path: Path) -> None:
    files = {"t_one": _file_for("t_one", 1000), "t_two": _file_for("t_two", 1000)}
    one = len(files["t_one"])
    # Each file alone is under the cap; together they are over it.
    r = _verify_tables(tmp_path, files, one + one // 2)
    assert r.returncode == 1
    assert "too large" in r.stderr
    # One table alone, below the cap, passes; both pass under a roomy cap.
    assert _verify_tables(tmp_path, {"t_one": files["t_one"]}, one + 10).returncode == 0
    assert _verify_tables(tmp_path, files, 3 * one).returncode == 0


def test_doubled_quotes_do_not_end_a_value_early(tmp_path: Path) -> None:
    """'' inside a string and "" inside an identifier stay one quoted run: the
    text that looks like another INSERT is data, so this is one row."""
    tricky = HEAD + b"VALUES('x''); INSERT INTO \"view_daily\" VALUES(''y');\n"
    assert _verify(tmp_path, PRAGMA + CREATE + tricky, 1).returncode == 0
    assert _verify(tmp_path, PRAGMA + CREATE + tricky, 2).returncode == 1
    ident = b'INSERT INTO "view_daily" ("we""ird","site") VALUES(1,\'it\'\'s\');\n'
    assert _verify(tmp_path, PRAGMA + CREATE + ident, 1).returncode == 0


CTE_AND_OTHER = [
    b'WITH x(v) AS (VALUES(1))\nINSERT INTO "view_daily" ("views") SELECT v FROM x;\n',
    b'WITH x(v) AS (SELECT 1) INSERT INTO "view_daily" ("views") SELECT v FROM x;\n',
    b'WITH x(v) AS (SELECT 1)\nREPLACE INTO "view_daily" ("views") SELECT v FROM x;\n',
    b'SELECT 1; INSERT INTO "view_daily" ("views") VALUES(1);\n',
    b'INSERT OR IGNORE INTO "view_daily" ("views") VALUES(1);\n',
    b'(INSERT INTO "view_daily" ("views") VALUES(1));\n',
    b"CREATE TRIGGER x AFTER UPDATE ON t BEGIN INSERT INTO t VALUES(1); END;\n",
    b'insert\ninto "view_daily" ("views") VALUES(1);\n',
]


@pytest.mark.parametrize("body", CTE_AND_OTHER)
@pytest.mark.parametrize("expected", [0, 1])
def test_an_insert_word_outside_quotes_fails_closed(
    tmp_path: Path, body: bytes, expected: int
) -> None:
    r = _verify(tmp_path, PRAGMA + CREATE + body, expected)
    assert r.returncode == 1, r.stderr
    assert "cannot be counted" in r.stderr or "does not match" in r.stderr


def test_the_guard_trigger_event_words_and_quoted_text_are_not_inserts(
    tmp_path: Path,
) -> None:
    trigger = (
        b"CREATE TRIGGER kit_meta_floor_insert BEFORE INSERT ON view_daily "
        b"WHEN NEW.key = 'insert' BEGIN SELECT RAISE(ABORT, 'replace it'); END;\n"
    )
    assert _verify(tmp_path, PRAGMA + CREATE + trigger + ROW, 1).returncode == 0
