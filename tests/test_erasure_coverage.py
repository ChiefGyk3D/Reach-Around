# SPDX-License-Identifier: Apache-2.0
"""Erasure coverage (#856): every store that can hold lead data is classified.

The schema comes from running the Worker migrations in order in an in-memory
SQLite database and reading sqlite_master, table_info and foreign_key_list (the
migration text is never rewritten or pattern-matched, so comments, string
literals and CHECK text cannot hide an object). scripts/erasure-stores.json is
the registry: every table, view and trigger must be classified, every column
that can hold person-level data (by name, declared type or default) needs its
own recorded reason, and the recorded column lists and the schema digest must
match the live schema, so a change fails here until the entry is looked at
again. What this test cannot do is prove that a person looked: a second human
reviewer on changes to the registry is a policy control (docs/privacy.md says
so). It also cannot follow data flow: the Worker storage scan is lexical.
Synthetic data only; no network.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from reach_around.commands.leads import CHECKLIST, _sql

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "worker" / "migrations").glob("*.sql"))
REGISTRY_FILE = ROOT / "scripts" / "erasure-stores.json"
PRIVACY = ROOT / "docs" / "privacy.md"
ALERTS_DOC = ROOT / "docs" / "alerts.md"
BACKUP_SCRIPT = ROOT / "scripts" / "backup-d1.sh"
WORKER_SRC = ROOT / "worker" / "src"
LEAD_RETENTION = WORKER_SRC / "kit" / "leadretention.ts"

CLASSES = {"lead", "lead_hash", "unlinkable", "aggregate", "internal"}
# Tables of these classes are exempt from the per-column vocabulary check: the
# first holds the person's data on purpose, the second only keyed hashes.
PERSON_CLASSES = {"lead", "lead_hash"}
MIN_REASON = 12

# A column whose name suggests a person, a device, a place, a session, a hashed
# identifier or free-form content. In a table that is not class lead or lead_hash
# each such column needs its own reason in reviewed_columns.
PERSONAL_COLUMN = re.compile(
    r"mail|name|phone|addr|(^|_)ip($|_)|(^|_)ua($|_)|agent|ident|sess|token|hash|hmac"
    r"|text|message|(^|_)msg($|_)|body|subject|note|referr|city|region|country"
    r"|(^|_)(lat|lon|lng)($|_)|payload|contact|metadata|content|blob|raw|json|comment"
    r"|detail|inquir|visitor|user|lead|cookie|^h$|brand|lang|browser|device|^os$"
    r"|^vp$|^net$|src|value",
    re.IGNORECASE,
)

# What a Worker source file can use to store something outside D1. Lexical by
# nature: it names the primitive in any spelling that still has to mention it
# (a method reference or a bind of it counts, a computed property name counts).
PRIMITIVES = {
    "put": re.compile(r"\.put\b"),
    "caches": re.compile(r"\bcaches\b"),
    "KVNamespace": re.compile(r"\bKVNamespace\b"),
    "R2Bucket": re.compile(r"\bR2Bucket\b"),
    "DurableObject": re.compile(r"\bDurableObject"),
    "Queue": re.compile(r"\bQueues?\b|\.sendBatch\b"),
    "AnalyticsEngineDataset": re.compile(
        r"\bAnalyticsEngineDataset\b|\bwriteDataPoint\b"
    ),
    "Vectorize": re.compile(r"\bVectorize"),
    "Hyperdrive": re.compile(r"\bHyperdrive\b"),
    "Fetcher": re.compile(r"\bFetcher\b"),
    "computed-property": re.compile(
        r"\[\s*[\"'`](?:put|send|sendBatch|writeDataPoint|upsert|insert)[\"'`]\s*\]"
    ),
    "dynamic-env": re.compile(r"\benv\s*\["),
}
# Wrangler sections whose bindings are not storage the Worker writes to (D1 is
# covered by the schema, a rate limiter keeps only a count per key).
NON_STORAGE_SECTIONS = {"d1_databases", "ratelimits", "vars", "routes", "triggers"}
WRANGLER_GLOBS = ("wrangler*.toml", "wrangler*.json", "wrangler*.jsonc")
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv"}

# Commands that read a database out: a wrangler D1 call or a dump tool.
EXPORT_COMMAND = re.compile(
    r"\bd1\s+(?:export|execute|time-travel)\b|\bpg_dump\b"
    r"|\bsqlite3\s+\S+\s+\.(?:dump|backup)\b"
)
# A default that is a plain literal; anything else (a call, an expression) is reviewed.
LITERAL_DEFAULT = re.compile(
    r"'(?:[^']|'')*'|[-+]?\d+(?:\.\d+)?|NULL|TRUE|FALSE"
    r"|CURRENT_(?:TIMESTAMP|DATE|TIME)",
    re.IGNORECASE,
)
LISTING_SQL = "SELECT name FROM sqlite_master WHERE type = 'table'"


# --- the schema, from a real SQLite -------------------------------------------


def build_db(migrations: list[tuple[str, str]]) -> sqlite3.Connection:
    """Run the migrations in order. A migration SQLite cannot run is a failure
    that names it; nothing is rewritten to make it run."""
    db = sqlite3.connect(":memory:")
    for name, sql in migrations:
        try:
            db.executescript(sql)
        except sqlite3.Error as exc:
            raise AssertionError(
                f"migration {name} does not run on SQLite "
                f"{sqlite3.sqlite_version}: {exc}"
            ) from exc
    return db


def real_db() -> sqlite3.Connection:
    return build_db([(m.name, m.read_text(encoding="utf-8")) for m in MIGRATIONS])


def live_schema(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """table -> columns, types, defaults and foreign keys, as SQLite sees them."""
    out: dict[str, dict[str, Any]] = {}
    for (name,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ):
        info = db.execute(f"PRAGMA table_info('{name}')").fetchall()  # noqa: S608
        fks = db.execute(f"PRAGMA foreign_key_list('{name}')").fetchall()  # noqa: S608
        out[name] = {
            "columns": [c[1] for c in info],
            "types": {c[1]: c[2] for c in info},
            "defaults": {c[1]: c[4] for c in info},
            "fk_parents": [r[2] for r in fks],
            "digest_rows": [list(c) for c in info] + [list(r) for r in fks],
        }
    return out


def live_objects(db: sqlite3.Connection) -> dict[str, list[str]]:
    """The views and triggers the migrations leave behind (they can expose rows)."""
    return {
        kind: sorted(
            n
            for (n,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type = ?", (kind,)
            )
        )
        for kind in ("view", "trigger")
    }


def schema_digest(
    schema: dict[str, dict[str, Any]], objects: dict[str, list[str]]
) -> str:
    """One hash of every table's columns, types, defaults and keys, and the views and
    triggers. It changes when any of them does, including a changed type or default."""
    body = {t: schema[t]["digest_rows"] for t in sorted(schema)}
    text = json.dumps([body, objects], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def load_registry() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return data


def review_reasons(name: str, ctype: str | None, default: str | None) -> list[str]:
    """Why a column cannot be waved through on its table's class alone."""
    why: list[str] = []
    if PERSONAL_COLUMN.search(name):
        why.append("looks personal")
    if not (ctype or "").strip() or (ctype or "").strip().upper() == "BLOB":
        why.append("declared type holds anything (blob or none)")
    if default is not None and not LITERAL_DEFAULT.fullmatch(str(default).strip()):
        why.append("default is an expression")
    return why


