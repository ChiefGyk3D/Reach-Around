# SPDX-License-Identifier: Apache-2.0
"""Migration 0022 recreates the kit_meta floor guard triggers with exact bodies (#877).

0021 used CREATE TRIGGER IF NOT EXISTS, which keeps a same-named trigger with a
different body. 0022 drops and recreates both, and creates them before the repair.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MIG_DIR = ROOT / "worker" / "migrations"
M21 = (MIG_DIR / "0021_kit_floor_guard.sql").read_text()
M22 = (MIG_DIR / "0022_kit_floor_guard_recreate.sql").read_text()
SCHEMA_JSON = (ROOT / "scripts" / "dump-schema.json").read_text()
NAMES = ("kit_meta_floor_insert", "kit_meta_floor_update")


def fresh() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE kit_meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL) STRICT;"
    )
    return db


def bodies(db: sqlite3.Connection) -> dict[str, str]:
    rows = db.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger'"
    ).fetchall()
    return {n: " ".join(s.split()) for n, s in rows}


def intended() -> dict[str, str]:
    out = {}
    for name in NAMES:
        m = re.search(rf'"{name}": "(CREATE TRIGGER {name}[^"]*)"', SCHEMA_JSON)
        assert m, name
        out[name] = m.group(1)
    return out


def test_replaces_a_same_named_trigger_with_a_different_body() -> None:
    db = fresh()
    for name in NAMES:
        event = "INSERT" if name.endswith("insert") else "UPDATE"
        weak = f"CREATE TRIGGER {name} BEFORE {event} ON kit_meta"
        db.execute(weak + " WHEN 0 BEGIN SELECT 1; END")
    db.executescript(M21)  # IF NOT EXISTS keeps the weak triggers: the defect
    assert bodies(db) != intended()
    db.executescript(M22)
    assert bodies(db) == intended()
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("INSERT INTO kit_meta VALUES ('unstored_floor_ts', -1)")
    db.execute("INSERT INTO kit_meta VALUES ('unstored_floor_ts', 5)")
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE kit_meta SET value = -1 WHERE key = 'unstored_floor_ts'")


def test_works_when_no_trigger_exists_and_final_bodies_are_exact() -> None:
    db = fresh()
    db.execute("INSERT INTO kit_meta VALUES ('unstored_floor_ts', 7)")
    db.executescript(M22)
    assert bodies(db) == intended()
    assert db.execute("SELECT value FROM kit_meta").fetchone() == (7,)


def test_repairs_a_negative_floor_and_leaves_other_keys() -> None:
    db = fresh()
    # no triggers: a negative floor is storable, as on a database that predates 0021
    db.execute("INSERT INTO kit_meta VALUES ('unstored_floor_ts', -5)")
    db.execute("INSERT INTO kit_meta VALUES ('last_purge_ts', -9)")
    db.executescript(M22)
    assert (
        db.execute(
            "SELECT value FROM kit_meta WHERE key = 'unstored_floor_ts'"
        ).fetchone()[0]
        > 0
    )
    assert db.execute(
        "SELECT value FROM kit_meta WHERE key = 'last_purge_ts'"
    ).fetchone() == (-9,)


def test_applies_twice_and_after_0021() -> None:
    db = fresh()
    db.executescript(M21)
    db.executescript(M22)
    db.executescript(M22)
    assert bodies(db) == intended()


def test_guard_triggers_are_created_before_the_repair() -> None:
    text = re.sub(r"--[^\n]*", "", M22)
    repair = text.index("UPDATE kit_meta SET")
    for name in NAMES:
        assert text.index(f"CREATE TRIGGER {name}") < repair
        assert text.index(f"DROP TRIGGER IF EXISTS {name}") < text.index(
            f"CREATE TRIGGER {name}"
        )
    assert "IF NOT EXISTS" not in text


def test_annotated_as_contract_for_the_drop() -> None:
    assert re.search(r"^--\s*migration-phase:\s*contract\s*$", M22, re.MULTILINE)
