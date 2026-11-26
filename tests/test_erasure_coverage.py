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
so); the registry digest only shows that its records were recomputed together.
It also cannot follow data flow: the Worker storage scan is lexical (it pins each
statement that names a storage primitive, not what the data does next). The age
purge and the alert dedup checks run the SQL statements taken from the Worker
source against the migrated schema with a controlled clock; they do not execute
the TypeScript, which worker/test (vitest) does. Synthetic data only; no network.
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


def strip_checks(sql: str) -> str:
    """Remove every CHECK (...) clause, with a small state machine instead of a
    pattern: line and block comments, single, double and backtick quoted text and
    [bracket] names are copied through untouched, so CHECK text inside a comment or a
    literal is never mistaken for a clause, and the clause's own parentheses are
    matched with the same awareness. Used only when SQLite refuses a migration for
    the size of a CHECK expression (older SQLite builds have a small parser stack).
    CHECK constrains inserted values; it adds no table, column, view or trigger, so
    the schema read from the stripped migration is the same."""
    out: list[str] = []
    i, n = 0, len(sql)

    def skip_quoted(j: int) -> int:
        """Index just past the string, comment or bracket name starting at j, or j."""
        c = sql[j]
        if sql.startswith("--", j):
            k = sql.find("\n", j)
            return n if k < 0 else k
        if sql.startswith("/*", j):
            k = sql.find("*/", j + 2)
            return n if k < 0 else k + 2
        close = {"'": "'", '"': '"', "`": "`", "[": "]"}.get(c)
        if close is None:
            return j
        k = j + 1
        while k < n:
            if sql[k] == close:
                if close != "]" and sql.startswith(close, k + 1):
                    k += 2  # a doubled quote is an escaped quote
                    continue
                return k + 1
            k += 1
        return n

    while i < n:
        j = skip_quoted(i)
        if j != i:
            out.append(sql[i:j])
            i = j
            continue
        if sql[i].isalnum() or sql[i] == "_":
            k = i
            while k < n and (sql[k].isalnum() or sql[k] == "_"):
                k += 1
            word = sql[i:k]
            if word.upper() == "CHECK":
                m = k
                while m < n:  # spaces and comments between CHECK and the "("
                    if sql[m].isspace():
                        m += 1
                    elif sql.startswith(("--", "/*"), m):
                        m = skip_quoted(m)
                    else:
                        break
                if m < n and sql[m] == "(":
                    depth = 0
                    while m < n:
                        e = skip_quoted(m)
                        if e != m:
                            m = e
                            continue
                        if sql[m] == "(":
                            depth += 1
                        elif sql[m] == ")":
                            depth -= 1
                            if depth == 0:
                                m += 1
                                break
                        m += 1
                    else:
                        raise ValueError("unbalanced parentheses in a CHECK clause")
                    out.append(" ")
                    i = m
                    continue
            out.append(word)
            i = k
            continue
        out.append(sql[i])
        i += 1
    return "".join(out)


# SQLite builds with a small parser stack refuse a very large CHECK expression.
PARSER_LIMIT = re.compile(
    r"stack overflow|too (?:complex|deep|large)|maximum depth|nested too"
    r"|recursion limit",
    re.I,
)


def build_db(
    migrations: list[tuple[str, str]],
    force_strip: bool = False,
    used: list[str] | None = None,
) -> sqlite3.Connection:
    """Run the migrations in order, as written. A migration SQLite cannot run is a
    failure that names it. The one exception: when SQLite refuses a migration for
    parser size (a very large CHECK, seen on SQLite 3.45), that migration alone is
    run again with its CHECK clauses removed by strip_checks, on a database rebuilt
    from the migrations before it; the names of such migrations go in `used`. If the
    stripped copy fails too, that fails by name. `force_strip` takes that path for
    every migration (a test of the fallback)."""
    version = sqlite3.sqlite_version
    db = sqlite3.connect(":memory:")
    applied: list[str] = []
    for name, sql in migrations:
        text = strip_checks(sql) if force_strip else sql
        try:
            db.executescript(text)
        except sqlite3.Error as exc:
            if force_strip or not PARSER_LIMIT.search(str(exc)):
                raise AssertionError(
                    f"migration {name} does not run on SQLite {version}: {exc}"
                ) from exc
            db.close()  # undo any statements of this migration that did run
            db = sqlite3.connect(":memory:")
            for earlier in applied:
                db.executescript(earlier)
            text = strip_checks(sql)
            try:
                db.executescript(text)
            except sqlite3.Error as exc2:
                raise AssertionError(
                    f"migration {name} does not run on SQLite {version}, also "
                    f"with its CHECK clauses removed: {exc2}"
                ) from exc2
            if used is not None:
                used.append(name)
        if force_strip and used is not None:
            used.append(name)
        applied.append(text)
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


def live_objects(db: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """The views, triggers and indexes the migrations leave behind: name -> the full
    CREATE text from sqlite_master with whitespace collapsed. A view or trigger body
    can expose or copy rows, so the text is part of what is reviewed and pinned."""
    return {
        kind: {
            n: " ".join(sql.split())
            for n, sql in db.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = ? AND sql IS NOT NULL ORDER BY name",
                (kind,),
            )
        }
        for kind in ("view", "trigger", "index")
    }