def schema_violations(
    schema: dict[str, dict[str, Any]],
    registry: dict[str, Any],
    objects: dict[str, list[str]] | None = None,
) -> list[str]:
    """Content checks: what a table holds, whatever the registry says about its name."""
    stores: dict[str, Any] = registry["stores"]
    found: list[str] = []
    for kind, names in (objects or {}).items():
        listed = set(registry.get("other_objects", {}).get(kind, []))
        for n in names:
            if n not in listed:
                found.append(
                    f"unreviewed {kind} {n}: a {kind} can copy or expose rows; "
                    "list it under other_objects with a reason"
                )
    for table in sorted(set(schema) - set(stores)):
        found.append(f"unreviewed table {table}: add it to scripts/erasure-stores.json")
    for table in sorted(set(stores) - set(schema)):
        found.append(f"registry lists {table} but no migration creates it")
    for table in sorted(set(schema) & set(stores)):
        entry = stores[table]
        info = schema[table]
        cols = info["columns"]
        if entry.get("class") not in CLASSES:
            found.append(f"{table}: class must be one of {sorted(CLASSES)}")
            continue
        if "leads" in info["fk_parents"] and entry["class"] != "lead":
            found.append(f"{table}: references leads but is not class lead")
        if "lead_id" in cols and entry["class"] != "lead":
            found.append(f"{table}: has a lead_id column but is not class lead")
        if "email" in cols and entry["class"] != "lead":
            found.append(f"{table}: has an email column but is not class lead")
        reviewed = entry.get("reviewed_columns", {})
        if entry["class"] not in PERSON_CLASSES:
            for col in cols:
                why = review_reasons(
                    col,
                    info.get("types", {}).get(col),
                    info.get("defaults", {}).get(col),
                )
                if why and len(str(reviewed.get(col, "")).strip()) < MIN_REASON:
                    found.append(
                        f"{table}.{col}: {'; '.join(why)}; review it and list it in "
                        "reviewed_columns with a reason, or class the table lead"
                    )
        for col in reviewed:
            if col not in cols:
                found.append(f"{table}.{col}: reviewed but not a column")
        if entry["class"] in {"lead", "lead_hash", "unlinkable"} and entry.get(
            "backup"
        ) not in {"never", "exported"}:
            found.append(f"{table}: backup must be never or exported")
    return found


