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

    move-legacy-leads.py --source ANALYTICS_DB --source-id UUID \\
        --dest LEADS_DB --dest-id UUID --workdir DIR
    move-legacy-leads.py ... --copy     # copy and verify, delete nothing
    move-legacy-leads.py ... --purge --locked-window-confirmed   # see below

With neither flag it is a dry run: it reads and counts, writes and deletes nothing.

Identity. Both databases are named twice: by name and by the platform id you read
yourself with `npx wrangler d1 info <NAME> --json` (the `uuid` field). Every run asks
the platform for each name's id (the same command) and refuses (exit 3) unless the
answer equals the id you passed, and the manifest is bound to those ids. A table inside
the data proves nothing, because a restored or cloned database carries a copy of it; a
clone gets a platform id of its own. SOURCE must also answer 'analytics' and DEST
'leads' from their own db_identity tables. Residual limit: the ids are checked at the
start of a run and the later calls address the databases by name through the wrangler
config of the folder you run from; if that config maps a name to a different database
than the platform does, only a mismatch you can see (the printed ids) shows it, so run
it from the folder whose wrangler config names both databases and read the two ids.

Recoverable, exclusively locked cutover. D1 has no transaction across two databases,
so the destination re-read and the source DELETE can never be one atomic step. The
purge does not claim they are. It is a locked window plus a recovery point:

1. --purge needs --locked-window-confirmed: your statement that every writer is off
   (the Worker KIT_KILL and SCHEDULED_KILL switches engaged and deployed, so the gate,
   the view route and the lead purge write neither database; no older Worker writes
   the analytics lead tables). Without the flag it refuses (exit 3), before any copy.
2. It checks what it can: every analytics lead table carries the exact freeze trigger
   (--freeze first), the destination holds exactly the manifest's rows, and the row
   count and a digest of every row of BOTH databases are the same across two reads
   MOVE_SETTLE seconds apart (default 5, 0 to 60). It cannot prove a writer is off
   (an UPDATE or DELETE by an older Worker is not blocked by the freeze, and a writer
   that is silent for MOVE_SETTLE seconds proves nothing); it can only notice one.
3. Before any delete it records a D1 Time Travel bookmark of SOURCE (the output of
   `wrangler d1 time-travel info SOURCE --json`, read only) in the manifest, and
   refuses (exit 3) without one. It prints the exact restore command. Recovery is to
   restore SOURCE to that bookmark (it overwrites SOURCE in place) and run the copy
   again from the manifest; DEST keeps its copies.
4. Per chunk it still re-reads the rows from DEST right before the delete and puts the
   freeze check inside each DELETE statement. These are defense in depth, not an
   atomic proof: they narrow the window, they do not close it.

Cutover, in this order (docs/deploy.md): --freeze makes the analytics database itself
refuse every new lead or kit event row (a BEFORE INSERT trigger), so an older Worker
can no longer store a lead there; the move; then --verify, which exits 0 only when the
tables are empty (or gone) and frozen. The two freeze triggers are the only objects
this tool leaves in a database; scripts/dump-schema.json allows them, so the backup
dump check passes before and after the move. They go with the legacy tables when a
later migration drops those tables.

Steps. 1 Identity (above). 2 Count what SOURCE holds. 3 (--copy or --purge) in bounded
chunks (MOVE_BATCH rows, default 200, 1 to 1000), in primary key order: write the chunk
to DEST (INSERT OR IGNORE for a row not seen before, so a DEST row is never overwritten;
an upsert for a row the manifest already knows whose SOURCE columns have changed since,
only while DEST still holds the recorded version), read it back and require every SOURCE
row of the chunk to be there with every column equal, then record a digest of each row
(a changed row's digest is replaced only after that read-back) and the cursor in a
manifest. A lead that DEST already holds under another id (same address) does not
match, so the check fails and nothing is deleted: decide that one by hand. 4 (--purge)
runs the window checks above, then deletes from SOURCE, in chunks, exactly the rows
whose id and every column match a recorded digest: each kit event, then each lead that
has no event left. Never "delete everything": a row inserted or changed in SOURCE after
the copy does not match, survives, and is counted; the script then exits 4 and says to
run it again (the copy step re-copies the changed row and records its new digest).
Last, in the same run, it runs the --verify check below and fails unless that passes.

Manifest. WORKDIR/move-manifest.json (mode 600, written atomically; an existing one is
read only if it is a plain file you own with mode 600 and one link): the two database
names and platform ids, the Time Travel bookmarks taken, the state ("copying" or
"copied"), a cursor per table, and one SHA-256 digest per row ever copied (the table
and id as the key; no address, no column value). It is the only record that survives
the delete, so it is what lets --verify prove DEST still holds every lead after the
analytics rows are gone. A crash mid-copy resumes from the cursor; running again is
idempotent. Rows are digested, never kept: memory is one chunk plus about 100 bytes per
row of manifest. Keep the file until cutover is done; it is not removed when the script
ends. --verify refuses without it when DEST holds any lead row. A manifest for other
database names or other platform ids is refused.

--verify exits 0 only when: SOURCE holds no lead rows and every present lead table is
frozen against inserts; and DEST holds exactly the manifest's rows, compared by count
and by digest of every column: none missing, none different, none extra (run it before
the gate is enabled, while DEST holds nothing but the moved rows). With no manifest it
passes only if DEST also holds no lead row (nothing was ever moved).

Privacy. Rows are held in memory one chunk at a time and in one file inside a fresh
directory made under WORKDIR (an existing directory you own, mode 700, outside any git
work tree, no symlink or group/other writable parent). The directory is opened by
descriptor and checked (owner, mode 700, same inode as its path), the file is created
exclusively through that descriptor and never through a symlink, and both are checked
again right before wrangler is started; both are removed when the chunk is done.
Residual race: wrangler opens the file by path, so a process of your own user that
swaps the file in the instant between that last check and wrangler's open is not
stopped (it can already read WORKDIR and the manifest). The manifest holds digests
only. It prints counts and fixed messages only, never a row or wrangler's own output.
Every wrangler call is bounded by MOVE_TIMEOUT seconds (default 120, 1 to 900). WRANGLER
names another command (a test stub); the default is `npx wrangler`. Run it from the
folder whose wrangler config names both databases.

Interruption. SIGINT (Ctrl-C), SIGTERM and SIGHUP are caught: the handler only notes
the signal, so no manifest, cursor or export write is ever cut in half. The run stops
at the next safe point, between two chunks (or right after the settle wait): a
wrangler call already running finishes first (it runs in its own session, bounded by
MOVE_TIMEOUT), so the outcome of every statement is known. Then it exits: 6 when this
run deleted nothing
(a copy resumes from its saved cursor, a purge starts over), 4 or 5 as below when it had
already deleted. The manifest, the cursor and the recovery export are written to a
temporary file, fsynced, and renamed (the directory is fsynced too), so a crash or
SIGKILL leaves the previous file or the new one. SIGKILL and power loss cannot be
caught: the manifest records `purge_open` before the first DELETE, which stays set
until a --verify passes. While it is set and no run has accounted for its deletes (a
killed run, a lost call), --purge refuses (exit 3) until a --verify attempt, pass or
fail, has been recorded (`purge_checked`); a new purge start clears that record. A
killed run can leave scratch directories `move-*` and `.*.tmp` files in WORKDIR; they
may hold lead rows: remove them by hand (the next manifest or export write removes
its own leftovers).

One run at a time. Every mode takes an exclusive, non-blocking flock on
WORKDIR/.move-run.lock before it reads the manifest and holds it to the end, so a
second run refuses at once (exit 7, a fixed message) and does nothing. The kernel
drops the lock whenever the process ends, SIGKILL included, so it cannot go stale; a
lock file left by a killed run is reused, and one that is a link, not yours, not mode
600 or not a plain file refuses (exit 3). Each manifest save re-checks the lock and
that the manifest file is the one this run last read or wrote.

Deleted set. Verification accepts a moved row that is gone from SOURCE only if this
tool's own DELETE removed it (recorded in the manifest after the call succeeded and
a SOURCE read showed the row gone) and a recovery export holds it. A deletion by
anyone else, or one whose outcome was never known, is reported and stays unaccounted
until reconciled (--restore-from-export, then --purge again; docs/deploy.md).

