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
    move-legacy-leads.py ... --purge    # (frozen only) copy, verify, delete, verify

With neither flag it is a dry run: it reads and counts, writes and deletes nothing.

Cutover, in this order (docs/deploy.md): --freeze makes the analytics database itself
refuse every new lead or kit event row (a BEFORE INSERT trigger), so an older Worker
can no longer store a lead there; the move; then --verify, which exits 0 only when the
tables are empty (or gone) and frozen. No application-level check can stop an older
writer; the freeze is what does, and --verify is the check to run before enabling the
gate. Both take the same --source, --dest and --workdir arguments as the move.

Steps. 1 Identity: SOURCE must answer 'analytics' and DEST 'leads' from their own
db_identity tables (a name proves nothing), else it refuses (exit 3). 2 Count what
SOURCE holds. 3 (--copy or --purge) in bounded chunks (MOVE_BATCH rows, default 200,
1 to 1000), in primary key order: write the chunk to DEST with INSERT OR IGNORE, so a
DEST row is never overwritten, read it back and require every SOURCE row of the chunk
to be there with every column equal, then record a digest of each row and the cursor in
a manifest. A lead that DEST already holds under another id (same address) does not
match, so the check fails and nothing is deleted: decide that one by hand. 4 (--purge)
REFUSES (exit 3) unless every present analytics lead table is frozen (--freeze first),
before any copy or delete. It then proves, from the manifest, that DEST holds exactly
the recorded rows (see Manifest) and only then deletes from SOURCE, in chunks, exactly
the rows whose id and every column match a recorded digest: each kit event, then each
lead that has no event left. Never "delete everything": a row inserted or changed in
SOURCE after the copy does not match, survives, and is counted; the script then exits 1
and says to run it again. Last, in the same run, it runs the --verify check below and
fails unless that passes. Stop every older Worker that writes the analytics database
first.

Manifest. WORKDIR/move-manifest.json (mode 600, written atomically): the two database
names, the state ("copying" or "copied"), a cursor per table, and one SHA-256 digest
per row ever copied (the table and id as the key; no address, no column value). It is
the only record that survives the delete, so it is what lets --verify prove DEST still
holds every lead after the analytics rows are gone. A crash mid-copy resumes from the
cursor; running again is idempotent. Rows are digested, never kept: memory is one
chunk plus about 100 bytes per row of manifest. Keep the file until cutover is done;
it is not removed when the script ends. --verify refuses without it when DEST holds
any lead row. A manifest for other database names is refused.

--verify exits 0 only when: SOURCE holds no lead rows and every present lead table is
frozen against inserts; and DEST holds exactly the manifest's rows, compared by count
and by digest of every column: none missing, none different, none extra (run it before
the gate is enabled, while DEST holds nothing but the moved rows). With no manifest it
passes only if DEST also holds no lead row (nothing was ever moved).

No data is lost: the delete is reachable only after the read-back check and the
manifest proof, in the same run, and the script stops at the first error. Re-running
after a failure is safe.

Privacy. Rows are held in memory one chunk at a time and in one file inside a fresh
private directory made under WORKDIR (an existing directory you own, mode 700, outside
any git work tree); the file is created exclusively, never through a symlink, and
removed with its directory when the chunk is done. The manifest holds digests only.
It prints counts and fixed messages only, never a row or wrangler's own output. Every
wrangler call is bounded by MOVE_TIMEOUT seconds (default 120, 1 to 900). WRANGLER
names another command (a test stub); the default is `npx wrangler`. Run it from the
folder whose wrangler config names both databases.

Exit: 0 done or dry run, 1 a step failed (nothing deleted unless it says so), 2 usage,
3 refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

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
TABLES = (("leads", LEAD_COLUMNS), ("kit_events", EVENT_COLUMNS))  # leads first
COLUMNS = dict(TABLES)
MANIFEST = "move-manifest.json"
EXISTS_SQL = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'leads' "
    "UNION ALL SELECT name FROM sqlite_master WHERE type = 'table' "
    "AND name = 'kit_events'"
)