def review_violations(
    schema: dict[str, dict[str, Any]],
    registry: dict[str, Any],
    objects: dict[str, list[str]],
) -> list[str]:
    """The registry's own record: every entry says why, records the exact column
    list it was judged on, and the whole schema still matches the reviewed digest."""
    found: list[str] = []
    for table, entry in sorted(registry["stores"].items()):
        if len(str(entry.get("reason", "")).strip()) < MIN_REASON:
            found.append(
                f"{table}: a reason of at least {MIN_REASON} characters is required"
            )
        judged = entry.get("columns")
        if not isinstance(judged, list) or not all(isinstance(c, str) for c in judged):
            found.append(
                f"{table}: columns must record the list the entry was judged on"
            )
        elif table in schema and judged != schema[table]["columns"]:
            changed = sorted(set(schema[table]["columns"]) ^ set(judged))
            found.append(
                f"{table}: columns differ from the migrations "
                f"(changed since reviewed: {changed})"
            )
        for col, why in entry.get("reviewed_columns", {}).items():
            if len(str(why).strip()) < MIN_REASON:
                found.append(f"{table}.{col}: a reviewed column needs a real reason")
    for kind, listed in registry.get("other_objects", {}).items():
        for obj, why in listed.items():
            if len(str(why).strip()) < MIN_REASON:
                found.append(
                    f"{kind} {obj}: a reason of {MIN_REASON}+ characters is required"
                )
    want = schema_digest(schema, objects)
    if registry.get("reviewed_schema_digest") != want:
        found.append(
            "the schema changed since the registry was reviewed (a type, default, key, "
            f"view or trigger); after review set reviewed_schema_digest to {want}"
        )
    return found


# --- the Worker's other stores ----------------------------------------------------