Exit: 0 done or dry run, 1 a step failed and this run deleted nothing, 2 usage,
3 refused (nothing deleted by this run), 4 partial purge (some chunks were deleted;
the copies and the manifest are intact; safe to run --purge again), 5 failure after a
delete (a delete may have been applied; run --verify and check the counts before
anything else; recovery is the recorded bookmark), 6 interrupted by a signal at a safe
point before any delete of this run (safe to run the same command again), 7 another
run holds the lock (this run did nothing).
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
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
EXPORT_PREFIX = "lead-recovery-"
EXISTS_SQL = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'leads' "
    "UNION ALL SELECT name FROM sqlite_master WHERE type = 'table' "
    "AND name = 'kit_events'"
)


class Failed(Exception):
    """A step failed. Carries a fixed message, never data."""


class Refused(Exception):
    """A precondition does not hold (exit 3)."""


class PartialPurge(Failed):
    """Some chunks were deleted, the rest not; safe to run --purge again (exit 4)."""


class VerifyRequired(Failed):
    """A delete may have been applied and the outcome is not proved (exit 5)."""


class Interrupted(Failed):
    """SIGINT, SIGTERM or SIGHUP arrived; the run stopped at a safe point (exit 6)."""


class LockHeld(Failed):
    """Another run holds the run lock of this work directory (exit 7)."""


LOCK_NAME = ".move-run.lock"


class RunLock:
    """An exclusive lock for the whole run, taken before the manifest is loaded.

    flock on a private file in WORKDIR (opened through the directory descriptor,
    never through a symlink). The kernel drops it when the process ends, whatever the
    cause (exit, signal, SIGKILL), so it cannot go stale; a file left behind by a
    killed run is harmless and is reused. The file is removed on a normal end, while
    the lock is still held, and every acquire checks afterwards that the path still
    names the file it locked."""

    def __init__(self, work: Path) -> None:
        self.work = work
        self.dfd = -1
        self.fd = -1
        self.stat: os.stat_result | None = None

    def _open(self) -> int:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(
                LOCK_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.dfd
            )
            os.fchmod(fd, 0o600)  # whatever the umask
            return fd
        except FileExistsError:
            pass
        try:
            return os.open(LOCK_NAME, flags, dir_fd=self.dfd)
        except FileNotFoundError:
            return -1  # removed by a finishing run between the two calls: try again
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Refused("the run lock file is a symbolic link.") from exc
            if exc.errno == errno.EISDIR:  # a directory under the lock's name
                raise Refused(
                    "the run lock file is not a private regular file."
                ) from exc
            raise

    def acquire(self) -> None:
        self.dfd = os.open(self.work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for _ in range(5):
                fd = self._open()
                if fd < 0:
                    continue
                st = os.fstat(fd)
                if (
                    not stat.S_ISREG(st.st_mode)
                    or st.st_nlink != 1
                    or st.st_uid != os.getuid()
                    or stat.S_IMODE(st.st_mode) != 0o600
                ):
                    os.close(fd)
                    raise Refused("the run lock file is not a private regular file.")
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    os.close(fd)
                    if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                        raise LockHeld(
                            "another run holds the lock on this work directory; "
                            "this run did nothing."
                        ) from exc
                    raise
                try:  # the holder before us may have removed it as it finished
                    now = os.stat(LOCK_NAME, dir_fd=self.dfd, follow_symlinks=False)
                except FileNotFoundError:
                    os.close(fd)
                    continue
                if not same_file(st, now):
                    os.close(fd)
                    continue
                self.fd, self.stat = fd, st
                return
            raise Refused("the run lock could not be taken; try again.")
        except BaseException:
            self.release()
            raise

    def check(self) -> None:
        """Still ours: the lock file is the one we locked and nobody linked it."""
        if self.fd < 0 or self.stat is None:
            raise Refused("the run lock is not held.")
        try:
            now = os.stat(LOCK_NAME, dir_fd=self.dfd, follow_symlinks=False)
        except OSError as exc:
            raise Refused("the run lock was lost.") from exc
        if not same_file(self.stat, now) or os.fstat(self.fd).st_nlink != 1:
            raise Refused("the run lock was lost.")

    def release(self) -> None:
        if self.fd >= 0:
            try:  # removed while still locked, so no one can lock the removed file
                if self.stat is not None and same_file(
                    self.stat,
                    os.stat(LOCK_NAME, dir_fd=self.dfd, follow_symlinks=False),
                ):
                    os.unlink(LOCK_NAME, dir_fd=self.dfd)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = -1
        if self.dfd >= 0:
            os.close(self.dfd)
            self.dfd = -1


# The lock this run holds (None outside run()); manifests check it on every save.
HELD: dict[str, RunLock | None] = {"lock": None}


# The handler only records the signal; nothing is written or raised from inside it, so
# a signal can never cut a manifest, export or cursor write in half. The run looks at
# the record at safe points (before each read page, each settle slice) and stops there.
STOP: dict[str, int | None] = {"signal": None}
STOP_SIGNALS = tuple(
    getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n)
)


def check_stop() -> None:
    if STOP["signal"] is not None:
        raise Interrupted("interrupted by a signal; stopped at a safe point")


def note_signal(signum: int, _frame: object) -> None:
    STOP["signal"] = signum


def timeout_seconds() -> int:
    raw = os.environ.get("MOVE_TIMEOUT", "120")
    if not raw.isdigit() or not 1 <= int(raw) <= 900:
        raise SystemExit("error: MOVE_TIMEOUT must be a whole number from 1 to 900")
    return int(raw)


