# SPDX-License-Identifier: Apache-2.0
"""Erasure coverage (#856): every store that can hold lead data is accounted for.

The tables and columns are read from the Worker migrations (never hand-listed).
scripts/erasure-stores.json is the reviewed registry: a new table or column, a
personal-looking column in a table not meant to hold one, a new Worker storage
primitive, or a lead table the delete tool does not reach fails here until the
change is reviewed on purpose. Synthetic data only; no network.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from reach_around.commands.leads import CHECKLIST, _sql

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "worker" / "migrations").glob("*.sql"))
REGISTRY_FILE = ROOT / "scripts" / "erasure-stores.json"
PRIVACY = ROOT / "docs" / "privacy.md"
BACKUP_SCRIPT = ROOT / "scripts" / "backup-d1.sh"
WORKER_SRC = ROOT / "worker" / "src"
WRANGLER = ROOT / "worker" / "wrangler.toml"
LEAD_RETENTION = WORKER_SRC / "kit" / "leadretention.ts"

CLASSES = {"lead", "lead_hash", "unlinkable", "aggregate", "internal"}
# Column names that suggest a person, a device, a place, a session or a hashed
# identifier. In a table that is not class lead each one needs a reviewed reason.
PERSON_NAME = re.compile(
    r"mail|name|addr|(^|_)ip($|_)|agent|(^|_)ua($|_)|sess|hash|^h$|lead|visitor|user"
    r"|token|inquir|detail|brand|phone|city|region|country|lang|browser|device|^os$"
    r"|^vp$|^net$|src|referr|cookie",
    re.IGNORECASE,
)
# What a Worker source file can write to outside D1.
PRIMITIVES = {
    "put(": re.compile(r"\.put\("),
    "caches.default": re.compile(r"\bcaches\.default\b"),
    "KVNamespace": re.compile(r"\bKVNamespace\b"),
    "R2Bucket": re.compile(r"\bR2Bucket\b"),
    "DurableObject": re.compile(r"\bDurableObject"),
    "Queue": re.compile(r"\bQueue\b"),
    "AnalyticsEngineDataset": re.compile(r"\bAnalyticsEngineDataset\b"),
    "writeDataPoint": re.compile(r"\bwriteDataPoint\b"),
}


def schema_sql(sql: str) -> str:
    """The migration without its CHECK constraints (migration 0009 nests about
    forty replace() calls that overflow the parser stack of an old SQLite).
    Constraints change neither a table's columns nor its foreign keys."""
    out: list[str] = []
    i = 0
    check = re.compile(r"\bCHECK\s*\(", re.IGNORECASE)
    while i < len(sql):
        m = check.search(sql, i)
        if not m:
            out.append(sql[i:])
            break
        out.append(sql[i : m.start()])
        depth, j, quote = 1, m.end(), ""
        while j < len(sql) and depth:
            c = sql[j]
            if quote:
                quote = "" if c == quote else quote
            elif c in "'\"":
                quote = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def real_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for m in MIGRATIONS:
        db.executescript(schema_sql(m.read_text(encoding="utf-8")))
    return db