def strip_jsonc(text: str) -> str:
    """Drop comments and trailing commas outside string literals."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            in_str = c != '"'
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated block comment")
            i = end + 2
            continue
        else:
            out.append(c)
        i += 1
    plain = "".join(out)
    result: list[str] = []
    in_str = False
    for j, c in enumerate(plain):
        if in_str:
            result.append(c)
            in_str = not (c == '"' and plain[j - 1] != "\\")
            continue
        if c == '"':
            in_str = True
        if c == "," and re.match(r"\s*[}\]]", plain[j + 1 :]):
            continue
        result.append(c)
    return "".join(result)


def wrangler_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in WRANGLER_GLOBS:
        for path in root.rglob(pattern):
            if not SKIP_DIRS & set(path.relative_to(root).parts):
                found.append(path)
    return sorted(set(found))


def _json_sections(data: dict[str, Any]) -> tuple[set[str], set[str]]:
    sections: set[str] = set()
    names: set[str] = set()

    def walk(node: Any, collecting: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if collecting and key in {"binding", "name"} and isinstance(value, str):
                    names.add(value)
                walk(value, collecting)
        elif isinstance(node, list):
            for item in node:
                walk(item, collecting)

    def visit(block: dict[str, Any]) -> None:
        for key, value in block.items():
            if key == "env" and isinstance(value, dict):
                for sub in value.values():
                    if isinstance(sub, dict):
                        visit(sub)
            elif isinstance(value, (dict, list)):
                sections.add(key)
                walk(value, key not in NON_STORAGE_SECTIONS)

    visit(data)
    return sections, names


def _toml_sections(text: str) -> tuple[set[str], set[str]]:
    """Sections the parser sees, plus commented template sections and binding names
    (the templates are what a deployment turns on)."""
    data = tomllib.loads(text)
    sections, names = _json_sections(data)
    current = ""
    for line in text.splitlines():
        header = re.match(r"#?\s*\[{1,2}([A-Za-z0-9_]+)", line)
        if header:
            current = header.group(1)
            sections.add(current)
            continue
        m = re.match(r"#?\s*(?:binding|name)\s*=\s*\"([A-Za-z0-9_]+)\"", line)
        if m and current and current not in NON_STORAGE_SECTIONS:
            names.add(m.group(1))
    return sections, names


def wrangler_config(root: Path) -> tuple[set[str], set[str]]:
    """(sections, storage binding names) over every wrangler config file."""
    sections: set[str] = set()
    names: set[str] = set()
    for path in wrangler_files(root):
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".toml":
            s, n = _toml_sections(text)
        else:
            s, n = _json_sections(json.loads(strip_jsonc(text)))
        sections |= s
        names |= n
    return sections, names


def worker_primitives(
    src: Path = WORKER_SRC, binding_names: set[str] | None = None, root: Path = ROOT
) -> dict[str, set[str]]:
    """file -> storage primitives its source mentions, for every file in worker/src
    (any extension), plus each configured storage binding by name."""
    names = binding_names if binding_names is not None else wrangler_config(root)[1]
    rx = dict(PRIMITIVES)
    for name in sorted(names):
        rx[f"binding:{name}"] = re.compile(rf"\b{re.escape(name)}\b")
    out: dict[str, set[str]] = {}
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        hits = {name for name, pattern in rx.items() if pattern.search(text)}
        if hits:
            out[path.relative_to(root).as_posix()] = hits
    return out


def runtime_violations(
    primitives: dict[str, set[str]], sections: set[str], registry: dict[str, Any]
) -> list[str]:
    reviewed: dict[str, dict[str, str]] = registry["runtime_stores"]["primitives"]
    found: list[str] = []
    for path, names in primitives.items():
        for name in sorted(names):
            if len(str(reviewed.get(path, {}).get(name, "")).strip()) < MIN_REASON:
                found.append(
                    f"{path}: {name} is a storage primitive with no reviewed reason"
                )
    for path, entry in reviewed.items():
        for name in entry:
            if name not in primitives.get(path, set()):
                found.append(
                    f"registry lists {name} in {path} but the source no longer uses it"
                )
    for section in sorted(
        sections - set(registry["runtime_stores"]["wrangler_tables"])
    ):
        found.append(f"a wrangler config has an unreviewed binding section [{section}]")
    return found


# --- every way the data can be read out -------------------------------------------


def export_scan(root: Path = ROOT) -> dict[str, set[str]]:
    """file -> export-capable commands it contains (scripts, CI, builds, the CLI)."""
    paths: list[Path] = []
    for sub, patterns in (
        ("scripts", ("*",)),
        (".github", ("*",)),
        ("deploy", ("*",)),
        ("src/reach_around", ("*.py",)),
    ):
        base = root / sub
        if base.is_dir():
            for pattern in patterns:
                paths += [
                    p
                    for p in base.rglob(pattern)
                    if p.is_file()
                    and p.suffix != ".json"
                    and not SKIP_DIRS & set(p.relative_to(root).parts)
                ]
    for single in ("Makefile", "worker/package.json"):
        if (root / single).is_file():
            paths.append(root / single)
    out: dict[str, set[str]] = {}
    for path in sorted(set(paths)):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = {m.group(0) for m in EXPORT_COMMAND.finditer(text)}
        if hits:
            out[path.relative_to(root).as_posix()] = hits
    return out


def whole_database_export_docs(root: Path = ROOT) -> set[str]:
    """Docs showing a `wrangler d1 export` with no --table: it dumps every table."""
    found: set[str] = set()
    for path in (root / "docs").rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(
            r"wrangler d1 export +([^`\s][^`\n]*(?:\n[^`\n]*)?)", text
        ):
            if "--table" not in m.group(1):
                found.add(path.relative_to(root).as_posix())
    return found


def export_inventory_violations(
    scan: dict[str, set[str]], docs: set[str], registry: dict[str, Any]
) -> list[str]:
    reg = registry["export_paths"]
    found: list[str] = []
    for path in sorted(set(scan) - set(reg["commands"])):
        found.append(
            f"{path}: contains {sorted(scan[path])} but is not in export_paths"
        )
    for path in sorted(set(reg["commands"]) - set(scan)):
        found.append(f"export_paths lists {path} but it has no export command any more")
    for path in sorted(docs - set(reg["whole_database_docs"])):
        found.append(f"{path}: shows a whole-database export; list it in export_paths")
    for path in sorted(set(reg["whole_database_docs"]) - docs):
        found.append(f"export_paths lists {path} but it shows no whole-database export")
    for section in ("commands", "whole_database_docs"):
        for path, why in reg[section].items():
            if len(str(why).strip()) < MIN_REASON:
                found.append(f"export_paths {path}: a reason is required")
    return found


def backup_call_violations(
    calls: list[list[str]], registry: dict[str, Any]
) -> list[str]:
    """Judge what a backup run actually asked wrangler to do."""
    stores: dict[str, Any] = registry["stores"]
    extra = set(registry["export_paths"]["backup_extra_tables"])
    found: list[str] = []
    for argv in calls:
        if argv[:2] == ["d1", "execute"]:
            sql = argv[argv.index("--command") + 1] if "--command" in argv else ""
            if sql != LISTING_SQL:
                found.append(
                    f"backup ran a query other than the table listing: {sql[:40]}"
                )
        elif argv[:2] == ["d1", "export"]:
            tables = [a.split("=", 1)[1] for a in argv if a.startswith("--table=")]
            if len(tables) != 1:
                found.append("backup ran an export that is not one named table")
                continue
            table = tables[0]
            entry = stores.get(table)
            if table in extra:
                continue
            if entry is None:
                found.append(
                    f"backup exported {table}, which the registry does not list"
                )
            elif (
                entry["class"] in {"lead", "internal"} or entry.get("backup") == "never"
            ):
                found.append(
                    f"backup exported {table}, which the registry says never to export"
                )
        else:
            found.append(f"backup ran an unexpected wrangler call: {argv[:2]}")
    return found


# --- the real repository -------------------------------------------------------


def test_the_migrations_are_collected_and_run_unchanged() -> None:
    assert MIGRATIONS, "no migrations collected"
    db = real_db()
    assert db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] > 10


def test_every_table_and_column_is_classified_by_content() -> None:
    db = real_db()
    found = schema_violations(live_schema(db), load_registry(), live_objects(db))
    assert found == []


def test_the_registry_record_matches_the_live_schema() -> None:
    db = real_db()
    found = review_violations(live_schema(db), load_registry(), live_objects(db))
    assert found == []


def test_lead_tables_are_what_the_foreign_keys_say() -> None:
    schema = live_schema(real_db())
    registry = load_registry()
    lead_tables = {t for t, e in registry["stores"].items() if e["class"] == "lead"}
    linked = {t for t, s in schema.items() if "leads" in s["fk_parents"]} | {"leads"}
    assert lead_tables == linked == {"leads", "kit_events"}


# --- hostile migrations through the real collection path ------------------------

HOSTILE = [
    (
        "0001_hostile.sql",
        """