class Failed(Exception):
    """A step failed. Carries a fixed message, never data."""


class Refused(Exception):
    """A precondition does not hold (exit 3)."""


def timeout_seconds() -> int:
    raw = os.environ.get("MOVE_TIMEOUT", "120")
    if not raw.isdigit() or not 1 <= int(raw) <= 900:
        raise SystemExit("error: MOVE_TIMEOUT must be a whole number from 1 to 900")
    return int(raw)


def batch_size() -> int:
    raw = os.environ.get("MOVE_BATCH", "200")
    if not raw.isdigit() or not 1 <= int(raw) <= 1000:
        raise SystemExit("error: MOVE_BATCH must be a whole number from 1 to 1000")
    return int(raw)


class Wrangler:
    def __init__(self) -> None:
        self.cmd = shlex.split(os.environ.get("WRANGLER", "npx wrangler"))
        self.limit = timeout_seconds()

    def execute(
        self, db: str, *, command: str | None = None, file: Path | None = None
    ) -> list[dict[str, Any]]:
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
        rows: list[dict[str, Any]] = []
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


def count_rows(w: Wrangler, db: str, table: str) -> int:
    rows = w.execute(db, command=f"SELECT COUNT(*) AS n FROM {table}")
    n = rows[0].get("n") if rows else None
    if not isinstance(n, int) or isinstance(n, bool):
        raise Failed("unexpected answer from the database")
    return n


def counts(w: Wrangler, db: str) -> dict[str, int]:
    """Rows per lead table; a table that is not there holds none."""
    have = present_tables(w, db)
    return {t: count_rows(w, db, t) if t in have else 0 for t, _ in TABLES}


def page(
    w: Wrangler, db: str, table: str, after: object | None, limit: int
) -> list[dict[str, Any]]:
    """The next `limit` rows of `table` after primary key `after`, in key order."""
    cols = ", ".join(COLUMNS[table])
    cond = "" if after is None else f" WHERE id > {literal(after)}"
    return w.execute(
        db, command=f"SELECT {cols} FROM {table}{cond} ORDER BY id LIMIT {limit}"
    )


def by_ids(w: Wrangler, db: str, table: str, ids: list[object]) -> list[dict[str, Any]]:
    cols = ", ".join(COLUMNS[table])
    listed = ", ".join(literal(i) for i in ids)
    return w.execute(db, command=f"SELECT {cols} FROM {table} WHERE id IN ({listed})")


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


def row_sql(prefix: str, columns: tuple[str, ...], row: dict[str, Any]) -> str:
    return prefix + "(" + ", ".join(literal(row.get(c)) for c in columns) + ");"


def insert_sql(table: str, rows: list[dict[str, Any]]) -> str:
    cols = COLUMNS[table]
    head = f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES "
    return "\n".join(row_sql(head, cols, r) for r in rows) + "\n"


def where(columns: tuple[str, ...], row: dict[str, Any]) -> str:
    """Match a row by id and every column (IS, so a NULL column matches NULL)."""
    return " AND ".join(f"{c} IS {literal(row.get(c))}" for c in columns)


def delete_sql(table: str, rows: list[dict[str, Any]]) -> str:
    """Delete exactly the verified rows; a lead goes only with no event left."""
    tail = ";"
    if table == "leads":
        tail = (
            " AND NOT EXISTS (SELECT 1 FROM kit_events e WHERE e.lead_id = leads.id);"
        )
    return (
        "\n".join(
            f"DELETE FROM {table} WHERE " + where(COLUMNS[table], r) + tail
            for r in rows
        )
        + "\n"
    )