def live_schema(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """table -> {columns, fk_parents} read from the database the migrations build."""
    out: dict[str, dict[str, Any]] = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        cols = [c[1] for c in db.execute(f"PRAGMA table_info('{name}')")]  # noqa: S608
        parents = [r[2] for r in db.execute(f"PRAGMA foreign_key_list('{name}')")]  # noqa: S608
        out[name] = {"columns": cols, "fk_parents": parents}
    return out


def load_registry() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return data


def schema_violations(
    schema: dict[str, dict[str, Any]], registry: dict[str, Any]
) -> list[str]:
    """Every way the schema and the reviewed registry disagree."""
    stores: dict[str, Any] = registry["stores"]
    found: list[str] = []
    for table in sorted(set(schema) - set(stores)):
        found.append(f"unreviewed table {table}: add it to scripts/erasure-stores.json")
    for table in sorted(set(stores) - set(schema)):
        found.append(f"registry lists {table} but no migration creates it")
    for table in sorted(set(schema) & set(stores)):
        entry = stores[table]
        cols = schema[table]["columns"]
        if entry.get("class") not in CLASSES:
            found.append(f"{table}: class must be one of {sorted(CLASSES)}")
            continue
        if not str(entry.get("reason", "")).strip():
            found.append(f"{table}: a reason is required")
        if entry.get("columns") != cols:
            found.append(
                f"{table}: columns differ from the migrations "
                f"(new or changed: {sorted(set(cols) ^ set(entry.get('columns', [])))})"
            )
        if "leads" in schema[table]["fk_parents"] and entry["class"] != "lead":
            found.append(f"{table}: references leads but is not class lead")
        if "lead_id" in cols and entry["class"] != "lead":
            found.append(f"{table}: has a lead_id column but is not class lead")
        if "email" in cols and entry["class"] != "lead":
            found.append(f"{table}: has an email column but is not class lead")
        reviewed = entry.get("reviewed_columns", {})
        if entry["class"] in {"aggregate", "unlinkable"}:
            for col in cols:
                if PERSON_NAME.search(col) and not str(reviewed.get(col, "")).strip():
                    found.append(
                        f"{table}.{col}: looks personal; review it and list it in "
                        "reviewed_columns with a reason, or class the table lead"
                    )
        for col in reviewed:
            if col not in cols:
                found.append(f"{table}.{col}: reviewed but not a column")
        if entry["class"] in {"lead", "lead_hash", "unlinkable"} and entry.get(
            "backup"
        ) not in {
            "never",
            "exported",
        }:
            found.append(f"{table}: backup must be never or exported")
    return found


def worker_primitives() -> dict[str, set[str]]:
    """file -> storage primitives its TypeScript source uses."""
    out: dict[str, set[str]] = {}
    for path in sorted(WORKER_SRC.rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        hits = {name for name, rx in PRIMITIVES.items() if rx.search(text)}
        if hits:
            out[path.relative_to(ROOT).as_posix()] = hits
    return out


def wrangler_tables() -> set[str]:
    """Binding table names in the wrangler config, commented templates included."""
    names: set[str] = set()
    for line in WRANGLER.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#?\s*\[{1,2}([A-Za-z0-9_]+)", line)
        if m:
            names.add(m.group(1))
    return names


def runtime_violations(
    primitives: dict[str, set[str]], tables: set[str], registry: dict[str, Any]
) -> list[str]:
    reviewed: dict[str, dict[str, str]] = registry["runtime_stores"]["primitives"]
    found: list[str] = []
    for path, names in primitives.items():
        for name in sorted(names):
            if not str(reviewed.get(path, {}).get(name, "")).strip():
                found.append(
                    f"{path}: {name} is a storage primitive with no reviewed reason"
                )
    for path, entry in reviewed.items():
        for name in entry:
            if name not in primitives.get(path, set()):
                found.append(
                    f"registry lists {name} in {path} but the source no longer uses it"
                )
    for table in sorted(tables - set(registry["runtime_stores"]["wrangler_tables"])):
        found.append(f"wrangler.toml has an unreviewed binding section [{table}]")
    return found


# --- the real repository -------------------------------------------------------


def test_the_migrations_are_collected() -> None:
    assert MIGRATIONS, "no migrations collected"


def test_every_table_and_column_is_reviewed() -> None:
    assert schema_violations(live_schema(real_db()), load_registry()) == []


def test_the_schema_check_bites() -> None:
    schema = live_schema(real_db())
    registry = load_registry()

    new_table = copy.deepcopy(schema)
    new_table["lead_notes"] = {"columns": ["id", "body"], "fk_parents": []}
    assert any(
        "unreviewed table lead_notes" in v
        for v in schema_violations(new_table, registry)
    )

    new_column = copy.deepcopy(schema)
    new_column["kit_events"]["columns"].append("user_agent")
    assert any(
        v.startswith("kit_events: columns differ")
        for v in schema_violations(new_column, registry)
    )

    sneaky = copy.deepcopy(schema)
    sneaky["counters"]["columns"].append("session_id")
    reg = copy.deepcopy(registry)
    reg["stores"]["counters"]["columns"].append("session_id")
    assert any(
        "counters.session_id: looks personal" in v
        for v in schema_violations(sneaky, reg)
    )

    linked = copy.deepcopy(schema)
    linked["view_meta"]["fk_parents"] = ["leads"]
    assert any(
        "references leads but is not class lead" in v
        for v in schema_violations(linked, registry)
    )

    mailbox = copy.deepcopy(schema)
    mailbox["alert_quota"]["columns"].append("email")
    reg2 = copy.deepcopy(registry)
    reg2["stores"]["alert_quota"]["columns"].append("email")
    assert any("has an email column" in v for v in schema_violations(mailbox, reg2))

    no_reason = copy.deepcopy(registry)
    no_reason["stores"]["leads"]["reason"] = " "
    assert any(
        "leads: a reason is required" in v for v in schema_violations(schema, no_reason)
    )


def test_a_lead_id_column_forces_the_lead_class() -> None:
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT);"
        "CREATE TABLE lead_notes (id INTEGER PRIMARY KEY, "
        "lead_id TEXT REFERENCES leads (id));"
    )
    schema = live_schema(db)
    assert schema["lead_notes"]["fk_parents"] == ["leads"]
    registry = {
        "stores": {
            "leads": {
                "class": "lead",
                "reason": "r",
                "columns": ["id", "email"],
                "backup": "never",
            },
            "lead_notes": {
                "class": "aggregate",
                "reason": "r",
                "columns": ["id", "lead_id"],
                "reviewed_columns": {"lead_id": "claimed harmless"},
            },
        }
    }
    found = schema_violations(schema, registry)
    assert any("lead_notes: references leads" in v for v in found)
    assert any("lead_notes: has a lead_id column" in v for v in found)