def schema_digest(
    schema: dict[str, dict[str, Any]], objects: dict[str, dict[str, str]]
) -> str:
    """One hash of every table's columns, types, defaults and keys, and the full
    definition text of every view, trigger and index. It changes when any of them
    does, including a changed type or default or a changed view or trigger body."""
    body = {t: schema[t]["digest_rows"] for t in sorted(schema)}
    text = json.dumps([body, objects], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def load_registry() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return data


def _without_comments(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            k: _without_comments(v) for k, v in node.items() if not k.startswith("_")
        }
    if isinstance(node, list):
        return [_without_comments(v) for v in node]
    return node


def registry_digest(registry: dict[str, Any]) -> str:
    """Hash of the whole registry except this field and the _comment prose. Any
    edit to a class, reason, column list, other_objects entry, call-site record,
    export path or schema digest changes it, so no record can be edited alone."""
    body = _without_comments(
        {k: v for k, v in registry.items() if k != "reviewed_registry_digest"}
    )
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def consistency_violations(
    registry: dict[str, Any],
    schema: dict[str, dict[str, Any]],
    objects: dict[str, dict[str, str]],
    sites: dict[str, dict[str, list[str]]],
) -> list[str]:
    """The registry's three records (schema digest, column lists, reviewed call
    sites) must agree with the live repository, and the registry's own digest must
    cover them, so a change to one record cannot be made without the others being
    recomputed in the same edit. This is a mechanical consistency check: it does
    not prove an independent person approved the new values."""
    found: list[str] = []
    if registry.get("reviewed_schema_digest") != schema_digest(schema, objects):
        found.append("reviewed_schema_digest does not match the live schema")
    for table, entry in registry["stores"].items():
        if table in schema and entry.get("columns") != schema[table]["columns"]:
            found.append(f"{table}: recorded columns do not match the live schema")
    found += call_site_violations(sites, registry)
    if registry.get("reviewed_registry_digest") != registry_digest(registry):
        found.append(
            "the registry changed without its own digest: set "
            f"reviewed_registry_digest to {registry_digest(registry)} in the same "
            "edit as the records it covers"
        )
    return found


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
    objects: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Content checks: what a table holds, whatever the registry says about its name."""
    stores: dict[str, Any] = registry["stores"]
    found: list[str] = []
    for kind, names in (objects or {}).items():
        if kind not in {"view", "trigger"}:
            continue  # indexes are pinned by the digest and expose no rows
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
    objects: dict[str, dict[str, str]],
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


def mask_ts(text: str) -> tuple[str, list[bool]]:
    """(text with every comment, string and template body blanked to 'x' (newlines
    kept), per-character flag: inside a comment). Lexical, for finding statement
    ends only; an unterminated literal blanks to the end of the file."""
    out = list(text)
    in_comment = [False] * len(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
        elif c in "\"'`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            for k in range(i + 1, max(i + 1, j - 1)):
                if out[k] != "\n":
                    out[k] = "x"
            i = j
            continue
        else:
            i += 1
            continue
        for k in range(i, j):
            in_comment[k] = True
            if out[k] != "\n":
                out[k] = "x"
        i = j
    return "".join(out), in_comment


def _site_text(text: str, masked: str, in_comment: list[bool], pos: int) -> str:
    """The statement around a match, whitespace collapsed. A match in a comment is
    just that comment line."""
    if in_comment[pos]:
        start = text.rfind("\n", 0, pos) + 1
        end = text.find("\n", pos)
        return " ".join(text[start : len(text) if end < 0 else end].split())
    # Backward to the start of the statement: a `;`, or a brace at bracket depth 0.
    depth, start = 0, 0
    for i in range(pos - 1, -1, -1):
        ch = masked[i]
        if ch in ")]":
            depth += 1
        elif ch in "([":
            depth = max(depth - 1, 0)
        elif ch in ";{}" and depth == 0:
            start = i + 1
            break
    # Forward to its end: a `;` or brace at depth 0, so an object or call argument
    # that spans lines (the value being written) is part of the statement.
    depth, end = 0, len(text)
    for i in range(pos, len(masked)):
        ch = masked[i]
        if ch == "{" and depth == 0 and masked[:i].rstrip()[-1:] not in {*"=:,(["}:
            end = i  # a block opens (function or control body), not a value
            break
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                end = i
                break
            depth -= 1
        elif ch == ";" and depth == 0:
            end = i
            break
    # Comments inside the statement are not part of it: blank them.
    # and all whitespace is dropped, so reformatting is not a change.
    code = "".join(" " if in_comment[i] else text[i] for i in range(start, end))
    return "".join(code.split())


def worker_call_sites(
    src: Path = WORKER_SRC, binding_names: set[str] | None = None, root: Path = ROOT
) -> dict[str, dict[str, list[str]]]:
    """file -> primitive -> sorted short hashes of every statement that mentions it.
    A new call through an already-approved primitive or binding, or an edit to a
    reviewed one, is a new hash."""
    names = binding_names if binding_names is not None else wrangler_config(root)[1]
    rx = dict(PRIMITIVES)
    for name in sorted(names):
        rx[f"binding:{name}"] = re.compile(rf"\b{re.escape(name)}\b")
    out: dict[str, dict[str, list[str]]] = {}
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        masked, in_comment = mask_ts(text)
        for name, pattern in rx.items():
            counts: dict[str, int] = {}
            in_comment_only = False
            for m in pattern.finditer(text):
                if in_comment[m.start()]:
                    # A comment cannot write; a name mentioned only in comments is
                    # recorded as "comment" so the mention itself stays reviewed.
                    in_comment_only = True
                    continue
                h = hashlib.sha256(
                    _site_text(text, masked, in_comment, m.start()).encode()
                ).hexdigest()[:12]
                counts[h] = counts.get(h, 0) + 1
            # A repeated identical statement is a second call, so it shows as h*N.
            sites = sorted(h if k == 1 else f"{h}*{k}" for h, k in counts.items())
            if in_comment_only and not sites:
                sites = ["comment"]
            if sites:
                out.setdefault(path.relative_to(root).as_posix(), {})[name] = sites
    return out


def call_site_violations(
    sites: dict[str, dict[str, list[str]]], registry: dict[str, Any]
) -> list[str]:
    """Every statement that touches a storage primitive was reviewed, per file."""
    reviewed: dict[str, dict[str, list[str]]] = {
        p: d
        for p, d in registry["runtime_stores"]["reviewed_call_sites"].items()
        if not p.startswith("_")
    }
    found: list[str] = []
    for path in sorted(set(sites) | set(reviewed)):
        for name in sorted(set(sites.get(path, {})) | set(reviewed.get(path, {}))):
            have = sites.get(path, {}).get(name, [])
            want = reviewed.get(path, {}).get(name, [])
            for h in sorted(set(have) - set(want)):
                found.append(
                    f"{path}: {name} appears in a statement not reviewed ({h}): a new "
                    "or edited call through a storage primitive; review what it "
                    "writes, then record the hash in reviewed_call_sites"
                )
            for h in sorted(set(want) - set(have)):
                found.append(
                    f"{path}: {name} reviewed statement {h} is gone or edited; "
                    "update reviewed_call_sites"
                )
    return found


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


# Never part of the repository's own code: dependencies and tool caches. Everything
# else that is a text file is scanned, in every directory.
EXPORT_SKIP_DIRS = SKIP_DIRS | {
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    ".wrangler",
    ".tox",
}
# Prose, not executable: docs are inventoried by whole_database_export_docs, the
# changelog fragments describe the change. A Markdown file cannot run anything.
EXPORT_PROSE = {".md", ".markdown"}
MAX_SCAN_BYTES = 2_000_000


def _json_strings(node: Any, out: list[str]) -> None:
    """Every string value in a parsed JSON document (a package.json scripts entry, a
    command array, a deploy file). Keys starting with an underscore are comments."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not str(key).startswith("_"):
                _json_strings(value, out)
    elif isinstance(node, list):
        if node and all(isinstance(x, str) for x in node):
            out.append(" ".join(node))  # an argv written as an array
        for item in node:
            _json_strings(item, out)
    elif isinstance(node, str):
        out.append(node)


def _flatten_command_text(text: str) -> str:
    """Join the spellings a command can hide in: line continuations, YAML block
    lists, quoted comma-separated arrays (Containerfile CMD, JSON, YAML flow)."""
    text = text.replace("\\\n", " ")
    text = re.sub(r"(?m)^\s*-\s+", " ", text)
    text = re.sub(r"[\[\]\"',]", " ", text)
    return " ".join(text.split())


def export_texts(path: Path) -> list[str]:
    """The text views of one file the export pattern is matched against."""
    raw = path.read_text(encoding="utf-8")
    views = [raw, _flatten_command_text(raw)]
    if path.suffix in {".json", ".jsonc"}:
        try:
            strings: list[str] = []
            _json_strings(json.loads(strip_jsonc(raw)), strings)
            views.append("\n".join(strings))
        except ValueError:
            pass  # not parseable: the raw views above still apply
    return views


def export_scan(root: Path = ROOT) -> dict[str, set[str]]:
    """file -> export-capable commands it contains, over every text file in the
    repository outside dependency and cache directories (scripts anywhere, package.json
    files, Makefile and justfile, workflows, composite actions, Containerfile, deploy
    files, JSON and YAML command arrays, tests, the CLI)."""
    out: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if (
            not path.is_file()
            or path.is_symlink()
            or EXPORT_SKIP_DIRS & set(rel.parts)
            or path.suffix in EXPORT_PROSE
        ):
            continue
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            views = export_texts(path)
        except (UnicodeDecodeError, OSError):
            continue  # a binary file holds no command text a shell could run
        hits = {m.group(0) for view in views for m in EXPORT_COMMAND.finditer(view)}
        if hits:
            out[rel.as_posix()] = {" ".join(h.split()) for h in hits}
    return out


def whole_database_export_docs(root: Path = ROOT) -> set[str]:
    """Markdown showing a `wrangler d1 export` with no --table: it dumps every table."""
    found: set[str] = set()
    for path in root.rglob("*.md"):
        if EXPORT_SKIP_DIRS & set(path.relative_to(root).parts) or not path.is_file():
            continue
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
    # Test change (#856): objects now carry their definition text, so compare names.
    objs = live_objects(db)
    assert list(objs["view"]) == ["everyone"]
    assert list(objs["trigger"]) == ["copy_email"]
    assert "SELECT email FROM leads" in objs["view"]["everyone"]
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


def _same_schema(a: sqlite3.Connection, b: sqlite3.Connection) -> bool:
    return live_schema(a) == live_schema(b) and live_objects(a) == live_objects(b)


def test_the_check_stripper_is_not_fooled_by_comments_or_literals() -> None:
    sql = HOSTILE[0][1]
    stripped = strip_checks(sql)
    # Text in comments and literals is copied through untouched.
    assert (
        "-- CHECK (" in stripped
        and "/* CHECK ( CREATE TABLE in_a_block_comment" in stripped
    )
    assert "'a (b); CHECK (c)'" in stripped
    assert "'semi; ) paren ( CREATE TABLE fake (id INT);'" in stripped
    # The one real clause is gone, parentheses and quotes inside it included.
    assert "label <> 'x(;'" not in stripped
    # Same schema as the real parse (the statements that matter are unchanged).
    assert _same_schema(build_db(HOSTILE), build_db(HOSTILE, force_strip=True))
    # Odd spellings: lower case, a comment between the keyword and the parenthesis,
    # a doubled quote, a quoted identifier that contains the word.
    odd = (
        "CREATE TABLE \"check\" (a INT check /* x ) */ (a > 0 AND 'it''s )' <> a),"
        " b TEXT CHECK\n(b <> '(' ), checks INT);"
    )
    got = strip_checks(odd)
    assert '"check"' in got and "checks INT" in got
    assert "a > 0" not in got and "b <> '('" not in got
    db = build_db([("0001.sql", got)])
    assert live_schema(db)["check"]["columns"] == ["a", "b", "checks"]


def test_the_fallback_gives_the_same_schema_and_digest_for_every_migration() -> None:
    """The fallback used on SQLite builds that refuse a large CHECK (3.45.1 on the
    CI runners, which cannot be run here) is forced for every real migration: the
    tables, columns, types, views, triggers and the digest are the same."""
    direct = real_db()
    used: list[str] = []
    forced = build_db(
        [(m.name, m.read_text(encoding="utf-8")) for m in MIGRATIONS],
        force_strip=True,
        used=used,
    )
    assert used == [m.name for m in MIGRATIONS]
    assert _same_schema(direct, forced)
    want = schema_digest(live_schema(direct), live_objects(direct))
    assert schema_digest(live_schema(forced), live_objects(forced)) == want
    assert want == load_registry()["reviewed_schema_digest"]

    # The forced run really ran without constraints: no stored table keeps a CHECK
    # (the direct run does keep at least one, so this is not a vacuous comparison).
    def checks(db: sqlite3.Connection) -> int:
        return sum(
            bool(re.search(r"\bCHECK\s*\(", sql or "", re.I))
            for (sql,) in db.execute("SELECT sql FROM sqlite_master WHERE type='table'")
        )

    assert checks(direct) > 0 and checks(forced) == 0


def test_a_migration_too_big_for_the_parser_is_retried_without_its_checks() -> None:
    """A real parser-size refusal (an expression tree past SQLite's depth limit),
    after an earlier statement of the same migration already ran: the retry starts
    from a rebuilt database, so nothing is created twice."""
    deep = "NOT " * 3000 + "v > 0"
    sql = (
        "CREATE TABLE first_t (id INTEGER PRIMARY KEY);"
        f"CREATE TABLE deep_t (v INTEGER CHECK ({deep}));"
    )
    with pytest.raises(sqlite3.Error):
        sqlite3.connect(":memory:").executescript(sql)
    used: list[str] = []
    db = build_db(
        [("0001_deep.sql", sql), ("0002_next.sql", "CREATE TABLE n (a);")], used=used
    )
    assert used == ["0001_deep.sql"]
    assert list(live_schema(db)) == ["deep_t", "first_t", "n"]
    # If the stripped copy fails too, it fails by name and says so.
    bad = sql + "CREATE TABLE ((;"
    with pytest.raises(AssertionError, match=r"0003_bad\.sql .*CHECK clauses removed"):
        build_db([("0003_bad.sql", bad)])


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


def _digest_of(*parts: str) -> str:
    db = build_db([("0001.sql", "".join(parts))])
    return schema_digest(live_schema(db), live_objects(db))


_BASE = (
    "CREATE TABLE leads (id TEXT PRIMARY KEY, email TEXT);"
    "CREATE TABLE notes_log (id INTEGER PRIMARY KEY, label TEXT);"
)


def test_a_changed_view_body_alone_changes_the_digest() -> None:
    """High (#856 round 2): same view name, same tables, only the body differs."""
    aggregate = _digest_of(
        _BASE, "CREATE VIEW stats AS SELECT COUNT(*) AS n FROM leads;"
    )
    exposing = _digest_of(_BASE, "CREATE VIEW stats AS SELECT email AS n FROM leads;")
    assert aggregate != exposing
    # Whitespace and line breaks alone do not count as a change.
    spaced = _digest_of(
        _BASE, "CREATE VIEW stats AS\n  SELECT   COUNT(*) AS n\n  FROM leads;"
    )
    assert aggregate == spaced


def test_a_changed_trigger_body_alone_changes_the_digest() -> None:
    quiet = _digest_of(
        _BASE, "CREATE TRIGGER t AFTER INSERT ON leads BEGIN SELECT 1; END;"
    )
    copying = _digest_of(
        _BASE,
        "CREATE TRIGGER t AFTER INSERT ON leads BEGIN ",
        "INSERT INTO notes_log (label) VALUES (NEW.email); END;",
    )
    assert quiet != copying


def test_the_real_registry_fails_when_a_trigger_or_view_body_changes() -> None:
    """Keep the real trigger name and every column; change only the body."""
    registry = load_registry()
    db = real_db()
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'kit_meta_floor_insert'"
    ).fetchone()
    assert row, "trigger kit_meta_floor_insert vanished"
    db.execute("DROP TRIGGER kit_meta_floor_insert")
    db.execute(
        "CREATE TRIGGER kit_meta_floor_insert BEFORE INSERT ON kit_meta "
        "BEGIN INSERT INTO view_meta (key, value) VALUES ('copy', NEW.value); END"
    )
    found = review_violations(live_schema(db), registry, live_objects(db))
    assert any("schema changed since the registry was reviewed" in v for v in found)

    db2 = real_db()
    db2.execute("CREATE VIEW exposes AS SELECT email FROM leads")
    approved = copy.deepcopy(registry)
    approved["other_objects"]["view"]["exposes"] = "synthetic: reviewed, aggregate only"
    still = review_violations(live_schema(db2), approved, live_objects(db2))
    assert any("schema changed since the registry was reviewed" in v for v in still)
    # And editing the body of that approved view (same name) is seen again.
    before = schema_digest(live_schema(db2), live_objects(db2))
    db2.execute("DROP VIEW exposes")
    db2.execute("CREATE VIEW exposes AS SELECT COUNT(*) AS n FROM leads")
    assert schema_digest(live_schema(db2), live_objects(db2)) != before


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


