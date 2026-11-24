#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: S608
# S608: the statements below are joined from module constants only, never from input.
"""Owner-run, one time: move legacy lead rows out of the ANALYTICS D1 database.

Issue #772. An older release stored leads and kit events in the analytics database.
This copies them into the LEADS database, reads them back, and only then deletes the
analytics copies. Until it has run, the Worker refuses every lead path
(src/kit/separation.ts), because those rows are readable with an analytics-only
credential.

    move-legacy-leads.py --source ANALYTICS_DB --dest LEADS_DB --workdir DIR
    move-legacy-leads.py ... --copy     # copy and verify, delete nothing
    move-legacy-leads.py ... --purge    # copy, verify, then delete the analytics rows

With neither flag it is a dry run: it reads and counts, writes and deletes nothing.

Steps. 1 Identity: SOURCE must answer 'analytics' and DEST 'leads' from their own
db_identity tables (a name proves nothing), else it refuses (exit 3). 2 Count what
SOURCE holds. 3 (--copy or --purge) write every lead and kit event to DEST with
INSERT OR IGNORE, so a DEST row is never overwritten, then read DEST back and require
every SOURCE row to be there with every column equal. A lead that DEST already holds
under another id (same address) does not match, so the check fails and nothing is
deleted: decide that one by hand. 4 (--purge, and only after step 3 passed in this
same run) delete kit_events then leads from SOURCE and require both to be empty.

No data is lost: the delete is reachable only after the read-back check, in the same
run, and the script stops at the first error. Re-running after a failure is safe.

Privacy. Rows are held in memory and in one private file in WORKDIR (an existing
directory you own, mode 700, outside any git work tree), removed when the script ends.
It prints counts and fixed messages only, never a row or wrangler's own output. Every
wrangler call is bounded by MOVE_TIMEOUT seconds (default 120, 1 to 900). WRANGLER
names another command (a test stub); the default is `npx wrangler`. Run it from the
folder whose wrangler config names both databases.

Exit: 0 done or dry run, 1 a step failed (nothing deleted unless it says so), 2 usage,
3 refused.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
LEAD_COLUMNS = (
    "id",
    "email",
    "created_ts",
    "last_seen_ts",
    "verified_ts",
    "source",
    "country",
    "last_view_alert_ts",
    "last_view_event_ts",
    "last_cc_alert_ts",
    "session_floor_ts",
)
EVENT_COLUMNS = ("id", "ts", "lead_id", "kind")
# Fixed statements: no table or column name is ever built from input.
LEAD_LIST = ", ".join(LEAD_COLUMNS)
EVENT_LIST = ", ".join(EVENT_COLUMNS)
SELECT_LEADS = "SELECT " + LEAD_LIST + " FROM leads ORDER BY id"
SELECT_EVENTS = "SELECT " + EVENT_LIST + " FROM kit_events ORDER BY id"
EXISTS_SQL = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'leads' "
    "UNION ALL SELECT name FROM sqlite_master WHERE type = 'table' "
    "AND name = 'kit_events'"
)
INSERT_LEAD = "INSERT OR IGNORE INTO leads (" + LEAD_LIST + ") VALUES "
INSERT_EVENT = "INSERT OR IGNORE INTO kit_events (" + EVENT_LIST + ") VALUES "


class Failed(Exception):
    """A step failed. Carries a fixed message, never data."""


class Refused(Exception):
    """A precondition does not hold (exit 3)."""


def timeout_seconds() -> int:
    raw = os.environ.get("MOVE_TIMEOUT", "120")
    if not raw.isdigit() or not 1 <= int(raw) <= 900:
        raise SystemExit("error: MOVE_TIMEOUT must be a whole number from 1 to 900")
    return int(raw)


class Wrangler:
    def __init__(self) -> None:
        self.cmd = shlex.split(os.environ.get("WRANGLER", "npx wrangler"))
        self.limit = timeout_seconds()

    def execute(
        self, db: str, *, command: str | None = None, file: Path | None = None
    ) -> list[dict]:
        args = [*self.cmd, "d1", "execute", db, "--remote", "--json"]
        args += ["--command", command] if command is not None else [f"--file={file}"]
        try:
            done = subprocess.run(  # noqa: S603 - argv list, no shell
                args, capture_output=True, text=True, timeout=self.limit, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise Failed("a database call timed out") from exc
        if done.returncode != 0:
            raise Failed("a database call failed")
        try:
            data = json.loads(done.stdout)
        except ValueError as exc:
            raise Failed("unexpected answer from the database") from exc
        rows: list[dict] = []
        for block in data if isinstance(data, list) else [data]:
            if not isinstance(block, dict) or block.get("success") is False:
                raise Failed("the database refused a statement")
            rows = block.get("results", rows)
        return rows


def roles(w: Wrangler, db: str) -> list[str]:
    try:
        rows = w.execute(db, command="SELECT role FROM db_identity ORDER BY role")
    except Failed:
        return []
    return [str(r.get("role")) for r in rows]


def read_all(w: Wrangler, db: str) -> tuple[list[dict], list[dict]]:
    """Every lead and kit event in `db`; a table that is not there holds none."""
    have = {r.get("name") for r in w.execute(db, command=EXISTS_SQL)}
    leads = w.execute(db, command=SELECT_LEADS) if "leads" in have else []
    events = w.execute(db, command=SELECT_EVENTS) if "kit_events" in have else []
    return leads, events


def literal(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        raise Failed("unexpected value type")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and "\x00" not in value:
        return "'" + value.replace("'", "''") + "'"
    raise Failed("unexpected value type")


def row_sql(prefix: str, columns: tuple[str, ...], row: dict) -> str:
    return prefix + "(" + ", ".join(literal(row.get(c)) for c in columns) + ");"


def insert_sql(leads: list[dict], events: list[dict]) -> str:
    out = [row_sql(INSERT_LEAD, LEAD_COLUMNS, r) for r in leads]
    out += [row_sql(INSERT_EVENT, EVENT_COLUMNS, r) for r in events]
    return "\n".join(out) + "\n"


def missing_from(
    dest_rows: list[dict], source_rows: list[dict], columns: tuple[str, ...]
) -> int:
    have = {row.get("id"): tuple(row.get(c) for c in columns) for row in dest_rows}
    return sum(
        1
        for r in source_rows
        if have.get(r.get("id")) != tuple(r.get(c) for c in columns)
    )


def check_workdir(path: str) -> Path:
    work = Path(path)
    if not work.is_dir() or work.is_symlink():
        raise Refused("WORKDIR is not a real directory.")
    work = work.resolve()
    if work.stat().st_uid != os.getuid():
        raise Refused("WORKDIR is not owned by you.")
    if stat.S_IMODE(work.stat().st_mode) & 0o077:
        raise Refused("WORKDIR must not be open to group or others (chmod 700).")
    # Ask git, as scripts/backup-d1.sh does: a work tree or a git directory refuses.
    for query in ("--is-inside-work-tree", "--is-inside-git-dir"):
        try:
            done = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["git", "-C", str(work), "rev-parse", query],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise Refused("git is needed to prove WORKDIR is outside a repo.") from exc
        if done.stdout.strip() == "true":
            raise Refused("WORKDIR is inside a git repository; never commit leads.")
    return work


def run(args: argparse.Namespace) -> int:
    w = Wrangler()
    work = check_workdir(args.workdir)
    if roles(w, args.source) != ["analytics"]:
        raise Refused("SOURCE did not identify itself as the analytics database.")
    if roles(w, args.dest) != ["leads"]:
        raise Refused("DEST did not identify itself as the leads database.")

    leads, events = read_all(w, args.source)
    print(f"source holds {len(leads)} leads and {len(events)} kit events")
    if not leads and not events:
        print("nothing to move: the analytics database holds no lead rows")
        return 0
    if not (args.copy or args.purge):
        print("dry run: nothing was written or deleted.")
        print("run again with --copy, or --purge to also delete the analytics rows.")
        return 0

    path = work / "move-legacy-leads.sql"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(insert_sql(leads, events))
        w.execute(args.dest, file=path)
    finally:
        path.unlink(missing_ok=True)

    dest_leads, dest_events = read_all(w, args.dest)
    short_leads = missing_from(dest_leads, leads, LEAD_COLUMNS)
    short_events = missing_from(dest_events, events, EVENT_COLUMNS)
    print(f"dest now holds {len(dest_leads)} leads and {len(dest_events)} kit events")
    print(
        f"verify: {short_leads} leads and {short_events} kit events "
        "are not in dest exactly as in source"
    )
    if short_leads or short_events:
        raise Failed("copy does not match; nothing was deleted from analytics")
    if not args.purge:
        print("copied and verified. Nothing was deleted; --purge deletes the copies.")
        return 0

    w.execute(args.source, command="DELETE FROM kit_events")
    w.execute(args.source, command="DELETE FROM leads")
    left_leads, left_events = read_all(w, args.source)
    print(f"source now holds {len(left_leads)} leads and {len(left_events)} kit events")
    if left_leads or left_events:
        raise Failed("rows are still in the analytics database after the delete")
    print("done: the lead rows live only in the leads database now.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Move legacy leads (issue #772).")
    parser.add_argument("--source", required=True, help="the analytics D1 database")
    parser.add_argument("--dest", required=True, help="the leads D1 database")
    parser.add_argument("--workdir", required=True, help="private (mode 700) dir")
    parser.add_argument("--copy", action="store_true", help="copy and verify only")
    parser.add_argument("--purge", action="store_true", help="copy, verify, delete")
    args = parser.parse_args(argv)
    for value in (args.source, args.dest):
        if not NAME.match(value):
            print("error: bad database name", file=sys.stderr)
            return 2
    if args.source == args.dest:
        print("refused: SOURCE and DEST are the same name.", file=sys.stderr)
        return 3
    try:
        return run(args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except Failed as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