def test_lead_tables_are_what_the_foreign_keys_say() -> None:
    schema = live_schema(real_db())
    registry = load_registry()
    lead_tables = {t for t, e in registry["stores"].items() if e["class"] == "lead"}
    linked = {t for t, s in schema.items() if "leads" in s["fk_parents"]} | {"leads"}
    assert lead_tables == linked == {"leads", "kit_events"}


def test_every_registered_class_has_a_reason_and_known_class() -> None:
    for table, entry in load_registry()["stores"].items():
        assert entry["class"] in CLASSES, table
        assert entry["reason"].strip(), table


# --- the delete tool reaches every lead table ----------------------------------


def delete_sql() -> str:
    return "\n".join(_sql("0123456789abcdef0123456789abcdef", "nobody@example.test"))


def test_the_delete_sql_deletes_every_lead_table_and_reads_each_back() -> None:
    sql = delete_sql()
    tables = [t for t, e in load_registry()["stores"].items() if e["class"] == "lead"]
    assert tables
    for table in tables:
        stmt = f"DELETE FROM {table}"  # noqa: S608 - registry names
        assert re.search(rf"{stmt}\b", sql), f"no {stmt}"
        assert re.search(rf"COUNT\(\*\) FROM {table}\b", sql), (
            f"no read-back of {table}"
        )
    # Nothing the tool deletes is outside the registry's lead class.
    for deleted in re.findall(r"DELETE FROM (\w+)", sql):
        assert deleted in tables, deleted


def test_the_delete_sql_runs_against_the_real_schema() -> None:
    """Run the tool's own SQL on the migrations' tables: both lead tables end empty."""
    db = real_db()
    lead = "0123456789abcdef0123456789abcdef"
    other = "fedcba9876543210fedcba9876543210"
    for lid, mail in ((lead, "gone@example.test"), (other, "stay@example.test")):
        db.execute(
            "INSERT INTO leads (id, email, created_ts, last_seen_ts, source) "
            "VALUES (?, ?, 0, 0, 'gate')",
            (lid, mail),
        )
        db.execute(
            "INSERT INTO kit_events (ts, lead_id, kind) VALUES (3600000, ?, 'unlock')",
            (lid,),
        )
    for stmt in _sql(lead, "gone@example.test"):
        if stmt.startswith("--"):
            continue
        db.execute(stmt)
    assert db.execute(
        "SELECT COUNT(*) FROM leads WHERE id = ?", (lead,)
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT COUNT(*) FROM kit_events WHERE lead_id = ?", (lead,)
    ).fetchone() == (0,)
    assert db.execute(
        "SELECT COUNT(*) FROM leads WHERE id = ?", (other,)
    ).fetchone() == (1,)
    assert db.execute(
        "SELECT COUNT(*) FROM kit_events WHERE lead_id = ?", (other,)
    ).fetchone() == (1,)


def test_the_foreign_key_cascade_is_a_second_layer() -> None:
    """Schema backstop (#856): with foreign keys on, a deleted lead takes its events."""
    db = real_db()
    db.execute("PRAGMA foreign_keys = ON")
    db.execute(
        "INSERT INTO leads (id, email, created_ts, last_seen_ts, source) "
        "VALUES ('0123456789abcdef0123456789abcdef', 'gone@example.test', 0, 0, 'gate')"
    )
    db.execute(
        "INSERT INTO kit_events (ts, lead_id, kind) "
        "VALUES (3600000, '0123456789abcdef0123456789abcdef', 'view')"
    )
    db.execute("DELETE FROM leads WHERE id = '0123456789abcdef0123456789abcdef'")
    assert db.execute("SELECT COUNT(*) FROM kit_events").fetchone() == (0,)