def strip_ts_comments(text: str) -> str:
    _, in_comment = mask_ts(text)
    return "".join(" " if c else ch for ch, c in zip(text, in_comment, strict=True))


def ts_prepared_sql(text: str) -> list[str]:
    """The SQL text of every `.prepare(<string literals joined by +>)` call, comments
    removed. A literal with a `${` interpolation is returned as written."""
    code = strip_ts_comments(text)
    found: list[str] = []
    for m in re.finditer(r"\.prepare\(", code):
        i, parts = m.end(), []
        while True:
            while i < len(code) and code[i].isspace():
                i += 1
            if i >= len(code) or code[i] not in "\"'`":
                break
            quote, j = code[i], i + 1
            while j < len(code) and code[j] != quote:
                j += 2 if code[j] == "\\" else 1
            parts.append(code[i + 1 : j])
            i = j + 1
            while i < len(code) and code[i].isspace():
                i += 1
            if code[i : i + 1] != "+":
                break
            i += 1
        if parts:
            found.append("".join(parts))
    return found


LEAD_FIXTURE_TABLES = {"leads", "kit_events"}
AGE_DAYS = 30
DAY_MS = 86_400_000


def age_purge_violations(source: Path = LEAD_RETENTION) -> list[str]:
    """Run the purge's own DELETE statements, in source order, on the migrated schema
    with an old lead and a fresh lead (each with events); every lead-class table must
    lose the old rows and keep the fresh ones."""
    found: list[str] = []
    deletes = [
        q
        for q in ts_prepared_sql(source.read_text(encoding="utf-8"))
        if q.lstrip().startswith("DELETE FROM")
    ]
    tables = sorted(
        t for t, e in load_registry()["stores"].items() if e["class"] == "lead"
    )
    if not set(tables) <= LEAD_FIXTURE_TABLES:
        missing = sorted(set(tables) - LEAD_FIXTURE_TABLES)
        return [f"no purge fixture for lead tables {missing}"]
    db = real_db()
    now = 10 * AGE_DAYS * DAY_MS
    cutoff = now - AGE_DAYS * DAY_MS
    leads = {
        "old": ("a" * 32, cutoff - 1),
        "edge": ("b" * 32, cutoff),
        "new": ("c" * 32, now),
    }
    for lid, created in leads.values():
        db.execute(
            "INSERT INTO leads (id, email, created_ts, last_seen_ts, source) "
            "VALUES (?, ?, ?, ?, 'gate')",
            (lid, f"{lid[:4]}@example.test", created, created),
        )
        db.execute(
            "INSERT INTO kit_events (ts, lead_id, kind) VALUES (3600000, ?, 'view')",
            (lid,),
        )
    for _ in range(3):  # bounded steps, as the hourly job takes them
        for q in deletes:
            db.execute(q, (cutoff, 100))

    def left(table: str, lid: str) -> int:
        col = "id" if table == "leads" else "lead_id"
        query = f"SELECT COUNT(*) FROM {table} WHERE {col} = ?"  # noqa: S608
        return int(db.execute(query, (lid,)).fetchone()[0])

    for table in tables:
        if left(table, leads["old"][0]) != 0:
            found.append(f"{table}: rows of an expired lead survive the purge")
        for keep in ("edge", "new"):
            if left(table, leads[keep][0]) != 1:
                found.append(f"{table}: a lead that is not expired lost rows ({keep})")
    return found