def run_file(w: Wrangler, db: str, work: Path, sql: str) -> None:
    """Run `sql` through a file made exclusively in a fresh private directory."""
    scratch = Path(tempfile.mkdtemp(prefix="move-", dir=work))  # mode 0700, new
    path = scratch / "statements.sql"
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(sql)
        w.execute(db, file=path)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def missing_from(
    dest_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
    columns: tuple[str, ...],
) -> int:
    have = {row.get("id"): tuple(row.get(c) for c in columns) for row in dest_rows}
    return sum(
        1
        for r in source_rows
        if have.get(r.get("id")) != tuple(r.get(c) for c in columns)
    )


def digest(table: str, row: dict[str, Any]) -> str:
    """SHA-256 over every column of a row, in a fixed order and type-tagged."""
    parts = [[type(row.get(c)).__name__, row.get(c)] for c in COLUMNS[table]]
    return hashlib.sha256(json.dumps(parts, separators=(",", ":")).encode()).hexdigest()


class Manifest:
    """The private record of what was copied: digests and cursors, never a row."""

    def __init__(self, work: Path, source: str, dest: str) -> None:
        self.path = work / MANIFEST
        self.source = source
        self.dest = dest
        self.state = "copying"
        self.cursor: dict[str, Any] = {}
        self.digests: dict[str, dict[str, str]] = {t: {} for t, _ in TABLES}
        self.exists = False

    def load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            if not stat.S_ISREG(os.fstat(fh.fileno()).st_mode):
                raise Refused("the manifest is not a regular file.")
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise Refused(
                    "the manifest is unreadable; resolve it by hand."
                ) from exc
        ok = (
            isinstance(data, dict)
            and data.get("version") == 1
            and data.get("state") in ("copying", "copied")
            and isinstance(data.get("cursor"), dict)
            and isinstance(data.get("digests"), dict)
            and all(isinstance(data["digests"].get(t), dict) for t, _ in TABLES)
        )
        if not ok:
            raise Refused("the manifest is unreadable; resolve it by hand.")
        if data.get("source") != self.source or data.get("dest") != self.dest:
            raise Refused("the manifest belongs to other databases; resolve by hand.")
        self.state = data["state"]
        self.cursor = data["cursor"]
        self.digests = {t: dict(data["digests"][t]) for t, _ in TABLES}
        self.exists = True

    def save(self) -> None:
        tmp = self.path.with_name(MANIFEST + ".tmp")
        tmp.unlink(
            missing_ok=True
        )  # a crashed run's leftover (a link goes, not its target)
        body = json.dumps(
            {
                "version": 1,
                "source": self.source,
                "dest": self.dest,
                "state": self.state,
                "cursor": self.cursor,
                "digests": self.digests,
            }
        )
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self.exists = True

    def record(self, table: str, row: dict[str, Any]) -> None:
        key, new = str(row.get("id")), digest(table, row)
        if self.digests[table].setdefault(key, new) != new:
            raise Failed(
                "a row differs from what an earlier run recorded; decide by hand"
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


# The exact definition each trigger must have. A name proves nothing: an unrelated
# trigger of the same name (one that does not refuse, or only refuses some inserts)
# would otherwise pass for the freeze. No IF NOT EXISTS: a name taken by another
# definition makes the CREATE fail instead of silently keeping the impostor.
FREEZE_BODY = "BEGIN SELECT RAISE(ABORT, 'leads moved to the leads database'); END"
FROZEN = {
    "leads": ("leads_frozen", "BEFORE INSERT ON leads " + FREEZE_BODY),
    "kit_events": ("kit_events_frozen", "BEFORE INSERT ON kit_events " + FREEZE_BODY),
}
TRIGGERS_SQL = (
    "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger' "
    "AND name IN ('leads_frozen', 'kit_events_frozen')"
)


def create_sql(table: str) -> str:
    name, rest = FROZEN[table]
    return f"CREATE TRIGGER {name} {rest}"


def present_tables(w: Wrangler, db: str) -> set[str]:
    return {str(r.get("name")) for r in w.execute(db, command=EXISTS_SQL)}


def frozen_tables(w: Wrangler, db: str) -> set[str]:
    """Tables whose freeze trigger has exactly the expected name, table and text."""
    rows = w.execute(db, command=TRIGGERS_SQL)
    got = {(r.get("name"), r.get("tbl_name"), r.get("sql")) for r in rows}
    return {t for t in FROZEN if (FROZEN[t][0], t, create_sql(t)) in got}


def freeze(w: Wrangler, db: str) -> int:
    """Make the database refuse a new lead row: an older Worker's INSERT fails."""
    tables = present_tables(w, db)
    done = frozen_tables(w, db)
    taken = {str(r.get("name")) for r in w.execute(db, command=TRIGGERS_SQL)}
    if any(FROZEN[t][0] in taken for t in tables - done):
        raise Failed("a freeze trigger is missing or differs; resolve it by hand")
    for table in sorted(tables - done):
        w.execute(db, command=create_sql(table))  # fails if the name is taken
    if tables - frozen_tables(w, db):  # read back, by definition, not by name
        raise Failed("a freeze trigger is missing or differs; resolve it by hand")
    if not tables:
        print("nothing to freeze: the analytics database has no lead tables")
        return 0
    print(f"frozen: {len(tables)} lead table(s) in analytics now refuse inserts")
    return 0


def check_dest(w: Wrangler, dest: str, manifest: Manifest, size: int) -> None:
    """DEST must hold exactly the manifest's rows: counts, then a digest of each row."""
    have = present_tables(w, dest)
    missing = different = extra = 0
    for table, _ in TABLES:
        want = dict(manifest.digests[table])
        seen = 0
        after: object | None = None
        while table in have:
            rows = page(w, dest, table, after, size)
            if not rows:
                break
            for r in rows:
                seen += 1
                got = want.pop(str(r.get("id")), None)
                if got is None:
                    extra += 1
                elif got != digest(table, r):
                    different += 1
            after = rows[-1].get("id")
        missing += len(want)
        print(f"dest {table}: {seen} rows, manifest {len(manifest.digests[table])}")
    print(
        f"dest check: {missing} missing, {different} different, {extra} extra "
        "against the manifest"
    )
    if missing or different or extra:
        raise Failed("the leads database does not hold exactly the moved rows")


def check_frozen(w: Wrangler, db: str) -> None:
    if present_tables(w, db) - frozen_tables(w, db):
        raise Refused("SOURCE is not frozen against inserts; run --freeze first.")


def verify(w: Wrangler, source: str, dest: str, work: Path) -> int:
    """The cutover check: exit 0 only when the move is complete and final."""
    size = batch_size()
    tables = present_tables(w, source)
    held = counts(w, source)
    unfrozen = sorted(tables - frozen_tables(w, source))
    print(f"analytics holds {held['leads']} leads and {held['kit_events']} kit events")
    print(f"lead tables not frozen against inserts: {len(unfrozen)}")
    if any(held.values()):
        raise Failed("lead rows remain in the analytics database; run the move")
    if unfrozen:
        raise Failed("a lead table can still take inserts from an older Worker; freeze")
    manifest = Manifest(work, source, dest)
    manifest.load()
    if not manifest.exists:
        if any(counts(w, dest).values()):
            raise Failed("no manifest: the leads database cannot be proved complete")
        print("no manifest and no lead rows anywhere: nothing was ever moved")
    elif manifest.state != "copied":
        raise Failed("the manifest says a copy did not finish; run the move again")
    else:
        check_dest(w, dest, manifest, size)
    print("cutover verified: moved completely, and no older Worker can store a lead.")
    return 0


def copy_chunks(w: Wrangler, args: argparse.Namespace, work: Path, m: Manifest) -> None:
    """Copy and read back in bounded chunks, recording each chunk before the next."""
    size = batch_size()
    if m.state == "copying" and m.exists:
        print("resuming an unfinished copy from the recorded cursor")
    else:
        m.state, m.cursor = "copying", {}
    m.save()
    total = {t: 0 for t, _ in TABLES}
    for table, cols in TABLES:
        after = m.cursor.get(table)
        while True:
            rows = page(w, args.source, table, after, size)
            if not rows:
                break
            run_file(w, args.dest, work, insert_sql(table, rows))
            back = by_ids(w, args.dest, table, [r.get("id") for r in rows])
            short = missing_from(back, rows, cols)
            if short:
                print(
                    f"verify: {short} {table} rows are not in dest exactly as in source"
                )
                raise Failed("copy does not match; nothing was deleted from analytics")
            for r in rows:
                m.record(table, r)
            after = rows[-1].get("id")
            m.cursor[table] = after
            m.save()
            total[table] += len(rows)
    m.state = "copied"
    m.save()
    print(
        f"copied {total['leads']} leads and {total['kit_events']} kit events; "
        "verify: 0 leads and 0 kit events are not in dest exactly as in source"
    )


def purge_chunks(
    w: Wrangler, args: argparse.Namespace, work: Path, m: Manifest
) -> None:
    """Delete only rows whose every column matches a recorded digest, in chunks."""
    size = batch_size()
    for table in ("kit_events", "leads"):  # events first: a lead goes with none left
        after: object | None = None
        while True:
            rows = page(w, args.source, table, after, size)
            if not rows:
                break
            after = rows[-1].get("id")
            known = [
                r
                for r in rows
                if m.digests[table].get(str(r.get("id"))) == digest(table, r)
            ]
            if known:
                run_file(w, args.source, work, delete_sql(table, known))


def run(args: argparse.Namespace) -> int:
    w = Wrangler()
    work = check_workdir(args.workdir)
    if roles(w, args.source) != ["analytics"]:
        raise Refused("SOURCE did not identify itself as the analytics database.")
    if roles(w, args.dest) != ["leads"]:
        raise Refused("DEST did not identify itself as the leads database.")

    if args.freeze:
        return freeze(w, args.source)
    if args.verify:
        return verify(w, args.source, args.dest, work)

    held = counts(w, args.source)
    print(f"source holds {held['leads']} leads and {held['kit_events']} kit events")
    if not any(held.values()):
        print("nothing to move: the analytics database holds no lead rows")
        return 0
    if not (args.copy or args.purge):
        print("dry run: nothing was written or deleted.")
        print("run again with --copy, or --purge to also delete the analytics rows.")
        return 0
    if args.purge:
        check_frozen(w, args.source)  # before any copy or delete

    manifest = Manifest(work, args.source, args.dest)
    manifest.load()
    copy_chunks(w, args, work, manifest)
    if not args.purge:
        print("copied and verified. Nothing was deleted; --purge deletes the copies.")
        return 0

    # Frozen again and DEST proved complete from the manifest, right before the delete.
    check_frozen(w, args.source)
    check_dest(w, args.dest, manifest, batch_size())
    purge_chunks(w, args, work, manifest)
    left = counts(w, args.source)
    print(f"source now holds {left['leads']} leads and {left['kit_events']} kit events")
    if any(left.values()):
        raise Failed(
            "rows remain in the analytics database (added or changed after the "
            "copy, so never verified); stop older Workers and run again"
        )
    verify(w, args.source, args.dest, work)  # the final check, in this same run
    print("done: the lead rows live only in the leads database now.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Move legacy leads (issue #772).")
    parser.add_argument("--source", required=True, help="the analytics D1 database")
    parser.add_argument("--dest", required=True, help="the leads D1 database")
    parser.add_argument("--workdir", required=True, help="private (mode 700) dir")
    # One mode per run: a second flag would be silently ignored, so it is refused.
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--copy", action="store_true", help="copy and verify only")
    modes.add_argument("--purge", action="store_true", help="copy, verify, delete")
    modes.add_argument("--freeze", action="store_true", help="refuse new rows (step 1)")
    modes.add_argument("--verify", action="store_true", help="the cutover check")
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
    except (OSError, UnicodeError):
        # A local file or process error: its text can hold a path or data. Fixed only.
        print(
            "failed: a local file or process error; nothing more is shown",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