def settle_seconds() -> int:
    raw = os.environ.get("MOVE_SETTLE", "5")
    if not raw.isdigit() or not 0 <= int(raw) <= 60:
        raise SystemExit("error: MOVE_SETTLE must be a whole number from 0 to 60")
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

    def call(self, args: list[str]) -> str:
        """Run one bounded wrangler command and return its stdout (never printed).

        It runs in its own session, so a Ctrl-C aimed at this tool does not also kill
        a statement in flight: the call finishes (bounded by MOVE_TIMEOUT), its outcome
        is known, and the tool stops at the next safe point."""
        try:
            done = subprocess.run(  # noqa: S603 - argv list, no shell
                [*self.cmd, *args],
                capture_output=True,
                text=True,
                timeout=self.limit,
                check=False,
                start_new_session=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise Failed("a database call timed out") from exc
        if done.returncode != 0:
            raise Failed("a database call failed")
        return done.stdout

    def execute(
        self, db: str, *, command: str | None = None, file: Path | None = None
    ) -> list[dict[str, Any]]:
        args = ["d1", "execute", db, "--remote", "--json"]
        args += ["--command", command] if command is not None else [f"--file={file}"]
        stdout = self.call(args)
        try:
            data = json.loads(stdout)
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
    """The next `limit` rows of `table` after primary key `after`, in key order.

    Every chunk of every loop reads through here, so this is the safe point at which a
    signal stops the run: between chunks, never inside a write."""
    check_stop()
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


def upsert_sql(table: str, rows: list[dict[str, Any]]) -> str:
    """Bring DEST's recorded version of each row up to SOURCE's current columns."""
    cols = COLUMNS[table]
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")
    head = f"INSERT INTO {table} ({', '.join(cols)}) VALUES "
    tail = f" ON CONFLICT(id) DO UPDATE SET {sets};"
    return (
        "\n".join(
            head + "(" + ", ".join(literal(r.get(c)) for c in cols) + ")" + tail
            for r in rows
        )
        + "\n"
    )


def where(columns: tuple[str, ...], row: dict[str, Any]) -> str:
    """Match a row by id and every column (IS, so a NULL column matches NULL)."""
    return " AND ".join(f"{c} IS {literal(row.get(c))}" for c in columns)


def guard_sql(tables: set[str]) -> str:
    """True only while every present lead table still has its exact freeze trigger.

    Part of each DELETE, so the freeze and the delete are one statement: a trigger
    dropped or replaced at any moment before it makes the delete match nothing."""
    parts = [
        f"(name = {literal(FROZEN[t][0])} AND tbl_name = {literal(t)} "
        f"AND sql = {literal(create_sql(t))})"
        for t in sorted(tables)
    ]
    return (
        "(SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND ("
        + " OR ".join(parts)
        + f")) = {len(parts)}"
    )


def delete_sql(table: str, rows: list[dict[str, Any]], guard: str) -> str:
    """Delete exactly the verified rows; a lead goes only with no event left."""
    tail = f" AND {guard};"
    if table == "leads":
        tail = (
            " AND NOT EXISTS (SELECT 1 FROM kit_events e WHERE e.lead_id = leads.id)"
            f" AND {guard};"
        )
    return (
        "\n".join(
            f"DELETE FROM {table} WHERE " + where(COLUMNS[table], r) + tail
            for r in rows
        )
        + "\n"
    )


def same_file(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def run_file(w: Wrangler, db: str, work: Path, sql: str) -> None:
    """Run `sql` through a file made exclusively in a fresh private directory.

    The directory is held open by descriptor and the file is created through it, so
    nothing is resolved by name until wrangler itself opens the path. Right before
    that, directory and file are checked again (owner, mode, and that the path still
    leads to the same inodes). The race that remains is only the instant between that
    check and wrangler's open (see the module docstring)."""
    scratch = Path(tempfile.mkdtemp(prefix="move-", dir=work))  # mode 0700, new
    try:
        dfd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            dst = os.fstat(dfd)
            if (
                not stat.S_ISDIR(dst.st_mode)
                or dst.st_uid != os.getuid()
                or stat.S_IMODE(dst.st_mode) != 0o700
                or not same_file(dst, os.lstat(scratch))
            ):
                raise Refused("the scratch directory is not a private directory.")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            fd = os.open("statements.sql", flags, 0o600, dir_fd=dfd)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(sql)
                fh.flush()
                os.fsync(fh.fileno())
                mine = os.fstat(fh.fileno())
                path = scratch / "statements.sql"
                try:
                    by_path = os.lstat(path)
                    dir_now = os.lstat(scratch)
                except OSError as exc:
                    raise Refused("the statement file changed before it ran.") from exc
                if (
                    not same_file(mine, by_path)
                    or not same_file(dst, dir_now)
                    or mine.st_nlink != 1
                    or mine.st_uid != os.getuid()
                    or stat.S_IMODE(mine.st_mode) != 0o600
                ):
                    raise Refused("the statement file changed before it ran.")
        finally:
            os.close(dfd)
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


UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# A D1 Time Travel bookmark, as the documentation shows it (three 8-digit hex groups
# and a 32-digit one). Read 2026-11-25: Cloudflare D1 docs, reference/time-travel.
BOOKMARK = re.compile(r"[0-9a-f]{8}-[0-9a-f]{8}-[0-9a-f]{8}-[0-9a-f]{32}")


def platform_identity(w: Wrangler, name: str) -> tuple[str, str]:
    """(platform id, name) of a database as the platform says (`d1 info --json`)."""
    try:
        data = json.loads(w.call(["d1", "info", name, "--json"]))
    except ValueError as exc:
        raise Failed("unexpected answer from the database") from exc
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    uid = data.get("uuid") if isinstance(data, dict) else None
    got = data.get("name") if isinstance(data, dict) else None
    if (
        not isinstance(uid, str)
        or not UUID.match(uid.lower())
        or not isinstance(got, str)
    ):
        raise Failed("unexpected answer from the database")
    return uid.lower(), got


def check_platform_ids(w: Wrangler, args: argparse.Namespace) -> None:
    """Each name must be the database whose platform id the operator passed."""
    for role, name, want in (
        ("SOURCE", args.source, args.source_id),
        ("DEST", args.dest, args.dest_id),
    ):
        uid, got = platform_identity(w, name)
        if got != name or uid != want:
            raise Refused(
                f"{role} is not the database you named: its platform id or name "
                "differs from the id you passed (read it with d1 info --json)."
            )
    if args.source_id == args.dest_id:
        raise Refused("SOURCE and DEST have the same platform id.")


def take_bookmark(w: Wrangler, db: str) -> str:
    """The current Time Travel bookmark of `db` (a read-only call)."""
    out = w.call(["d1", "time-travel", "info", db, "--json"])
    try:
        data = json.loads(out)
    except ValueError:
        data = None
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    if isinstance(data, dict):
        mark = data.get("bookmark")
        if isinstance(mark, str) and BOOKMARK.fullmatch(mark):
            return mark
    found = set(BOOKMARK.findall(out))  # plain text output: exactly one bookmark
    if len(found) == 1:
        return str(found.pop())
    raise Failed("no usable Time Travel bookmark was returned")


def write_private(dfd: int, name: str, body: str, check: Any = None) -> None:
    """Write `name` in the directory `dfd` so it is whole or absent, never half.

    A new file with a name of its own is created exclusively through the directory
    descriptor (never through a symlink), its mode is set to 600 before any content
    goes in, the content is written and fsynced, `check` (if any) reads it back, then
    it is renamed over `name` and the directory is fsynced so the rename is durable.
    A crash or a signal leaves either the old file or the new one; the temporary file
    is removed on any failure."""
    tmp = f".{name}.{secrets.token_hex(6)}.tmp"
    try:
        fd = os.open(
            tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd
        )
        with os.fdopen(fd, "w+", encoding="utf-8") as fh:
            os.fchmod(fh.fileno(), 0o600)  # before the content, whatever the umask
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
            if check is not None:
                check(fh)
        os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass
        raise
    os.fsync(dfd)


def sweep_stale(dfd: int, prefix: str) -> None:
    """Remove temporary files a killed run left behind (`.<prefix>*.tmp`, plain files
    only; a link goes, never its target)."""
    for entry in os.listdir(dfd):
        if entry.startswith(f".{prefix}") and entry.endswith(".tmp"):
            try:
                st = os.stat(entry, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) or stat.S_ISLNK(st.st_mode):
                    os.unlink(entry, dir_fd=dfd)
            except OSError:
                pass


def signature(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class Manifest:
    """The private record of what was copied: digests and cursors, never a row.

    Bound to the two databases' platform ids (read from the platform, passed by the
    operator and checked), never to a table inside the data, which a restore or a
    clone would copy along."""

    def __init__(
        self, work: Path, source: str, dest: str, src_id: str, dst_id: str
    ) -> None:
        self.path = work / MANIFEST
        self.source = source
        self.dest = dest
        self.src_id = src_id
        self.dst_id = dst_id
        self.state = "copying"
        self.cursor: dict[str, Any] = {}
        self.digests: dict[str, dict[str, str]] = {t: {} for t, _ in TABLES}
        self.bookmarks: list[str] = []
        self.bookmark_attempts: list[dict[str, str]] = []
        self.recovery_exports: list[str] = []
        # Digests this manifest once held for a row that was re-moved with new data:
        # an export made before the re-move is stale, not foreign.
        self.superseded: dict[str, dict[str, list[str]]] = {t: {} for t, _ in TABLES}
        self.run_id = secrets.token_hex(16)
        # True from just before the first DELETE of an attempt until a --verify passes:
        # a durable "a delete may have been applied; verify first" for a run that was
        # killed (SIGKILL, power loss) and so could not say it itself.
        self.purge_open = False
        # True once a run that knows how its deletes ended has said so (a purge that
        # stopped with every outcome known) or a --verify attempt, pass or fail, has
        # looked at the source since the purge began. A purge cannot start again while
        # purge_open is set and this is not.
        self.purge_checked = False
        # Rows this tool's own DELETE removed: attempt id -> [table, id, digest],
        # recorded only after the call succeeded and a source read showed the row gone.
        self.deleted: dict[str, list[list[str]]] = {}
        self.exists = False
        self.lock = HELD["lock"]
        self.seen: tuple[int, int, int, int] | None = None  # the file as we left it

    def load(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise Refused("the manifest is a symbolic link.") from exc
            raise
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            st = os.fstat(fh.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise Refused("the manifest is not a plain regular file.")
            if st.st_uid != os.getuid():
                raise Refused("the manifest is not owned by you.")
            if stat.S_IMODE(st.st_mode) != 0o600:
                raise Refused("the manifest must be mode 600 (chmod 600).")
            self.seen = signature(st)
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise Refused(
                    "the manifest is unreadable; resolve it by hand."
                ) from exc
        ok = (
            isinstance(data, dict)
            and data.get("version") == 3
            and data.get("state") in ("copying", "copied")
            and isinstance(data.get("cursor"), dict)
            and isinstance(data.get("digests"), dict)
            and all(isinstance(data["digests"].get(t), dict) for t, _ in TABLES)
            and all(
                isinstance(data.get(k), str) and UUID.match(data[k])
                for k in ("src_id", "dst_id")
            )
            and isinstance(data.get("bookmarks"), list)
            and all(
                isinstance(b, str) and BOOKMARK.fullmatch(b) for b in data["bookmarks"]
            )
        )
        if not ok:
            raise Refused("the manifest is unreadable; resolve it by hand.")
        if data.get("source") != self.source or data.get("dest") != self.dest:
            raise Refused("the manifest belongs to other databases; resolve by hand.")
        if (data["src_id"], data["dst_id"]) != (self.src_id, self.dst_id):
            raise Refused(
                "the manifest belongs to other databases (the platform ids differ); "
                "resolve by hand."
            )
        self.state = data["state"]
        self.cursor = data["cursor"]
        self.digests = {t: dict(data["digests"][t]) for t, _ in TABLES}
        self.bookmarks = list(data["bookmarks"])
        attempts = data.get("bookmark_attempts", [])
        exports = data.get("recovery_exports", [])
        run_id = data.get("run_id")
        if not isinstance(attempts, list) or not all(
            isinstance(a, dict)
            and isinstance(a.get("bookmark"), str)
            and BOOKMARK.fullmatch(a["bookmark"])
            and isinstance(a.get("timestamp"), str)
            for a in attempts
        ):
            raise Refused("the manifest is unreadable; resolve it by hand.")
        if not isinstance(exports, list) or not all(
            isinstance(x, str) for x in exports
        ):
            raise Refused("the manifest is unreadable; resolve it by hand.")
        old = data.get("superseded", {})
        if not isinstance(old, dict) or not all(
            isinstance(old.get(t, {}), dict)
            and all(
                isinstance(v, list) and all(isinstance(d, str) for d in v)
                for v in old.get(t, {}).values()
            )
            for t, _ in TABLES
        ):
            raise Refused("the manifest is unreadable; resolve it by hand.")
        self.superseded = {
            t: {k: list(v) for k, v in old.get(t, {}).items()} for t, _ in TABLES
        }
        self.bookmark_attempts = list(attempts)
        self.recovery_exports = list(exports)
        self.run_id = run_id if isinstance(run_id, str) else self.run_id
        open_flag = data.get("purge_open", False)
        if not isinstance(open_flag, bool):
            raise Refused("the manifest is unreadable; resolve it by hand.")
        self.purge_open = open_flag
        checked = data.get("purge_checked", False)
        gone = data.get("deleted", {})
        if (
            not isinstance(checked, bool)
            or not isinstance(gone, dict)
            or not all(
                isinstance(k, str)
                and isinstance(v, list)
                and all(
                    isinstance(e, list)
                    and len(e) == 3
                    and all(isinstance(x, str) for x in e)
                    for e in v
                )
                for k, v in gone.items()
            )
        ):
            raise Refused("the manifest is unreadable; resolve it by hand.")
        self.purge_checked = checked
        self.deleted = {k: [list(e) for e in v] for k, v in gone.items()}
        self.exists = True

    def save(self) -> None:
        """Write the manifest whole or not at all (write_private): a signal or a crash
        leaves the previous file or the new one, never half of either.

        Under the run lock it first re-checks that the lock is still ours and that the
        file is the one this object last read or wrote, so a copy of the manifest that
        another writer changed meanwhile is refused, never written over it."""
        if self.lock is not None:
            self.lock.check()
            try:
                now = signature(os.stat(self.path, follow_symlinks=False))
            except FileNotFoundError:
                now = None
            if now != self.seen:
                raise Refused("the manifest changed while this run held the lock.")
        body = json.dumps(
            {
                "version": 3,
                "source": self.source,
                "dest": self.dest,
                "src_id": self.src_id,
                "dst_id": self.dst_id,
                "bookmarks": self.bookmarks,
                "bookmark_attempts": self.bookmark_attempts,
                "recovery_exports": self.recovery_exports,
                "superseded": self.superseded,
                "run_id": self.run_id,
                "purge_open": self.purge_open,
                "purge_checked": self.purge_checked,
                "deleted": self.deleted,
                "state": self.state,
                "cursor": self.cursor,
                "digests": self.digests,
            }
        )
        dfd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            sweep_stale(dfd, MANIFEST)  # a killed run's leftover
            write_private(dfd, MANIFEST, body)
        finally:
            os.close(dfd)
        self.seen = signature(os.stat(self.path, follow_symlinks=False))
        self.exists = True

    def known(self, table: str, row: dict[str, Any]) -> str | None:
        """The digest recorded for this row's id, or None when it is new."""
        return self.digests[table].get(str(row.get("id")))

    def record(self, table: str, row: dict[str, Any]) -> None:
        """Record (or replace) a row's digest. Called only after the read-back proved
        DEST holds the row exactly as SOURCE has it now."""
        key = str(row.get("id"))
        new = digest(table, row)
        old = self.digests[table].get(key)
        if old is not None and old != new:
            trail = self.superseded[table].setdefault(key, [])
            if old not in trail:
                trail.append(old)
        self.digests[table][key] = new

    def is_stale(self, table: str, key: str, row_digest: str) -> bool:
        """True when this digest was this row's recorded one before a re-move."""
        return row_digest in self.superseded[table].get(key, [])


def check_ancestors(path: str) -> None:
    """Every directory from / down to `path`: a real directory (no symlink), owned by
    you or root, and not writable by group or others (a sticky root-owned directory
    such as /tmp is allowed). Walked with directory descriptors and O_NOFOLLOW, so
    the answer is about the directories actually opened, not about a name."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    names = [p for p in path.split(os.sep) if p]
    fd = os.open(os.sep, flags)
    try:
        for name in [None, *names]:
            if name is not None:
                try:
                    nxt = os.open(name, flags, dir_fd=fd)
                except OSError as exc:
                    raise Refused("WORKDIR has a symlink or missing parent.") from exc
                os.close(fd)
                fd = nxt
            st = os.fstat(fd)
            if st.st_uid not in (0, os.getuid()):
                raise Refused("a parent of WORKDIR is owned by another user.")
            open_to_others = bool(st.st_mode & 0o022)
            sticky_root = bool(st.st_mode & stat.S_ISVTX) and st.st_uid == 0
            if open_to_others and not sticky_root:
                raise Refused("a parent of WORKDIR is writable by group or others.")
    finally:
        os.close(fd)


def check_workdir(path: str) -> Path:
    absolute = os.path.abspath(path)  # lexical; the walk below proves no symlink
    work = Path(absolute)
    if not work.is_dir() or work.is_symlink():
        raise Refused("WORKDIR is not a real directory.")
    check_ancestors(absolute)
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


def accounted_rows(
    w: Wrangler,
    source: str,
    work: Path,
    manifest: Manifest,
    size: int,
) -> set[tuple[str, str, str]]:
    """Manifest rows still in SOURCE, or deleted by this tool and held by an export.

    A row absent from SOURCE counts only when this tool's own DELETE removed it (the
    manifest's deleted set, written after the call succeeded and a source read showed
    the row gone) AND a recorded recovery export holds it. An export alone is not
    enough: it lists rows eligible for deletion, and a row another process removed
    after the export is in it too."""
    accounted: set[tuple[str, str, str]] = set()
    removed = {
        (e[0], e[1], e[2]) for entries in manifest.deleted.values() for e in entries
    }
    exported: set[tuple[str, str, str]] = set()
    have = present_tables(w, source)
    for table, _ in TABLES:
        after: object | None = None
        while table in have:
            rows = page(w, source, table, after, size)
            if not rows:
                break
            for row in rows:
                key = str(row.get("id"))
                row_digest = digest(table, row)
                if manifest.digests[table].get(key) == row_digest:
                    accounted.add((table, key, row_digest))
            after = rows[-1].get("id")
    for name in manifest.recovery_exports:
        if Path(name).name != name or not name.startswith(EXPORT_PREFIX):
            raise Refused("the manifest contains an invalid recovery export name.")
        for item in read_recovery_export(work / name, manifest):
            table = item["table"]
            row = item["row"]
            exported.add((table, str(row.get("id")), item["digest"]))
    return accounted | (removed & exported)


def check_dest(
    w: Wrangler,
    source: str,
    dest: str,
    work: Path,
    manifest: Manifest,
    size: int,
) -> None:
    """DEST must match the manifest and each row must still be accounted for."""
    have = present_tables(w, dest)
    matched: set[tuple[str, str, str]] = set()
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
                key = str(r.get("id"))
                row_digest = digest(table, r)
                got = want.pop(key, None)
                if got is None:
                    extra += 1
                elif got != row_digest:
                    different += 1
                else:
                    matched.add((table, key, row_digest))
            after = rows[-1].get("id")
        missing += len(want)
        print(f"dest {table}: {seen} rows, manifest {len(manifest.digests[table])}")
    print(
        f"dest check: {missing} missing, {different} different, {extra} extra "
        "against the manifest"
    )
    if missing or different or extra:
        raise Failed("the leads database does not hold exactly the moved rows")
    if not manifest.bookmarks:
        return

    accounted = accounted_rows(w, source, work, manifest, size)
    unaccounted = len(matched - accounted)
    print(f"removed at source or unknown: {unaccounted}")
    if unaccounted:
        raise Failed(
            f"{unaccounted} destination row(s) are removed at source or unknown; "
            "the operator must decide whether to keep the moved rows or remove them "
            "with the documented command"
        )


def check_frozen(w: Wrangler, db: str) -> None:
    if present_tables(w, db) - frozen_tables(w, db):
        raise Refused("SOURCE is not frozen against inserts; run --freeze first.")


def new_manifest(work: Path, args: argparse.Namespace) -> Manifest:
    return Manifest(work, args.source, args.dest, args.source_id, args.dest_id)


def note_checked(work: Path, args: argparse.Namespace) -> None:
    """A --verify attempt has read the source: record that, pass or fail, so a purge
    that was cut off can be started again (the gate in run()). Does not clear
    purge_open; only a passing verification does."""
    m = new_manifest(work, args)
    try:
        m.load()
    except Refused:
        return  # the verification itself reports the bad manifest
    if m.exists and m.purge_open and not m.purge_checked:
        m.purge_checked = True
        m.save()


def verify(
    w: Wrangler, args: argparse.Namespace, work: Path, *, record: bool = False
) -> int:
    """The cutover check: exit 0 only when the move is complete and final.

    `record` (the --verify mode only) durably notes that an attempt was made, as soon
    as it has read the source, whether it then passes or fails."""
    source, dest = args.source, args.dest
    size = batch_size()
    tables = present_tables(w, source)
    held = counts(w, source)
    unfrozen = sorted(tables - frozen_tables(w, source))
    if record:
        note_checked(work, args)
    print(f"analytics holds {held['leads']} leads and {held['kit_events']} kit events")
    print(f"lead tables not frozen against inserts: {len(unfrozen)}")
    if any(held.values()):
        raise Failed("lead rows remain in the analytics database; run the move")
    if unfrozen:
        raise Failed("a lead table can still take inserts from an older Worker; freeze")
    manifest = new_manifest(work, args)
    manifest.load()
    if not manifest.exists:
        if any(counts(w, dest).values()):
            raise Failed("no manifest: the leads database cannot be proved complete")
        print("no manifest and no lead rows anywhere: nothing was ever moved")
    elif manifest.state != "copied":
        raise Failed("the manifest says a copy did not finish; run the move again")
    else:
        check_dest(w, source, dest, work, manifest, size)
        if manifest.purge_open:  # the proof the earlier interrupted purge needed
            manifest.purge_open = False
            manifest.save()
    print("cutover verified: moved completely, and no older Worker can store a lead.")
    return 0


def check_recorded_dest(
    w: Wrangler, dest: str, table: str, rows: list[dict[str, Any]], m: Manifest
) -> None:
    """Rows SOURCE changed since they were recorded: DEST may only hold the recorded
    version (or nothing). Anything else is a hand decision, never overwritten."""
    if not rows:
        return
    back = {
        str(r.get("id")): r for r in by_ids(w, dest, table, [r.get("id") for r in rows])
    }
    for r in rows:
        got = back.get(str(r.get("id")))
        if got is not None and digest(table, got) != m.known(table, r):
            raise Failed(
                "a destination row differs from its recorded version; decide by hand"
            )


INTEGER = re.compile(r"(?:0|[1-9][0-9]{0,18})")
MAX_EVENT_ID = 2**63 - 1


def at_or_before(table: str, key: str, cursor: object) -> bool:
    """Is manifest key `key` at or before `cursor` in the table's key order?

    kit_events ids are integers of varying width: compared as integers (strictly
    validated, never as text, where "10" sorts before "2"). Lead ids are fixed-width
    strings and compare as text, as the database orders them."""
    if table != "kit_events":
        return key <= str(cursor)
    if (
        not isinstance(cursor, int)
        or isinstance(cursor, bool)
        or not 0 <= cursor <= MAX_EVENT_ID
        or not INTEGER.fullmatch(key)
        or int(key) > MAX_EVENT_ID
    ):
        raise Refused("the manifest is unreadable; resolve it by hand.")
    return int(key) <= cursor


def check_resume_prefix(
    w: Wrangler, dest: str, table: str, cursor: object, m: Manifest
) -> None:
    """Refuse to continue past a cursor whose destination prefix changed."""
    columns = ", ".join(COLUMNS[table])
    rows = w.execute(
        dest,
        command=(
            f"SELECT {columns} FROM {table} WHERE id <= {literal(cursor)} ORDER BY id"
        ),
    )
    expected = {
        key: value
        for key, value in m.digests[table].items()
        if at_or_before(table, key, cursor)
    }
    actual = {str(row.get("id")): digest(table, row) for row in rows}
    if actual != expected:
        raise Refused(
            "the destination prefix at or before the saved cursor does not match "
            "the manifest; rerun with --restart-copy to copy from the beginning"
        )


def check_resume_prefixes(w: Wrangler, dest: str, m: Manifest) -> None:
    """Check every saved table cursor before advancing any of them."""
    for table, _ in TABLES:
        cursor = m.cursor.get(table)
        if cursor is not None:
            check_resume_prefix(w, dest, table, cursor, m)


def copy_chunks(w: Wrangler, args: argparse.Namespace, work: Path, m: Manifest) -> None:
    """Copy and read back in bounded chunks, recording each chunk before the next.

    A rerun reads every SOURCE row again and recomputes its digest: a row that changed
    since it was recorded is brought up to date in DEST (an upsert, only while DEST
    still holds the recorded version) and its digest is replaced after the read-back,
    never skipped."""
    size = batch_size()
    if args.restart_copy:
        m.state, m.cursor = "copying", {}
        print("restarting the copy from the beginning as requested")
    elif m.state == "copying" and m.exists:
        check_resume_prefixes(w, args.dest, m)
        print("resuming an unfinished copy from the recorded cursor")
    else:
        m.state, m.cursor = "copying", {}
    m.save()
    total = {t: 0 for t, _ in TABLES}
    updated = 0
    for table, cols in TABLES:
        after = m.cursor.get(table)
        while True:
            rows = page(w, args.source, table, after, size)
            if not rows:
                break
            changed = [
                r for r in rows if m.known(table, r) not in (None, digest(table, r))
            ]
            check_recorded_dest(w, args.dest, table, changed, m)
            changed_ids = {str(r.get("id")) for r in changed}
            fresh = [r for r in rows if str(r.get("id")) not in changed_ids]
            run_file(
                w,
                args.dest,
                work,
                (insert_sql(table, fresh) if fresh else "")
                + (upsert_sql(table, changed) if changed else ""),
            )
            back = by_ids(w, args.dest, table, [r.get("id") for r in rows])
            short = missing_from(back, rows, cols)
            if short:
                print(
                    f"verify: {short} {table} rows are not in dest exactly as in source"
                )
                raise Failed("copy does not match; nothing was deleted from analytics")
            for r in rows:
                m.record(table, r)
            updated += len(changed)
            after = rows[-1].get("id")
            m.cursor[table] = after
            m.save()
            total[table] += len(rows)
    m.state = "copied"
    m.save()
    if updated:
        print(f"re-copied {updated} rows that changed in analytics since the record")
    print(
        f"copied {total['leads']} leads and {total['kit_events']} kit events; "
        "verify: 0 leads and 0 kit events are not in dest exactly as in source"
    )


def require_frozen(w: Wrangler, db: str) -> None:
    """The freeze must still be there; a purge in progress stops when it is not."""
    if present_tables(w, db) - frozen_tables(w, db):
        raise Failed("the freeze is gone; nothing more was deleted. Freeze and rerun")


def snapshot(w: Wrangler, db: str, size: int) -> tuple[dict[str, int], str]:
    """Row count per lead table and one SHA-256 over every row's digest, in id order."""
    have = present_tables(w, db)
    total = hashlib.sha256()
    seen: dict[str, int] = {}
    for table, _ in TABLES:
        seen[table] = 0
        after: object | None = None
        while table in have:
            rows = page(w, db, table, after, size)
            if not rows:
                break
            for r in rows:
                seen[table] += 1
                total.update(f"{table}:{r.get('id')}:{digest(table, r)}\n".encode())
            after = rows[-1].get("id")
    return seen, total.hexdigest()


def prove_quiet(w: Wrangler, args: argparse.Namespace) -> None:
    """Both databases must read the same twice, MOVE_SETTLE seconds apart.

    This notices a writer that is active in the window; it cannot prove none is
    (module docstring). A difference refuses the purge before any delete."""
    size = batch_size()
    first = (snapshot(w, args.source, size), snapshot(w, args.dest, size))
    time.sleep(settle_seconds())  # a signal is acted on right after it (at most 60 s)
    check_stop()
    second = (snapshot(w, args.source, size), snapshot(w, args.dest, size))
    if first != second:
        raise Refused(
            "a database changed during the settle wait: the window is not quiet. "
            "Stop every writer (KIT_KILL, SCHEDULED_KILL, older Workers) and run again."
        )
    print(
        f"window: both databases read the same twice, {settle_seconds()} s apart "
        "(this notices a writer; it cannot prove there is none)"
    )


def record_bookmark(w: Wrangler, args: argparse.Namespace, m: Manifest) -> str:
    """Take SOURCE's Time Travel bookmark, store it, say how to restore."""
    mark = take_bookmark(w, args.source)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    m.bookmarks.append(mark)
    m.bookmark_attempts.append({"bookmark": mark, "timestamp": timestamp})
    m.save()
    for attempt in m.bookmark_attempts[:-1]:
        print(
            f"earlier recovery bookmark: {attempt['bookmark']} ({attempt['timestamp']})"
        )
    print(f"current attempt recovery bookmark: {mark} ({timestamp})")
    print(
        "last-resort full-database restore command (it rolls back every later write):"
    )
    print(f"  npx wrangler d1 time-travel restore {args.source} --bookmark={mark}")
    return mark


def recovery_rows(w: Wrangler, source: str, m: Manifest) -> list[dict[str, Any]]:
    """Full SOURCE rows that currently match the manifest and may be deleted."""
    result: list[dict[str, Any]] = []
    have = present_tables(w, source)
    for table, _ in TABLES:
        after: object | None = None
        while table in have:
            rows = page(w, source, table, after, batch_size())
            if not rows:
                break
            for row in rows:
                row_digest = digest(table, row)
                if m.digests[table].get(str(row.get("id"))) == row_digest:
                    result.append({"table": table, "digest": row_digest, "row": row})
            after = rows[-1].get("id")
    return result


def validate_export(data: object, m: Manifest) -> list[dict[str, Any]]:
    """Validate an export without ever including its row values in an error."""
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("run_id") != m.run_id
        or data.get("src_id") != m.src_id
        or data.get("dst_id") != m.dst_id
        or not isinstance(data.get("rows"), list)
    ):
        raise Refused("the recovery export is not bound to this manifest.")
    raw_rows = data["rows"]
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if (
            not isinstance(item, dict)
            or item.get("table") not in COLUMNS
            or not isinstance(item.get("row"), dict)
            or not isinstance(item.get("digest"), str)
        ):
            raise Refused("the recovery export is unreadable.")
        table = item["table"]
        row = item["row"]
        key = str(row.get("id"))
        if digest(table, row) != item["digest"] or not (
            m.digests[table].get(key) == item["digest"]
            or m.is_stale(table, key, item["digest"])
        ):
            raise Refused("the recovery export does not match the manifest.")
        rows.append(item)
    return rows


def write_recovery_export(
    w: Wrangler, args: argparse.Namespace, work: Path, m: Manifest
) -> Path:
    """Create, fsync and re-read a private export before any DELETE is sent."""
    rows = recovery_rows(w, args.source, m)
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{EXPORT_PREFIX}{timestamp}-{secrets.token_hex(6)}.json"
    body = json.dumps(
        {
            "version": 1,
            "run_id": m.run_id,
            "src_id": m.src_id,
            "dst_id": m.dst_id,
            "rows": rows,
        },
        separators=(",", ":"),
    )

    def reread(fh: Any) -> None:
        """Right after the fsync, before the rename: a private regular file whose
        content parses and matches the manifest."""
        st = os.fstat(fh.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_nlink != 1
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise Refused("the recovery export is not a private regular file.")
        fh.seek(0)
        try:
            validate_export(json.load(fh), m)
        except ValueError as exc:
            raise Refused("the recovery export is unreadable.") from exc

    dfd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        sweep_stale(dfd, EXPORT_PREFIX)  # a killed run's half-written export
        write_private(dfd, name, body, reread)
    finally:
        os.close(dfd)
    m.recovery_exports.append(name)
    m.save()
    print(f"private recovery export written: {work / name}")
    return work / name


def read_recovery_export(path: Path, m: Manifest) -> list[dict[str, Any]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Refused("a recorded recovery export cannot be opened.") from exc
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if (
            not stat.S_ISREG(st.st_mode)
            or st.st_uid != os.getuid()
            or st.st_nlink != 1
            or stat.S_IMODE(st.st_mode) != 0o600
        ):
            raise Refused("a recovery export is not a private regular file.")
        try:
            return validate_export(json.load(fh), m)
        except ValueError as exc:
            raise Refused("the recovery export is unreadable.") from exc


def restore_from_exports(
    w: Wrangler, args: argparse.Namespace, work: Path, m: Manifest
) -> int:
    if not m.exists or not m.recovery_exports:
        raise Refused("no recovery export is recorded in the manifest.")
    restored = 0
    # Every export is read and validated first. A row re-moved with new data after an
    # export was made is exported again with its new digest; only the current digest
    # of each (table, id) is kept, once. Then all current leads go back before any
    # current event, whichever export held them (events refer to leads).
    current: dict[tuple[str, str], dict[str, Any]] = {}
    for name in m.recovery_exports:
        if Path(name).name != name or not name.startswith(EXPORT_PREFIX):
            raise Refused("the manifest contains an invalid recovery export name.")
        for item in read_recovery_export(work / name, m):
            key = str(item["row"].get("id"))
            if m.digests[item["table"]].get(key) == item["digest"]:
                current[(item["table"], key)] = item["row"]
    for table, _ in TABLES:
        check_stop()  # between tables; the inserts are idempotent, rerun resumes
        table_rows = [row for (t, _key), row in current.items() if t == table]
        size = batch_size()
        for start in range(0, len(table_rows), size):
            chunk = table_rows[start : start + size]
            existing = {
                str(row.get("id")): row
                for row in by_ids(w, args.source, table, [r.get("id") for r in chunk])
            }
            missing: list[dict[str, Any]] = []
            for row in chunk:
                got = existing.get(str(row.get("id")))
                if got is None:
                    missing.append(row)
                elif digest(table, got) != digest(table, row):
                    raise Refused(
                        "a source row has an exported primary key but different data."
                    )
            if missing:
                run_file(w, args.source, work, insert_sql(table, missing))
                restored += len(missing)
            back = by_ids(w, args.source, table, [r.get("id") for r in chunk])
            if missing_from(back, chunk, COLUMNS[table]):
                raise Failed("recovery export read-back verification failed")
    print(f"recovery export verified; {restored} missing rows restored")
    return 0


def confirm_dest(
    w: Wrangler, dest: str, table: str, rows: list[dict[str, Any]], m: Manifest
) -> None:
    """Right before a delete: DEST holds each of these rows, equal to the manifest."""
    back = {
        str(r.get("id")): r for r in by_ids(w, dest, table, [r.get("id") for r in rows])
    }
    for r in rows:
        key = str(r.get("id"))
        got = back.get(key)
        if got is None or digest(table, got) != m.digests[table].get(key):
            raise Failed(
                "the leads database no longer holds a row about to be deleted; "
                "nothing more was deleted"
            )


class Progress:
    """What this run sent: delete statements acknowledged, and ones whose outcome is
    unknown (the call failed after it may have reached the database)."""

    def __init__(self) -> None:
        self.sent = 0
        self.unknown = 0
        self.attempt = secrets.token_hex(8)  # keys this attempt's deleted rows


def purge_chunks(
    w: Wrangler,
    args: argparse.Namespace,
    work: Path,
    m: Manifest,
    progress: Progress,
) -> None:
    """Delete only rows whose every column matches a recorded digest, in chunks.

    Refuses without a recorded recovery bookmark. Each chunk is re-proved where it
    matters (rows read back from DEST immediately before their delete, the freeze
    inside the delete itself): defense in depth, not an atomic proof."""
    if not m.bookmarks:
        raise Refused("no Time Travel bookmark of SOURCE is recorded; nothing deleted.")
    size = batch_size()
    guard = guard_sql(present_tables(w, args.source))
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
                require_frozen(w, args.source)
                confirm_dest(w, args.dest, table, known, m)
                try:
                    run_file(w, args.source, work, delete_sql(table, known, guard))
                except Failed:
                    progress.unknown += 1  # it may have reached the database
                    raise
                progress.sent += 1
                acknowledge_deleted(w, args.source, table, known, m, progress)
                require_frozen(w, args.source)


def acknowledge_deleted(
    w: Wrangler,
    source: str,
    table: str,
    sent: list[dict[str, Any]],
    m: Manifest,
    progress: Progress,
) -> None:
    """Record, durably, the rows this tool's DELETE removed: its call succeeded and a
    fresh SOURCE read shows each row gone. A row still there (a lead that kept an
    event, a row that no longer matched) is not recorded. A call whose outcome is
    unknown never reaches here, so its rows stay unaccounted until reconciled with
    --restore-from-export and a rerun (docs/deploy.md)."""
    back = {
        str(r.get("id")) for r in by_ids(w, source, table, [r.get("id") for r in sent])
    }
    gone = [
        [table, str(r.get("id")), digest(table, r)]
        for r in sent
        if str(r.get("id")) not in back
    ]
    if gone:
        m.deleted.setdefault(progress.attempt, []).extend(gone)
        m.save()


def classify(
    w: Wrangler,
    args: argparse.Namespace,
    exc: Exception,
    progress: Progress,
    before: int,
) -> Exception:
    """Say which exit a failure belongs to, from what this run actually sent."""
    if isinstance(exc, (PartialPurge, VerifyRequired)) or not (
        progress.sent or progress.unknown
    ):
        return exc  # no delete statement was ever sent: nothing was deleted
    verify_msg = (
        "failure after a delete: a delete may have been applied. Run --verify and "
        "compare the counts before anything else; recovery is the recorded bookmark."
    )
    if progress.unknown:
        return VerifyRequired(verify_msg)
    try:
        now = sum(counts(w, args.source).values())
    except (Failed, Refused, OSError):
        return VerifyRequired(verify_msg)
    if now >= before:
        return exc  # statements were acknowledged but removed nothing
    detail = str(exc) if isinstance(exc, (Failed, Refused)) else "a local error"
    return PartialPurge(
        f"partial purge: some chunks were deleted and the rest were not ({detail}). "
        "The copies and the manifest are intact; it is safe to run --purge again."
    )


def note_reconciled(m: Manifest) -> None:
    """A purge that ends knowing the outcome of every delete it sent (nothing sent, all
    acknowledged, or counted afterwards) may be run again without a --verify first.
    Best effort: if the write fails, the gate simply stays closed."""
    if m.purge_open and not m.purge_checked:
        m.purge_checked = True
        try:
            m.save()
        except (Refused, OSError):
            pass


def purge_phase(
    w: Wrangler, args: argparse.Namespace, work: Path, m: Manifest, before: int
) -> None:
    progress = Progress()
    try:
        check_frozen(w, args.source)
        check_dest(w, args.source, args.dest, work, m, batch_size())
        prove_quiet(w, args)
        record_bookmark(w, args, m)
        try:
            write_recovery_export(w, args, work, m)
        except Interrupted:
            raise
        except (Failed, OSError, UnicodeError) as exc:
            raise Refused(
                "the recovery export could not be written; nothing was deleted."
            ) from exc
        check_stop()
        m.purge_open = True  # durable before the first DELETE; --verify clears it
        m.purge_checked = False  # a new attempt: a --verify (or this run) must look
        m.save()
        purge_chunks(w, args, work, m, progress)
        left = counts(w, args.source)
        print(f"source now holds {left['leads']} leads, {left['kit_events']} events")
        if any(left.values()):
            rows_left = (
                "rows remain in the analytics database (added or changed after the "
                "copy, so never verified); stop older Workers and run again"
            )
            if sum(left.values()) < before:
                raise PartialPurge(
                    f"partial purge: some chunks were deleted; {rows_left}. The copies "
                    "and the manifest are intact; it is safe to run --purge again."
                )
            raise Failed(rows_left)
        try:
            verify(w, args, work)  # the final check, in this same run
        except (Failed, Refused, OSError) as exc:  # Interrupted is a Failed
            if sum(left.values()) < before:
                raise VerifyRequired(
                    "the purge ran but the final verification failed: run --verify "
                    "and compare the counts; recovery is the recorded bookmark."
                ) from exc
            raise
    except (Failed, Refused, OSError, UnicodeError) as exc:
        wrapped = classify(w, args, exc, progress, before)
        if not isinstance(wrapped, VerifyRequired):
            note_reconciled(m)  # every delete outcome of this run is known
        if wrapped is exc:
            raise
        raise wrapped from exc


def run(args: argparse.Namespace) -> int:
    w = Wrangler()
    work = check_workdir(args.workdir)
    lock = RunLock(work)
    lock.acquire()  # before anything reads or writes the manifest; held to the end
    HELD["lock"] = lock
    try:
        return locked_run(w, work, args)
    finally:
        HELD["lock"] = None
        lock.release()


def locked_run(w: Wrangler, work: Path, args: argparse.Namespace) -> int:
    check_platform_ids(w, args)
    if roles(w, args.source) != ["analytics"]:
        raise Refused("SOURCE did not identify itself as the analytics database.")
    if roles(w, args.dest) != ["leads"]:
        raise Refused("DEST did not identify itself as the leads database.")

    if args.freeze:
        return freeze(w, args.source)
    if args.verify:
        return verify(w, args, work, record=True)

    if args.restore_from_export:
        manifest = new_manifest(work, args)
        manifest.load()
        return restore_from_exports(w, args, work, manifest)

    held = counts(w, args.source)
    print(f"source holds {held['leads']} leads and {held['kit_events']} kit events")
    if args.purge:
        check_frozen(w, args.source)  # before any copy or delete, even when empty
        if not any(held.values()):
            # An empty source proves nothing by itself: a rerun after a purge is done
            # only if the same final check passes (manifest, destination, freeze).
            print("nothing left to move: proving the earlier move instead")
            verify(w, args, work)
            print("done: the lead rows live only in the leads database now.")
            return 0
    if not any(held.values()):
        print("nothing to move: the analytics database holds no lead rows")
        return 0
    if not (args.copy or args.purge):
        print("dry run: nothing was written or deleted.")
        print("run again with --copy, or --purge to also delete the analytics rows.")
        return 0

    manifest = new_manifest(work, args)
    manifest.load()
    if args.purge and manifest.purge_open and not manifest.purge_checked:
        # An earlier purge may have sent a delete it could not account for (a kill, a
        # lost call): look first. Any --verify attempt, pass or fail, opens the gate.
        raise Refused(
            "an earlier purge did not end with its deletes accounted for: run "
            "--verify first (docs/deploy.md), then run --purge again."
        )
    copy_chunks(w, args, work, manifest)
    if not args.purge:
        print("copied and verified. Nothing was deleted; --purge deletes the copies.")
        return 0

    print(
        "locked window asserted by --locked-window-confirmed: the tool will check "
        "the freeze, the destination and that both databases read the same twice; "
        "it cannot prove that every writer is off."
    )
    purge_phase(w, args, work, manifest, sum(held.values()))
    print("done: the lead rows live only in the leads database now.")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Move legacy leads (issue #772).")
    parser.add_argument("--source", required=True, help="the analytics D1 database")
    parser.add_argument(
        "--source-id", required=True, help="its platform id (d1 info --json: uuid)"
    )
    parser.add_argument("--dest", required=True, help="the leads D1 database")
    parser.add_argument(
        "--dest-id", required=True, help="its platform id (d1 info --json: uuid)"
    )
    parser.add_argument("--workdir", required=True, help="private (mode 700) dir")
    # One mode per run: a second flag would be silently ignored, so it is refused.
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--copy", action="store_true", help="copy and verify only")
    modes.add_argument("--purge", action="store_true", help="copy, verify, delete")
    modes.add_argument("--freeze", action="store_true", help="refuse new rows (step 1)")
    modes.add_argument("--verify", action="store_true", help="the cutover check")
    modes.add_argument(
        "--restore-from-export",
        action="store_true",
        help="restore exported source rows and verify them",
    )
    parser.add_argument(
        "--restart-copy",
        action="store_true",
        help="--copy or --purge only: ignore saved cursors and copy from the start",
    )
    parser.add_argument(
        "--locked-window-confirmed",
        action="store_true",
        help="--purge only: every writer is off (kill switches, old Workers)",
    )
    args = parser.parse_args(argv)
    if args.locked_window_confirmed and not args.purge:
        parser.error("--locked-window-confirmed is only for --purge")
    if args.restart_copy and not (args.copy or args.purge):
        parser.error("--restart-copy is only for --copy or --purge")
    for value in (args.source, args.dest):
        if not NAME.match(value):
            print("error: bad database name", file=sys.stderr)
            return 2
    args.source_id, args.dest_id = args.source_id.lower(), args.dest_id.lower()
    for value in (args.source_id, args.dest_id):
        if not UUID.match(value):
            print("error: a platform id must be a uuid", file=sys.stderr)
            return 2
    if args.source == args.dest:
        print("refused: SOURCE and DEST are the same name.", file=sys.stderr)
        return 3
    if args.purge and not args.locked_window_confirmed:
        print(
            "refused: --purge needs --locked-window-confirmed (the Worker kill "
            "switches engaged and every writer off); nothing was copied or deleted.",
            file=sys.stderr,
        )
        return 3
    if args.purge:
        settle_seconds()  # a bad MOVE_SETTLE stops the run before any copy
    previous: dict[int, Any] = {}
    try:
        for signum in STOP_SIGNALS:
            previous[signum] = signal.signal(signum, note_signal)
    except ValueError:  # not the main thread: no handlers, the default applies
        previous.clear()
    STOP["signal"] = None
    try:
        return run(args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    except PartialPurge as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 4
    except VerifyRequired as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 5
    except Interrupted as exc:
        print(f"interrupted: {exc}; this run deleted nothing.", file=sys.stderr)
        return 6
    except LockHeld as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 7
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
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