def test_the_age_purge_removes_expired_rows_of_every_lead_table() -> None:
    assert age_purge_violations() == []


def test_the_age_purge_check_bites_on_a_dead_or_missing_delete(tmp_path: Path) -> None:
    text = LEAD_RETENTION.read_text(encoding="utf-8")
    stmt = '"DELETE FROM kit_events WHERE id IN'
    assert stmt in text
    # Test change (#856, round 2): was a substring assertion; a statement in a
    # comment, or no statement, now fails because nothing runs it.
    commented = tmp_path / "commented.ts"
    commented.write_text(
        text.replace(stmt, "/* " + stmt + " */ \"SELECT 1 WHERE 0 AND '", 1)
    )
    assert age_purge_violations(commented) != []
    gone = tmp_path / "gone.ts"
    gone.write_text(
        text.replace("DELETE FROM leads WHERE id IN", "SELECT 1 WHERE 0 AND id IN", 1)
    )
    assert age_purge_violations(gone) != []
    # A cutoff that deletes a lead on the boundary is seen too.
    off = tmp_path / "off.ts"
    off.write_text(text.replace("created_ts < ?1", "created_ts <= ?1"))
    assert any("not expired lost rows" in v for v in age_purge_violations(off))


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

ALERT_TTL_MS = 600_000
ALERT_CAP = 500
ALERT_SITE = "default"
QUOTA_TS = WORKER_SRC / "alerts" / "quota.ts"
DISPATCHER_TS = WORKER_SRC / "alerts" / "dispatcher.ts"


