#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Expand, migrate, contract: keep D1 migrations safe to deploy ahead of the Worker.

Issue #768.

Run as `python3 -I .github/scripts/check-migrations.py [MIGRATIONS_DIR]` (default
worker/migrations). The deploy applies migrations first and the Worker second, so
for a moment the OLD Worker runs against the NEW schema, and after a failed
deploy or an automatic rollback it stays there. A migration is therefore only
safe to ship with the Worker when the old code still works on it.

Rule: every migration is ADDITIVE (create table, create index, add column, insert)
unless its file carries the annotation line

    -- migration-phase: contract

which says a person decided the previous release no longer reads what is being
removed. A contract migration ships in a LATER release than the Worker that stopped
using the column or table, never in the same one. Statements refused without the
annotation: DROP (table, column, index, view, trigger), RENAME (table or column,
including ALTER ... RENAME) and DELETE FROM. ADD COLUMN, CREATE and INSERT pass.

Migrations 0001 to 0017 were written before this rule and are left as they are
(0007 and 0008 contain a DELETE and a DROP TABLE); the rule applies to every
migration numbered above LEGACY_THROUGH.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LEGACY_THROUGH = 17
NO_LEGACY = -1  # below every migration number, 0000 included
ANNOTATION = re.compile(r"^\s*--\s*migration-phase:\s*contract\s*$", re.MULTILINE)
NAME = re.compile(r"^(\d{4})_[A-Za-z0-9_]+\.sql$")
BREAKING = (
    (
        re.compile(r"\bDROP\s+(TABLE|COLUMN|INDEX|VIEW|TRIGGER)\b", re.IGNORECASE),
        "DROP",
    ),
    (re.compile(r"\bRENAME\s+(TO|COLUMN)\b", re.IGNORECASE), "RENAME"),
    (re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE), "DELETE FROM"),
)


def strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def problems(directory: Path, legacy_through: int = LEGACY_THROUGH) -> list[str]:
    found: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        match = NAME.match(path.name)
        if match is None:
            found.append(f"{path.name}: the name must be NNNN_name.sql")
            continue
        if int(match.group(1)) <= legacy_through:
            continue
        text = path.read_text(encoding="utf-8")
        if ANNOTATION.search(text):
            continue
        body = strip_comments(text)
        for pattern, label in BREAKING:
            if pattern.search(body):
                found.append(
                    f"{path.name}: {label} breaks the previous Worker. Ship it "
                    "additive, or add the line '-- migration-phase: contract' in a "
                    "release AFTER the Worker stopped using it"
                )
    return found


def main(argv: list[str]) -> int:
    # `check-migrations.py [DIR] [--no-legacy]`. --no-legacy applies the rule to every
    # file, for a folder that never had a pre-rule history (worker/leads-migrations).
    args = [a for a in argv[1:] if a != "--no-legacy"]
    # -1, not 0: migration number 0000 matches the name pattern and must be checked too.
    legacy_through = NO_LEGACY if "--no-legacy" in argv[1:] else LEGACY_THROUGH
    directory = Path(args[0]) if args else Path("worker/migrations")
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return 2
    found = problems(directory, legacy_through)
    for line in found:
        print(f"error: {line}")
    if not found:
        print("migrations: all additive, or annotated as contract")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