def test_the_age_purge_covers_every_lead_table() -> None:
    text = LEAD_RETENTION.read_text(encoding="utf-8")
    for table, entry in load_registry()["stores"].items():
        if entry["class"] == "lead":
            statement = "DELETE FROM " + table  # noqa: S608 - registry names
            assert statement in text, table


# --- the checklist and the docs name every store -------------------------------


def test_the_checklist_and_privacy_doc_name_every_lead_and_lead_hash_store() -> None:
    privacy = PRIVACY.read_text(encoding="utf-8")
    for table, entry in load_registry()["stores"].items():
        if entry["class"] in {"lead", "lead_hash"}:
            assert table in CHECKLIST, f"checklist does not name {table}"
            assert f"`{table}`" in privacy, f"docs/privacy.md does not name {table}"


def test_the_privacy_doc_has_the_store_table_and_the_hand_clear_statement() -> None:
    privacy = PRIVACY.read_text(encoding="utf-8")
    section = privacy.split("### Every store, checked against the schema", 1)
    assert len(section) == 2, "docs/privacy.md lost the store table"
    body = section[1].split("\n## ", 1)[0]
    stores = load_registry()["stores"]
    for table, entry in stores.items():
        if entry["class"] in {"lead", "lead_hash", "unlinkable"}:
            assert f"`{table}`" in body, f"store table does not list {table}"
    aggregates = sum(1 for e in stores.values() if e["class"] == "aggregate")
    assert f"| {aggregates} other tables " in body, "the aggregate table count is stale"
    assert "DELETE FROM alert_dedup WHERE expires_at" in body
    assert "OPEN QUESTION" in body


def test_the_checklist_prints_no_statement_and_no_address() -> None:
    assert "DELETE FROM" not in CHECKLIST
    assert "@" not in CHECKLIST


# --- backups the flow produces -------------------------------------------------


def backup_tables() -> tuple[set[str], str]:
    text = BACKUP_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^tables=\((.*?)^\)", text, re.DOTALL | re.MULTILINE)
    assert m, "backup-d1.sh tables array not found"
    never = re.search(r"never exported: ([a-z_, ]+)", text)
    assert never, "backup-d1.sh no longer says what it never exports"
    return set(m.group(1).split()), never.group(1)


def test_the_backup_script_matches_the_registry() -> None:
    exported, never_line = backup_tables()
    never = {t.strip() for t in never_line.split(",")}
    for table, entry in load_registry()["stores"].items():
        if entry["class"] not in {"lead", "lead_hash", "unlinkable"}:
            continue
        if entry["backup"] == "never":
            assert table not in exported, f"{table} is exported by backup-d1.sh"
            assert table in never, f"backup-d1.sh does not say it never exports {table}"
        else:
            assert table in exported, f"{table} is registered as exported but is not"


# --- non-D1 stores ---------------------------------------------------------------


def test_no_unreviewed_storage_primitive_or_binding() -> None:
    assert (
        runtime_violations(worker_primitives(), wrangler_tables(), load_registry())
        == []
    )


def test_the_runtime_check_bites() -> None:
    registry = load_registry()
    prims = copy.deepcopy(worker_primitives())
    prims["worker/src/kit/newstore.ts"] = {"R2Bucket"}
    assert any(
        "newstore.ts: R2Bucket" in v
        for v in runtime_violations(prims, wrangler_tables(), registry)
    )
    gone = copy.deepcopy(worker_primitives())
    gone.pop("worker/src/live.ts")
    assert any(
        "live.ts but the source no longer uses" in v
        for v in runtime_violations(gone, wrangler_tables(), registry)
    )
    tables = wrangler_tables() | {"durable_objects"}
    assert any(
        "[durable_objects]" in v
        for v in runtime_violations(worker_primitives(), tables, registry)
    )


def test_the_scan_sees_the_known_primitives() -> None:
    found = worker_primitives()
    assert "put(" in found["worker/src/live.ts"]
    assert "KVNamespace" in found["worker/src/kit/env.ts"]
    assert {"kv_namespaces", "d1_databases"} <= wrangler_tables()


@pytest.mark.parametrize(
    "name", ["email", "lead_id", "event_hash", "user_agent", "session_id", "ip_hash"]
)
def test_the_name_pattern_catches_personal_looking_columns(name: str) -> None:
    assert PERSON_NAME.search(name), name