def alert_retention_violations(
    quota: Path = QUOTA_TS, dispatcher: Path = DISPATCHER_TS
) -> list[str]:
    """Run the real prune and claim SQL of the alert code on the migrated schema with a
    clock the test controls. Checks the 10 minute boundary, that expired rows go only
    when the next event is admitted, and the row cap after more than 500 claims."""
    found: list[str] = []
    qtext = quota.read_text(encoding="utf-8")
    code = strip_ts_comments(qtext)
    cap_m = re.search(r"export const DEDUP_ROW_CAP = (\d+);", code)
    ttl_m = re.search(
        r"export const DEDUP_MS = (\d+) \* 60_?000;",
        strip_ts_comments(dispatcher.read_text(encoding="utf-8")),
    )
    if not cap_m or int(cap_m.group(1)) != ALERT_CAP:
        found.append(f"the row cap is not {ALERT_CAP}")
    if not ttl_m or int(ttl_m.group(1)) * 60_000 != ALERT_TTL_MS:
        found.append("the repeat window is not 10 minutes")
    sql = ts_prepared_sql(qtext)
    prune = [q for q in sql if q.startswith("DELETE FROM alert_dedup WHERE expires_at")]
    claim = [q for q in sql if q.lstrip().startswith("INSERT INTO alert_dedup")]
    if len(prune) != 1 or len(claim) != 1:
        return found + ["the prune or claim statement was not found"]
    batch = re.search(r"db\.batch\(\[\s*\.\.\.pruneStatements\(db, now\),", code)
    if not batch:
        found.append("claimDedup no longer prunes in the same batch, before the claim")

    def admit(db: sqlite3.Connection, digest: str, now: int) -> bool:
        db.execute(prune[0], (now,))  # the batch runs the prune, then the claim
        rows = db.execute(
            claim[0], (ALERT_SITE, digest, now + ALERT_TTL_MS, ALERT_CAP, now)
        ).fetchall()
        return len(rows) == 1

    def count(db: sqlite3.Connection) -> int:
        return int(db.execute("SELECT COUNT(*) FROM alert_dedup").fetchone()[0])

    t0 = 1_000_000_000
    db = real_db()
    a, b = "a" * 32, "b" * 32
    if not admit(db, a, t0):
        found.append("a first claim was refused")
    if admit(db, a, t0 + ALERT_TTL_MS - 1):
        found.append("a repeat inside 10 minutes was admitted")
    if not admit(db, a, t0 + ALERT_TTL_MS):
        found.append("a repeat at the 10 minute mark was refused")
    # Quiet site: nothing runs between events, so an expired row stays until the next
    # admission, which deletes it.
    admit(db, b, t0 + ALERT_TTL_MS + 1)
    if count(db) != 2:
        found.append("a live row was lost, or the repeat was not recorded")
    later = t0 + 100 * ALERT_TTL_MS
    admit(db, "c" * 32, later)
    if count(db) != 1:
        found.append("the next admitted event did not delete the expired rows")
    # Cap: more than 500 distinct claims at one instant never store more than 500.
    full = real_db()
    admitted = sum(admit(full, f"{i:032x}", t0) for i in range(ALERT_CAP + 100))
    if count(full) > ALERT_CAP or admitted > ALERT_CAP:
        found.append("more than the row cap was stored")
    if count(full) != ALERT_CAP:
        found.append("the table did not fill to its cap")
    return found