-- CHECK (
CREATE TABLE inquiry_archive (
  id INTEGER PRIMARY KEY,
  email TEXT
);
-- )
/* CHECK ( CREATE TABLE in_a_block_comment (id INTEGER); ) */
CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT);
CREATE TABLE notes_log (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL DEFAULT 'a (b); CHECK (c)' CHECK (label <> 'x(;'),
  scratch TEXT
);
INSERT INTO notes_log (label) VALUES ('semi; ) paren ( CREATE TABLE fake (id INT);');
ALTER TABLE notes_log ADD COLUMN added_later TEXT;
ALTER TABLE notes_log DROP COLUMN scratch;
CREATE VIEW everyone AS SELECT email FROM leads;
CREATE TRIGGER copy_email AFTER INSERT ON leads
BEGIN
  INSERT INTO notes_log (label) VALUES (NEW.email);
END;
""",
    )
]


def fixture_registry(
    schema: dict[str, dict[str, Any]],
    classes: dict[str, str],
    reviewed: dict[str, dict[str, str]] | None = None,
    objects: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """A synthetic registry whose lists are exactly what the schema holds."""
    stores = {
        t: {
            "class": classes.get(t, "aggregate"),
            "reason": "synthetic fixture reason text",
            "backup": "never",
            "columns": list(info["columns"]),
            "reviewed_columns": (reviewed or {}).get(t, {}),
        }
        for t, info in schema.items()
    }
    return {
        "stores": stores,
        "other_objects": objects or {},
        "reviewed_schema_digest": "",
    }


def test_the_real_parser_sees_through_comments_literals_alters_views_and_triggers() -> (
    None
):
    db = build_db(HOSTILE)
    schema = live_schema(db)
    # A table created right after a comment that contains CHECK text is seen.
    assert schema["inquiry_archive"]["columns"] == ["id", "email"]
    # Objects named only inside comments or string literals do not exist.
    assert "in_a_block_comment" not in schema and "fake" not in schema
    # ALTER ADD COLUMN and DROP COLUMN are applied; a CHECK and a default with
    # parentheses inside quotes do not confuse anything.
    assert schema["notes_log"]["columns"] == ["id", "label", "added_later"]
    assert live_objects(db) == {"view": ["everyone"], "trigger": ["copy_email"]}
    registry = fixture_registry(schema, {"leads": "lead"})
    found = schema_violations(schema, registry, live_objects(db))
    assert any("unreviewed view everyone" in v for v in found)
    assert any("unreviewed trigger copy_email" in v for v in found)
    assert any(v.startswith("inquiry_archive: has an email column") for v in found)
    # Listing the objects with a reason, and classing the archive lead, clears it.
    ok = fixture_registry(
        schema,
        {"leads": "lead", "inquiry_archive": "lead"},
        reviewed={
            "notes_log": {
                "label": "synthetic closed label",
                "added_later": "synthetic: holds nothing personal",
            }
        },
        objects={"view": ["everyone"], "trigger": ["copy_email"]},
    )
    assert schema_violations(schema, ok, live_objects(db)) == []


def test_a_migration_sqlite_cannot_run_fails_by_name() -> None:
    with pytest.raises(AssertionError, match=r"0002_bad\.sql does not run"):
        build_db([("0002_bad.sql", "CREATE TABLE ((;")])


@pytest.mark.parametrize(
    "name",
    ["payload", "body", "identity", "contact", "metadata", "user_agent", "session_id"],
)
def test_a_generic_column_in_a_bookkeeping_table_fails_until_reviewed(
    name: str,
) -> None:
    db = build_db(
        [("0001.sql", f"CREATE TABLE messages (id INTEGER PRIMARY KEY, {name} TEXT);")]
    )
    schema = live_schema(db)
    sneaky = fixture_registry(schema, {"messages": "aggregate"})
    assert any(
        f"messages.{name}: looks personal" in v
        for v in schema_violations(schema, sneaky)
    )
    blank = fixture_registry(
        schema, {"messages": "aggregate"}, reviewed={"messages": {name: "  "}}
    )
    assert any(f"messages.{name}" in v for v in schema_violations(schema, blank))
    reviewed = fixture_registry(
        schema,
        {"messages": "aggregate"},
        reviewed={"messages": {name: "synthetic: a closed label, no person"}},
    )
    assert schema_violations(schema, reviewed) == []


def test_a_blob_or_untyped_column_and_an_expression_default_need_a_reason() -> None:
    db = build_db(
        [
            (
                "0001.sql",
                "CREATE TABLE stash (id INTEGER PRIMARY KEY, a BLOB, b, "
                "c INTEGER DEFAULT (strftime('%s','now')));",
            )
        ]
    )
    schema = live_schema(db)
    found = schema_violations(schema, fixture_registry(schema, {"stash": "aggregate"}))
    assert any("stash.a: declared type holds anything" in v for v in found)
    assert any("stash.b: declared type holds anything" in v for v in found)
    assert any("stash.c: default is an expression" in v for v in found)


def test_a_lead_link_forces_the_lead_class_in_a_real_database() -> None:
    db = build_db(
        [
            (
                "0001.sql",
                "CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT);"
                "CREATE TABLE lead_notes (id INTEGER PRIMARY KEY, "
                "lead_id TEXT REFERENCES leads (id));",
            )
        ]
    )
    schema = live_schema(db)
    assert schema["lead_notes"]["fk_parents"] == ["leads"]
    registry = fixture_registry(
        schema,
        {"leads": "lead", "lead_notes": "aggregate"},
        reviewed={"lead_notes": {"lead_id": "claimed harmless but it is not"}},
    )
    found = schema_violations(schema, registry)
    assert any("lead_notes: references leads" in v for v in found)
    assert any("lead_notes: has a lead_id column" in v for v in found)


def test_the_registry_record_check_bites() -> None:
    db = real_db()
    schema, objects = live_schema(db), live_objects(db)
    registry = load_registry()

    blank = copy.deepcopy(registry)
    blank["stores"]["leads"]["reason"] = " "
    assert any(
        "leads: a reason of at least" in v
        for v in review_violations(schema, blank, objects)
    )

    missing = copy.deepcopy(registry)
    del missing["stores"]["counters"]["columns"]
    assert any(
        "counters: columns must record" in v
        for v in review_violations(schema, missing, objects)
    )

    stale = copy.deepcopy(registry)
    stale["stores"]["counters"]["columns"].append("session_id")
    assert any(
        v.startswith("counters: columns differ")
        for v in review_violations(schema, stale, objects)
    )

    thin = copy.deepcopy(registry)
    thin["stores"]["view_events"]["reviewed_columns"]["src"] = "ok"
    assert any(
        "view_events.src: a reviewed column needs a real reason" in v
        for v in review_violations(schema, thin, objects)
    )

    # A changed type or default keeps the column list the same; the digest sees it.
    changed = real_db()
    changed.execute("CREATE TABLE extra_probe (a INTEGER)")
    drifted = review_violations(live_schema(changed), registry, live_objects(changed))
    assert any(
        "the schema changed since the registry was reviewed" in v for v in drifted
    )

    retyped = build_db([("0001.sql", "CREATE TABLE t (a TEXT);")])
    retyped2 = build_db([("0001.sql", "CREATE TABLE t (a TEXT DEFAULT 'x');")])
    assert schema_digest(live_schema(retyped), live_objects(retyped)) != schema_digest(
        live_schema(retyped2), live_objects(retyped2)
    )


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
    assert "policy control" in body, (
        "the docs must say the registry review is a policy control"
    )


def test_the_checklist_prints_no_statement_and_no_address() -> None:
    assert "DELETE FROM" not in CHECKLIST
    assert "@" not in CHECKLIST


# --- the alert hash lifetime, as the code really behaves -------------------------


def test_the_alert_hash_claim_is_what_the_code_does() -> None:
    """Rows stop counting as repeats after ten minutes, but nothing deletes them on a
    timer: the only prune is on an admitted alert event (or by hand). The docs must
    say so, and a timer prune added later must change them."""
    callers: dict[str, set[str]] = {}
    for path in sorted(WORKER_SRC.rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        hits = {
            s
            for s in ("pruneAlertState(", "claimDedup(", "FROM alert_dedup")
            if s in text
        }
        if hits:
            callers[path.relative_to(WORKER_SRC).as_posix()] = hits
    assert set(callers) == {"alerts/dispatcher.ts", "alerts/quota.ts"}, callers
    index = (WORKER_SRC / "index.ts").read_text(encoding="utf-8")
    assert "pruneAlertState" not in index and "alert_dedup" not in index

    claims = {
        "docs/privacy.md": PRIVACY.read_text(encoding="utf-8"),
        "docs/alerts.md": ALERTS_DOC.read_text(encoding="utf-8"),
        "checklist": CHECKLIST,
        "registry": json.dumps(load_registry()["stores"]["alert_dedup"]),
    }
    for where, text in claims.items():
        flat = " ".join(text.split())
        assert not re.search(
            r"(held|for|lives?|life) (for )?at most (10|ten) minutes", flat
        ), where
        assert "next alert" in flat or "next admitted" in flat, where
    assert "10 minutes" in " ".join(PRIVACY.read_text(encoding="utf-8").split())


# --- backups the flow produces -------------------------------------------------


def backup_tables() -> tuple[set[str], str]:
    text = BACKUP_SCRIPT.read_text(encoding="utf-8")
    m = re.search(r"^tables=\((.*?)^\)", text, re.DOTALL | re.MULTILINE)
    assert m, "backup-d1.sh tables array not found"
    never = re.search(r"never exported: ([a-z_, ]+)", text)
    assert never, "backup-d1.sh no longer says what it never exports"
    return set(m.group(1).split()), never.group(1)


def test_the_backup_script_declaration_matches_the_registry() -> None:
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


def test_every_export_command_in_the_repository_is_inventoried() -> None:
    found = export_inventory_violations(
        export_scan(), whole_database_export_docs(), load_registry()
    )
    assert found == []


def test_the_export_inventory_bites_on_a_new_path(tmp_path: Path) -> None:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "scripts" / "dump-all.sh").write_text(
        'npx wrangler d1 execute "$db" --remote --command "SELECT * FROM leads"\n'
    )
    (tmp_path / "docs" / "tip.md").write_text(
        "Run `npx wrangler d1 export mydb --remote --output all.sql` first.\n"
    )
    (tmp_path / "docs" / "ok.md").write_text(
        "Run `npx wrangler d1 export mydb --remote --table=t --output t.sql`.\n"
    )
    found = export_inventory_violations(
        export_scan(tmp_path), whole_database_export_docs(tmp_path), load_registry()
    )
    assert any("scripts/dump-all.sh" in v and "not in export_paths" in v for v in found)
    assert any("docs/tip.md" in v and "whole-database export" in v for v in found)
    assert not any("docs/ok.md" in v for v in found)


def _recording_env(tmp_path: Path, env: dict[str, str]) -> tuple[dict[str, str], Path]:
    log = tmp_path / "calls.jsonl"
    recorder = tmp_path / "wrangler-recorder"
    recorder.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        'with open(os.environ["STUB_LOG"], "a") as fh:\n'
        "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        'real = os.environ["STUB_REAL"]\n'
        "os.execv(sys.executable, [sys.executable, real, *sys.argv[1:]])\n"
    )
    recorder.chmod(0o700)
    env = dict(env)
    env["STUB_REAL"] = env["WRANGLER"]
    env["WRANGLER"] = str(recorder)
    env["STUB_LOG"] = str(log)
    return env, log


def _run_backup(tmp_path: Path, script: Path) -> tuple[list[list[str]], Path]:
    import subprocess

    from test_backup_d1 import dest, make

    env, log = _recording_env(tmp_path, make(tmp_path))
    out = dest(tmp_path)
    subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        ["/usr/bin/bash", str(script), "db", str(out), "--apply"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    calls = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return calls, out


_W = '"${wrangler[@]}" d1 '


def test_a_real_backup_run_asks_wrangler_for_nothing_it_should_not(
    tmp_path: Path,
) -> None:
    calls, out = _run_backup(tmp_path, BACKUP_SCRIPT)
    assert [c[:2] for c in calls].count(["d1", "export"]) > 5
    assert backup_call_violations(calls, load_registry()) == []
    text = "".join(p.read_text() for p in out.rglob("*.sql"))
    assert "person@example.test" not in text
    assert not list(out.rglob("leads.sql")) and not list(out.rglob("kit_events.sql"))


@pytest.mark.parametrize(
    "hostile",
    [
        _W  # noqa: S608 - synthetic shell text, no query is built
        + 'execute "$db" --remote --command "SELECT * FROM leads" >/dev/null\n',
        _W + 'export "$db" --remote --table=leads --output="$out/x" >/dev/null\n',
        _W + 'export "$db" --remote --output="$out/all.sql" >/dev/null\n',
    ],
)
def test_a_backup_script_with_a_second_export_path_is_caught(
    tmp_path: Path, hostile: str
) -> None:
    text = BACKUP_SCRIPT.read_text(encoding="utf-8")
    marker = 'mkdir "$out"\n'
    assert marker in text
    copy_dir = tmp_path / "scripts"
    copy_dir.mkdir()
    for name in ("check-dump.py", "dump-schema.json"):
        (copy_dir / name).write_text(
            (ROOT / "scripts" / name).read_text(encoding="utf-8")
        )
    script = copy_dir / "backup-d1.sh"
    script.write_text(text.replace(marker, marker + hostile, 1))
    work = tmp_path / "run"
    work.mkdir()
    calls, _ = _run_backup(work, script)
    assert backup_call_violations(calls, load_registry()) != []


# --- non-D1 stores ---------------------------------------------------------------


def test_no_unreviewed_storage_primitive_or_binding() -> None:
    sections, names = wrangler_config(ROOT)
    assert (
        runtime_violations(
            worker_primitives(binding_names=names), sections, load_registry()
        )
        == []
    )


def test_the_scan_sees_the_known_primitives() -> None:
    sections, names = wrangler_config(ROOT)
    found = worker_primitives(binding_names=names)
    assert "put" in found["worker/src/live.ts"]
    assert "KVNamespace" in found["worker/src/kit/env.ts"]
    assert {"kv_namespaces", "d1_databases"} <= sections
    assert {"LIVE_KV", "KIT"} <= names


def test_the_scan_catches_bypasses_in_any_file_type_and_any_config_format(
    tmp_path: Path,
) -> None:
    src = tmp_path / "worker" / "src"
    src.mkdir(parents=True)
    (src / "store.mjs").write_text("const save = env.KV.put.bind(env.KV);\n")
    (src / "c.js").write_text('await env.KV["put"]("lead", email);\n')
    (src / "w.tsx").write_text("export const w = (e) => e.BLOB.get('x');\n")
    (src / "q.cjs").write_text("await env.JOBS.sendBatch([]);\n")
    (src / "e.ts").write_text("env[name].writeDataPoint({});\n")
    (src / "svc.ts").write_text("const s: Fetcher = env.STORE_SVC;\n")
    (tmp_path / "worker" / "wrangler.jsonc").write_text(
        """{
  // comment with "quotes" and CHECK (
  "name": "w",
  "r2_buckets": [{ "binding": "BLOB", "bucket_name": "b", },],
  "queues": { "producers": [{ "binding": "JOBS", "queue": "q" }] },
  "env": { "prod": { "durable_objects": { "bindings": [{ "name": "ROOM" }] } } },
  /* block */
  "vars": { "A": "b // not a comment" }
}
"""
    )
    (tmp_path / "wrangler.dev.toml").write_text(
        'name = "x"\n[[hyperdrive]]\nbinding = "PG"\n'
        '[[services]]\nbinding = "STORE_SVC"\n'
    )
    sections, names = wrangler_config(tmp_path)
    assert {
        "r2_buckets",
        "queues",
        "durable_objects",
        "vars",
        "hyperdrive",
        "services",
    } <= sections
    assert {"BLOB", "JOBS", "ROOM", "PG", "STORE_SVC"} <= names
    found = worker_primitives(src, names, tmp_path)
    assert "put" in found["worker/src/store.mjs"]
    assert "computed-property" in found["worker/src/c.js"]
    assert "binding:BLOB" in found["worker/src/w.tsx"]
    assert {"Queue", "binding:JOBS"} <= found["worker/src/q.cjs"]
    assert {"dynamic-env", "AnalyticsEngineDataset"} <= found["worker/src/e.ts"]
    assert {"Fetcher", "binding:STORE_SVC"} <= found["worker/src/svc.ts"]
    registry = {
        "runtime_stores": {"primitives": {}, "wrangler_tables": ["vars"]},
    }
    problems = runtime_violations(found, sections, registry)
    assert any("store.mjs: put" in v for v in problems)
    assert any("unreviewed binding section [r2_buckets]" in v for v in problems)


def test_the_jsonc_reader_keeps_strings_and_drops_comments() -> None:
    text = '{"a": "x // y", /* c */ "b": [1, 2,], // tail\n}'
    assert json.loads(strip_jsonc(text)) == {"a": "x // y", "b": [1, 2]}


def test_the_runtime_check_bites_on_the_real_registry() -> None:
    registry = load_registry()
    sections, names = wrangler_config(ROOT)
    real = worker_primitives(binding_names=names)
    prims = copy.deepcopy(real)
    prims["worker/src/kit/newstore.ts"] = {"R2Bucket"}
    assert any(
        "newstore.ts: R2Bucket" in v
        for v in runtime_violations(prims, sections, registry)
    )
    gone = copy.deepcopy(real)
    gone.pop("worker/src/live.ts")
    assert any(
        "live.ts but the source no longer uses" in v
        for v in runtime_violations(gone, sections, registry)
    )
