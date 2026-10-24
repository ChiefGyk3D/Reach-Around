# SPDX-License-Identifier: Apache-2.0
"""The migration runner, shared by every database adapter.

Rules (Plan 2, Task 2): locked, checksummed, one transaction per file together
with its ``schema_migrations`` row, no down migrations, a database newer than
the code is refused, an edited or missing applied file is refused, and a file
numbered below the highest applied version is refused.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING

from reach_around.store.models import StoreError, fmt_ts

if TYPE_CHECKING:
    from reach_around.store.base import Db

_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

BOOTSTRAP = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
    "checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    filename: str
    checksum: str
    sql: str


@dataclass(frozen=True)
class MigrationStatus:
    applied: tuple[int, ...]
    pending: tuple[int, ...]


def _label(version: int) -> str:
    return f"{version:04d}"


def checksum(data: bytes) -> str:
    """SHA-256 of the bytes with ``\\r\\n`` normalized to ``\\n``."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def default_migrations_dir() -> Traversable:
    return files("reach_around.store").joinpath("migrations")


def load_migrations(directory: Traversable | Path | None = None) -> list[Migration]:
    """Read ``NNNN_name.sql`` files in numeric order; refuse duplicates."""
    root = default_migrations_dir() if directory is None else directory
    found: dict[int, Migration] = {}
    for entry in root.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILE.match(entry.name)
        if match is None:
            raise StoreError(
                f"migration file {entry.name!r} is not named NNNN_name.sql"
            )
        version = int(match.group(1))
        if version in found:
            raise StoreError(
                f"duplicate migration number {_label(version)}: "
                f"{found[version].filename} and {entry.name}"
            )
        data = entry.read_bytes()
        found[version] = Migration(
            version,
            match.group(2),
            entry.name,
            checksum(data),
            data.decode("utf-8").replace("\r\n", "\n"),
        )
    return [found[v] for v in sorted(found)]


def split_statements(sql: str) -> list[str]:
    """Split a migration into plain statements.

    Only plain statements are allowed: a ``;`` inside a string, a trigger body
    or an inline comment is refused rather than guessed at.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.split("\n"):
        if not buffer.strip() and (not line.strip() or line.lstrip().startswith("--")):
            buffer = ""
            continue
        buffer += line + "\n"
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if ";" in statement[:-1]:
                raise StoreError(
                    "migration statements may not contain a ';' inside "
                    "a string, comment or trigger body"
                )
            statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise StoreError("migration ends with an incomplete statement")
    return statements


def _applied(db: Db) -> dict[int, tuple[str, str]]:
    if not db.table_exists("schema_migrations"):
        return {}
    rows = db.query("SELECT version, name, checksum FROM schema_migrations")
    return {int(str(r[0])): (str(r[1]), str(r[2])) for r in rows}


def plan(db: Db, migrations: list[Migration]) -> list[Migration]:
    """Return the pending migrations, or raise ``StoreError`` for any breach."""
    by_version = {m.version: m for m in migrations}
    highest_known = max(by_version, default=0)
    applied = _applied(db)
    for version in sorted(applied):
        known = by_version.get(version)
        if known is None:
            if version > highest_known:
                raise StoreError(
                    f"the database is at migration {_label(version)}, newer than "
                    f"this build (highest known {_label(highest_known)}). "
                    "Upgrade the engine or restore a backup taken before the "
                    "newer migration; there are no down migrations."
                )
            raise StoreError(
                f"applied migration {_label(version)} has no file in this build"
            )
        if known.checksum != applied[version][1]:
            raise StoreError(
                f"migration {_label(version)} changed after it was applied "
                f"({known.filename})"
            )
    highest_applied = max(applied, default=0)
    pending = [m for m in migrations if m.version not in applied]
    for migration in pending:
        if migration.version < highest_applied:
            raise StoreError(
                f"migration {_label(migration.version)} is older than "
                f"applied {_label(highest_applied)}"
            )
    return pending


def status(db: Db, migrations: list[Migration]) -> MigrationStatus:
    applied = _applied(db)
    return MigrationStatus(
        tuple(sorted(applied)),
        tuple(m.version for m in migrations if m.version not in applied),
    )


def run_migrations(
    db: Db, migrations: list[Migration], *, now: datetime | None = None
) -> int:
    """Apply every pending migration; return the highest applied version."""
    stamp = fmt_ts(now or datetime.now(UTC))
    pending = plan(db, migrations)  # refuse early, before any backup
    if pending:
        db.pre_migration_backup(pending[0].version)
    failure: Exception | None = None
    with db.migration_lock():
        db.execute(BOOTSTRAP)
        pending = plan(db, migrations)  # again, now that we hold the lock
        for migration in pending:
            try:
                with db.transaction():
                    for statement in split_statements(migration.sql):
                        db.execute(statement)
                    db.execute(
                        "INSERT INTO schema_migrations "
                        "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                        (migration.version, migration.name, migration.checksum, stamp),
                    )
            except Exception as exc:
                failure = exc
                break
        highest = max(_applied(db), default=0) if failure is None else 0
    if failure is not None:
        raise failure
    return highest