def test_the_alert_hash_retention_runs_as_documented_on_the_real_sql() -> None:
    assert alert_retention_violations() == []


def test_the_alert_retention_check_bites(tmp_path: Path) -> None:
    # Test change (#856, round 2): was substring matching on two files; each mutation
    # below keeps the old substrings and now fails by behavior.
    qtext = QUOTA_TS.read_text(encoding="utf-8")
    dtext = DISPATCHER_TS.read_text(encoding="utf-8")

    def run(q: str, d: str) -> list[str]:
        (tmp_path / "q.ts").write_text(q)
        (tmp_path / "d.ts").write_text(d)
        return alert_retention_violations(tmp_path / "q.ts", tmp_path / "d.ts")

    assert run(qtext, dtext) == []
    assert any(
        "row cap" in v
        for v in run(
            qtext.replace("DEDUP_ROW_CAP = 500", "DEDUP_ROW_CAP = 5000"), dtext
        )
    )
    assert any(
        "10 minutes" in v
        for v in run(
            qtext, dtext.replace("DEDUP_MS = 10 * 60_000", "DEDUP_MS = 600 * 60_000")
        )
    )
    no_cap = qtext.replace(
        "(SELECT COUNT(*) FROM alert_dedup) < ?4\n            OR EXISTS "
        "(SELECT 1 FROM alert_dedup WHERE site = ?1 AND event_hash = ?2)",
        "?4 > 0",
        1,
    )
    assert no_cap != qtext
    assert run(no_cap, dtext) != []
    head = "\n    db\n      .prepare(\n        `INSERT INTO alert_dedup"
    no_prune = qtext.replace(
        "...pruneStatements(db, now)," + head,
        head.lstrip("\n"),
        1,
    )
    assert no_prune != qtext
    assert any("prunes" in v or "refused" in v for v in run(no_prune, dtext))
    # The prune and the claim's own conflict clause both treat the mark as expired.
    wrong_boundary = qtext.replace("expires_at <= ?1", "expires_at < ?1", 1).replace(
        "alert_dedup.expires_at <= ?5", "alert_dedup.expires_at < ?5", 1
    )
    assert wrong_boundary != qtext
    assert any("10 minute mark" in v for v in run(wrong_boundary, dtext))


def test_the_alert_hash_claim_in_the_docs_matches_the_behavior() -> None:
    """Rows stop counting as repeats after ten minutes, and nothing deletes them on a
    timer: the docs, the checklist and the registry must say so."""
    index = (WORKER_SRC / "index.ts").read_text(encoding="utf-8")
    assert "pruneAlertState" not in index and "alert_dedup" not in index
    deleters = sorted(
        p.relative_to(WORKER_SRC).as_posix()
        for p in WORKER_SRC.rglob("*.ts")
        if "DELETE FROM alert_dedup" in p.read_text(encoding="utf-8")
    )
    # Only the alert state module deletes dedup rows (no timer-driven prune elsewhere).
    assert deleters == ["alerts/quota.ts"], deleters
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


EXPORT = "wrangler d1 export db --remote --output=all.sql"
EMPTY_EXPORT_PATHS: dict[str, dict[str, str]] = {
    "commands": {},
    "whole_database_docs": {},
}


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("worker/scripts/dump-all.sh", f"npx {EXPORT}\n"),
        ("tools/backup.sh", f"{EXPORT}\n"),
        ("bin/export-db", f"#!/bin/sh\n{EXPORT}\n"),
        ("package.json", json.dumps({"scripts": {"dump": f"npx {EXPORT}"}})),
        ("apps/web/package.json", json.dumps({"scripts": {"dump": f"npx {EXPORT}"}})),
        (
            "scripts/export.json",
            json.dumps({"cmd": ["npx", "wrangler", "d1", "export"]}),
        ),
        ("justfile", f"dump:\n    npx {EXPORT}\n"),
        (
            ".github/workflows/x.yml",
            f"jobs:\n  a:\n    steps:\n      - run: {EXPORT}\n",
        ),
        (".github/actions/y/action.yml", f"runs:\n  steps:\n    - run: {EXPORT}\n"),
        ("Containerfile", 'CMD ["npx", "wrangler", "d1", "export", "db"]\n'),
        ("deploy/run.yaml", "args:\n  - wrangler\n  - d1\n  - export\n"),
        ("tools/cont.sh", "npx wrangler d1 \\\n  export db\n"),
    ],
)
def test_the_export_inventory_finds_a_command_in_any_executable_location(
    tmp_path: Path, name: str, content: str
) -> None:
    """Round 2 (#856): shell scripts anywhere, any package.json, a justfile, workflow
    and composite action files, a Containerfile, deploy files, JSON and YAML argument
    arrays and line continuations are all seen."""
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    found = export_inventory_violations(
        export_scan(tmp_path), set(), {"export_paths": EMPTY_EXPORT_PATHS}
    )
    assert any(name in v and "not in export_paths" in v for v in found), (name, found)


def test_the_export_inventory_skips_dependencies_and_prose(tmp_path: Path) -> None:
    for name in ("node_modules/x/run.sh", "worker/node_modules/y/run.sh", "notes.md"):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(EXPORT + "\n")
    assert export_scan(tmp_path) == {}


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


# --- reviewed call sites: a primitive's approval does not cover a new write ----------


def _copy_worker_src(tmp_path: Path) -> tuple[Path, Path]:
    """A copy of worker/src and the wrangler config, to mutate."""
    import shutil

    shutil.copytree(WORKER_SRC, tmp_path / "worker" / "src")
    for cfg in wrangler_files(ROOT):
        dest = tmp_path / cfg.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cfg.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path / "worker" / "src", tmp_path


def test_the_real_call_sites_match_the_reviewed_record() -> None:
    names = wrangler_config(ROOT)[1]
    sites = worker_call_sites(binding_names=names)
    assert call_site_violations(sites, load_registry()) == []
    # Every reviewed primitive has a recorded site and the other way round.
    assert {(p, n) for p, d in sites.items() for n in d} == {
        (p, n)
        for p, d in load_registry()["runtime_stores"]["reviewed_call_sites"].items()
        if not p.startswith("_")
        for n in d
    }


def test_a_new_write_through_an_already_approved_binding_fails(tmp_path: Path) -> None:
    """Round 2 (#856): live.ts is approved for put and the LIVE_KV binding; a new
    statement writing a lead-shaped key through them used to pass."""
    src, root = _copy_worker_src(tmp_path)
    live = src / "live.ts"
    live.write_text(
        live.read_text(encoding="utf-8")
        + "\nexport async function leak(env: Env, email: string) {\n"
        "  await env.LIVE_KV.put(`lead:${email}`, JSON.stringify({ email }));\n}\n",
        encoding="utf-8",
    )
    names = wrangler_config(root)[1]
    registry = load_registry()
    # The primitive-level check still passes: this is the gap being closed.
    assert (
        runtime_violations(
            worker_primitives(src, names, root), wrangler_config(root)[0], registry
        )
        == []
    )
    found = call_site_violations(worker_call_sites(src, names, root), registry)
    assert any(
        v.startswith("worker/src/live.ts: put appears in a statement not reviewed")
        for v in found
    )
    assert any(
        "binding:LIVE_KV appears in a statement not reviewed" in v for v in found
    )


def test_an_edited_reviewed_call_and_a_repeated_identical_call_both_fail(
    tmp_path: Path,
) -> None:
    src, root = _copy_worker_src(tmp_path)
    names = wrangler_config(root)[1]
    registry = load_registry()
    sites = worker_call_sites(src, names, root)["worker/src/live.ts"]["put"]
    assert sites
    live = src / "live.ts"
    original = live.read_text(encoding="utf-8")
    line = next(ln for ln in original.splitlines() if ".put(" in ln)
    live.write_text(original.replace(line, line + "\n" + line, 1), encoding="utf-8")
    again = worker_call_sites(src, names, root)["worker/src/live.ts"]["put"]
    assert again != sites
    assert call_site_violations(worker_call_sites(src, names, root), registry)
    live.write_text(
        original.replace(line, line.replace(".put(", ".put( /* x */   ", 1), 1),
        encoding="utf-8",
    )
    # A comment or spacing alone is not a change to the statement.
    assert call_site_violations(worker_call_sites(src, names, root), registry) == []
    live.write_text(original.replace(".put(", '.put("k2", ', 1), encoding="utf-8")
    assert call_site_violations(worker_call_sites(src, names, root), registry)


def test_a_brace_inside_a_template_does_not_hide_a_statement() -> None:
    text = "const a = 1;\nawait kv.put(`lead:${email}`, body);\nconst b = 2;\n"
    masked, flags = mask_ts(text)
    pos = text.index(".put")
    # Whitespace is dropped, so the template's braces cannot end the statement.
    assert _site_text(text, masked, flags, pos) == "awaitkv.put(`lead:${email}`,body)"
    comment = "x();\n// kv.put is mentioned here\ny();\n"
    cm, cf = mask_ts(comment)
    assert (
        _site_text(comment, cm, cf, comment.index(".put"))
        == "// kv.put is mentioned here"
    )


# --- the registry cannot change one record without the others ---------------------


def test_the_registry_records_are_consistent_with_the_repository() -> None:
    db = real_db()
    names = wrangler_config(ROOT)[1]
    assert (
        consistency_violations(
            load_registry(),
            live_schema(db),
            live_objects(db),
            worker_call_sites(binding_names=names),
        )
        == []
    )


def test_editing_one_registry_record_alone_fails_the_consistency_check() -> None:
    """The registry's own digest covers the schema digest, the column lists, the
    call-site record and every reason, so an edit to any of them is incomplete until
    the digest is recomputed with it. It proves the records move together, not that a
    second person approved them (a policy control, docs/privacy.md)."""
    db = real_db()
    schema, objects = live_schema(db), live_objects(db)
    sites = worker_call_sites(binding_names=wrangler_config(ROOT)[1])
    registry = load_registry()

    def check(mutated: dict[str, Any]) -> list[str]:
        return consistency_violations(mutated, schema, objects, sites)

    reason = copy.deepcopy(registry)
    reason["stores"]["counters"]["reason"] += " (edited)"
    assert any("without its own digest" in v for v in check(reason))

    cols = copy.deepcopy(registry)
    cols["stores"]["counters"]["columns"].append("payload")
    out = check(cols)
    assert any("without its own digest" in v for v in out)
    assert any("recorded columns do not match" in v for v in out)

    digest = copy.deepcopy(registry)
    digest["reviewed_schema_digest"] = "0" * 64
    out = check(digest)
    assert any("does not match the live schema" in v for v in out)
    assert any("without its own digest" in v for v in out)

    reviewed = copy.deepcopy(registry)
    reviewed["runtime_stores"]["reviewed_call_sites"]["worker/src/live.ts"][
        "put"
    ].append("deadbeef0000")
    out = check(reviewed)
    assert any("is gone or edited" in v for v in out)
    assert any("without its own digest" in v for v in out)

    # All records recomputed together is the one edit that passes.
    consistent = copy.deepcopy(registry)
    consistent["stores"]["counters"]["reason"] += " (edited)"
    consistent["reviewed_registry_digest"] = registry_digest(consistent)
    assert check(consistent) == []
    # Comment prose is not covered, so fixing a typo in a _comment needs no digest.
    prose = copy.deepcopy(registry)
    prose["_comment"] += " typo fixed"
    assert check(prose) == []
