# SPDX-License-Identifier: Apache-2.0
"""How a fediverse refresh commits, and how the next run repairs a crash (#940).

A run that read a snapshot touches several files: the snapshot, history rows,
the numbers file and the status file. They cannot change in one step, so the run
has ONE commit point and every other step is either idempotent or derived from
what the commit point put on disk.

Design (the assumption: one refresh at a time per output; ``counts refresh`` holds
the output's ``FileLock`` around the whole cycle, so a staged file found at the
start of a run belongs to a run that is no longer there):

1. The commit point is the rename of the staged snapshot into place. Before it
   nothing the kit reads has changed; after it the snapshot is the truth.
2. The fediverse history rows are DERIVED from the snapshot (``snapshot_rows``:
   one row per metric, dated by the snapshot's own ``as_of`` day). They are
   appended only after the rename, and every run starts by appending any that a
   crash left out (``skip_days``: a row that is there is never written twice).
   An aborted run therefore never leaves a row for a snapshot that never landed.
3. The same-day rule: history is append-only and holds ONE row per platform,
   metric and UTC day. If that day already has a row with another value than the
   snapshot this run would commit, the new snapshot is NOT committed (the stored
   one stays, so snapshot and history agree) and the platform is reported as
   skipped with the fixed word ``same_day_kept``. The next UTC day commits.
4. The status file is written ``committed: false`` before the commit point and
   ``committed: true`` last, and it may lag a crash. It is never trusted alone:
   every run starts by RECOMPUTING from the files whether the unit is complete
   (no staged copy, no live pending note, no unsettled unit note, every row of
   the committed snapshot in history, no snapshot dated after today). If not, a
   status of ``true`` is set to false BEFORE any repair step. ``true`` is
   written only by the end of a run, after the same check passes again.
5. The unit note (``unit_path``) is a TRANSACTION RECORD written before the
   commit point: the digest of the proposed snapshot, the digest of the expected
   numbers text and a random generation id. After this run's numbers write the
   same id is written back into the note. It settles only when the committed
   snapshot is the proposed one AND the note carries its own id (identical
   numbers bytes on two days prove nothing). While it stays the status is false,
   even if every source fails on the retry. A rename that fails removes the note
   at once; after a crash, recovery removes a note whose proposed snapshot is not
   the committed one (or whose staged copy is still there): that transaction
   never crossed the commit point.
6. Rows are repaired whatever the age of the snapshot (a crash on day N is
   repaired by a run on day N+2). A snapshot dated after today (the clock went
   backward) cannot get rows yet: the run prints ``snapshot_in_future`` and the
   status stays false until the day arrives.
7. Staged and temp files of this snapshot are removed at the start of a run
   (``find_stale``, ``remove_stale``), after the status says false; a committed
   snapshot is never touched.
8. Reads are bounded: the digests of the snapshot, the numbers file and every
   staged copy are streamed in fixed chunks and capped (``file_digest``); a file
   over its limit leaves the unit pending with the fixed word ``UnitFileTooLarge``,
   and at most ``MAX_STAGED_CANDIDATES`` staged copies are examined. Discovery is
   bounded too (``find_stale``): it stops at the ninth matching plain file or after
   ``MAX_DIR_ENTRIES`` directory entries, keeps nothing more, and raises
   ``UnitFileTooLarge`` (recovery then leaves the status false and the files in
   place as evidence).
9. A unit note that cannot be parsed is moved, after the status demotion, to
   ``.numbers.json.unit.bad`` (``quarantine_unit``; an older ``.bad`` is replaced);
   that run reports ``unit_note_quarantined`` and keeps the status false, and the
   next good run proceeds. The ``.bad`` file may be deleted by hand at any time.

Residual (explicit and bounded). The unit note proves crash consistency against
this program's own crashes. It is not a defense against a writer that has write
access to the output folder: such a writer can already replace the numbers file
and the snapshot, which is inside the trust boundary of the single-writer
``FileLock`` design. One case is named: a valid, settled note from an earlier
transaction, kept aside and put back after a later transaction produced
byte-identical snapshot and numbers files and crashed before its numbers write,
is accepted as that transaction's proof, because the generation id is not bound
into a committed artifact. Binding it would need a new field in the pinned
snapshot and numbers schemas; that is deliberately not done.

Crash table (what is on disk after the crash, and what the next run does):

    crash during staging
        disk: old files, maybe a stray temp or partial staged file
        next run: sweep removes it
    crash after staging
        disk: old files + the staged snapshot
        next run: sweep removes it (rolled back; nothing had changed)
    crash after the ordinary history rows
        disk: + a live pending note, status still the old one (it lags)
        next run: the note is live, so status is set false at once; the rows
        are reconciled by the existing pending-note logic
    crash after status false
        disk: + status false, staged snapshot
        next run: as above
    crash after the RENAME (the commit point)
        disk: new snapshot, no fediverse rows, status false
        next run: rows derived from the snapshot are appended (never twice)
    crash after the fediverse rows
        disk: + rows, old numbers file
        next run: the run rewrites the numbers file (existing note logic)
    crash after the numbers file or the pending note settled
        disk: everything landed, status false
        next run: nothing to repair; the run writes status true
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reach_around.config import ConfigError
from reach_around.config.loader import _read
from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts import bluesky_feed, mastodon_feed
from reach_around.counts.fediverse_store import MAX_SNAPSHOT_BYTES
from reach_around.counts.files import write_atomic
from reach_around.counts.history import History, Record

SAME_DAY_KEPT = "same_day_kept"
# The fixed word a run prints when a committed snapshot is dated after today (the
# clock went backward): its rows cannot be appended yet, and the status says so.
SNAPSHOT_IN_FUTURE = "snapshot_in_future"
# The fixed word of a run that moved an unparsable unit note aside.
UNIT_NOTE_QUARANTINED = "unit_note_quarantined"

_SUMS = {
    "bluesky": bluesky_feed.snapshot_aggregates,
    "mastodon": mastodon_feed.snapshot_aggregates,
}

_HEX64 = re.compile(r"[0-9a-f]{64}")
_HEX16 = re.compile(r"[0-9a-f]{16}")
_NOTE_KEYS = {"snapshot", "numbers", "generation", "landed"}


def new_generation() -> str:
    """A random id (16 hex) for one unit transaction."""
    return secrets.token_hex(8)


class UnitFileTooLarge(OSError):
    """A file the unit check must hash is over its limit or not a regular file.
    The unit stays pending (status false); nothing is read past the limit."""


# The most staged candidates ``abandon_unit`` examines; more leave the unit pending.
MAX_STAGED_CANDIDATES = 8
# The most directory entries ``find_stale`` looks at (names only) before it gives up.
MAX_DIR_ENTRIES = 10_000
_CHUNK = 64 * 1024


def file_digest(path: Path, limit: int) -> str | None:
    """sha256 of the file's bytes, ``None`` when it is absent or unreadable.

    The file is streamed in fixed chunks and never read past ``limit + 1`` bytes:
    the size is checked before the read, and each read asks for at most the bytes
    that remain up to ``limit + 1`` (so a file that grows during the hash is caught
    by the extra byte). A file over ``limit`` (or one that is not a regular file)
    raises ``UnitFileTooLarge``."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise UnitFileTooLarge("unit file over its limit")
        digest = hashlib.sha256()
        total = 0
        while chunk := os.read(fd, min(_CHUNK, limit + 1 - total)):
            total += len(chunk)
            if total > limit:
                raise UnitFileTooLarge("unit file over its limit")
            digest.update(chunk)
        return digest.hexdigest()
    except UnitFileTooLarge:
        raise
    except OSError:
        return None
    finally:
        os.close(fd)


@dataclass(frozen=True)
class UnitNote:
    """The transaction record of one unit: the digest of the snapshot proposed
    for the commit point, the digest of the numbers text expected after it, the
    random generation id of the transaction, and ``landed``: the same generation
    id once THIS transaction's numbers write landed (``None`` before)."""

    snapshot: str
    numbers: str
    generation: str
    landed: str | None = None

    def text(self) -> str:
        return json.dumps(
            {
                "snapshot": self.snapshot,
                "numbers": self.numbers,
                "generation": self.generation,
                "landed": self.landed,
            },
            sort_keys=True,
        )


def unit_path(numbers: Path) -> Path:
    """The unit note: written before the commit point, a transaction record
    (``UnitNote``). Identical numbers bytes on two days cannot be mistaken for
    the committed transaction: only this transaction's own generation id, written
    into the note after its numbers write, proves that write landed. (The
    numbers file keeps its pinned schema; the record lives in this sibling.)"""
    return numbers.with_name(f".{numbers.name}.unit")


def write_unit(numbers: Path, note: UnitNote) -> None:
    """Write the unit note beside ``numbers`` (atomic, mode 0600). Raises
    ``OSError`` on failure."""
    write_atomic(unit_path(numbers), note.text())


def mark_landed(numbers: Path, note: UnitNote) -> None:
    """Record, atomically in the note itself, that this transaction's numbers
    write landed (the generation id is written back). Raises ``OSError``."""
    write_atomic(
        unit_path(numbers),
        UnitNote(note.snapshot, note.numbers, note.generation, note.generation).text(),
    )


def discard_unit(numbers: Path) -> None:
    """Remove the unit note of a transaction that never crossed the commit point
    (never raises: recovery removes what is left)."""
    try:
        unit_path(numbers).unlink()
    except OSError:
        pass


def read_unit(numbers: Path) -> UnitNote | None:
    """The unit note, ``None`` when it cannot be read or is not a valid record."""
    try:
        raw = json.loads(_read(unit_path(numbers), 1024))
    except (ConfigError, ValueError, RecursionError, OSError):
        return None
    if not isinstance(raw, dict) or set(raw) != _NOTE_KEYS:
        return None
    snapshot, digest, gen, landed = (
        raw["snapshot"],
        raw["numbers"],
        raw["generation"],
        raw["landed"],
    )
    if not (
        type(snapshot) is str
        and _HEX64.fullmatch(snapshot)
        and type(digest) is str
        and _HEX64.fullmatch(digest)
        and type(gen) is str
        and _HEX16.fullmatch(gen)
        and (landed is None or landed == gen)
    ):
        return None
    return UnitNote(snapshot, digest, gen, landed)


def unit_pending(numbers: Path, snapshot: Path) -> bool:
    """True while the unit note is there and its transaction has not settled.

    It settles (and the note is removed) only when the COMMITTED snapshot is the
    one the note proposed AND the note carries its own generation id (its numbers
    write landed) AND the numbers file still has the expected digest. A note that
    cannot be read, or whose snapshot is not the committed one, is unsettled (the
    start of the next run removes the latter: ``abandon_unit``)."""
    note = unit_path(numbers)
    if not os.path.lexists(note):
        return False
    record = read_unit(numbers)
    if record is None:
        return True
    try:
        if file_digest(snapshot, MAX_SNAPSHOT_BYTES) != record.snapshot:
            return True
        if record.landed != record.generation:
            return True
        if file_digest(numbers, MAX_NUMBERS_BYTES) != record.numbers:
            return True
    except UnitFileTooLarge:
        return True  # an oversized file is never settled
    try:
        note.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return True
    return False


def abandon_unit(numbers: Path, snapshot: Path, staged: Sequence[Path]) -> bool:
    """Remove the note of a transaction that never crossed the commit point.

    Called first in recovery (``staged`` is what ``find_stale`` listed, about to
    be swept). The transaction did not commit when the committed snapshot is not
    the proposed one, or when a staged copy of the proposed one is still there.
    Returns True when a note was removed. Raises ``OSError`` if it cannot be, and
    ``UnitFileTooLarge`` (the note stays, the unit stays pending) when a file it
    must hash is over its limit or more than ``MAX_STAGED_CANDIDATES`` staged
    copies are there."""
    record = read_unit(numbers)
    if record is None:
        return False
    if len(staged) > MAX_STAGED_CANDIDATES:
        raise UnitFileTooLarge("too many staged candidates")
    never = file_digest(snapshot, MAX_SNAPSHOT_BYTES) != record.snapshot or any(
        file_digest(path, MAX_SNAPSHOT_BYTES) == record.snapshot for path in staged
    )
    if not never:
        return False
    try:
        unit_path(numbers).unlink()
    except FileNotFoundError:
        return False
    return True


def bad_unit_path(numbers: Path) -> Path:
    """Where ``quarantine_unit`` moves a note that cannot be parsed."""
    return numbers.with_name(f".{numbers.name}.unit.bad")


def quarantine_unit(numbers: Path) -> bool:
    """Move a unit note that ``read_unit`` cannot parse to ``bad_unit_path``.

    Only a plain file is moved (a link or a folder is left alone and keeps
    blocking). The rename replaces an older ``.bad`` file and never follows a
    link. Returns True when a note was quarantined. Raises ``OSError`` if the
    rename fails. The caller keeps the status false for that run."""
    note = unit_path(numbers)
    try:
        if not stat.S_ISREG(note.lstat().st_mode):
            return False
    except OSError:
        return False
    if read_unit(numbers) is not None:
        return False
    try:
        os.replace(note, bad_unit_path(numbers))
    except FileNotFoundError:
        return False
    return True


def find_stale(snapshot: Path) -> list[Path]:
    """The staged and temp copies a crash left beside ``snapshot``.

    Only plain files whose name is exactly one this module's writers make
    (``.<name>.<16 hex>.staged`` or ``.tmp``, or the temp copy of a staged file)
    are listed. A link, a folder or the committed snapshot itself never is.
    Raises ``OSError`` if the folder cannot be read, and ``UnitFileTooLarge``
    (nothing is returned) at the ``MAX_STAGED_CANDIDATES + 1``-th matching file or
    after ``MAX_DIR_ENTRIES`` entries: the walk stops there.
    """
    pattern = re.compile(
        rf"\.{{1,2}}{re.escape(snapshot.name)}\.[0-9a-f]{{16}}\.(?:staged|tmp)"
        r"(?:\.[0-9a-f]{16}\.tmp)?"
    )
    found: list[Path] = []
    seen = 0
    with os.scandir(snapshot.parent) as entries:
        for entry in entries:
            seen += 1
            if seen > MAX_DIR_ENTRIES:
                raise UnitFileTooLarge("too many directory entries")
            if entry.name == snapshot.name or not pattern.fullmatch(entry.name):
                continue
            try:
                regular = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if regular:
                if len(found) >= MAX_STAGED_CANDIDATES:
                    raise UnitFileTooLarge("too many staged candidates")
                found.append(snapshot.parent / entry.name)
    return found


def remove_stale(found: Sequence[Path]) -> int:
    """Remove the files ``find_stale`` listed; returns how many were removed."""
    removed = 0
    for entry in found:
        try:
            entry.unlink()
        except FileNotFoundError:
            continue
        removed += 1
    return removed


def sweep_stale(snapshot: Path) -> int:
    """``find_stale`` then ``remove_stale``; returns how many were removed."""
    return remove_stale(find_stale(snapshot))


def future_snapshots(
    snapshots: Mapping[str, Mapping[str, object]], today: date
) -> list[str]:
    """The platforms whose stored snapshot is dated after ``today``: their rows
    cannot be appended (history rejects a future time), so a run reports
    ``SNAPSHOT_IN_FUTURE`` and never claims the unit is complete."""
    late: list[str] = []
    for kind in sorted(snapshots):
        as_of = snapshots[kind].get("as_of")
        try:
            day = date.fromisoformat(as_of) if type(as_of) is str else None
        except ValueError:
            day = None
        if day is not None and day > today:
            late.append(kind)
    return late


def rows_present(history: History | None, rows: Sequence[Record]) -> bool:
    """True when history holds a row for the day key of every row in ``rows``
    (the value is not compared: a later correction of a day stands)."""
    if not rows:
        return True
    if history is None:
        return False
    held = {item.day_key for item in history.records}
    return all(row.day_key in held for row in rows)


def snapshot_rows(
    snapshots: Mapping[str, Mapping[str, object]],
    run_id: str,
    today: date,
) -> list[Record]:
    """The history rows of the stored snapshots: one per platform and metric,
    dated by the snapshot's own ``as_of`` day. A snapshot of ANY earlier day is
    used (a crash on day N is repaired by a run on day N+2); one dated after
    ``today`` is not (see ``future_snapshots``)."""
    rows: list[Record] = []
    for kind in sorted(snapshots):
        snapshot = snapshots[kind]
        sums = _SUMS[kind](snapshot) if kind in _SUMS else None
        as_of = snapshot.get("as_of")
        if sums is None or type(as_of) is not str:
            continue
        try:
            day = date.fromisoformat(as_of)
        except ValueError:
            continue
        if day > today:
            continue
        rows.extend(
            Record(
                platform=kind,
                metric=metric,
                value=value,
                as_of_utc=f"{as_of}T00:00:00Z",
                source="api",
                run_id=run_id,
            )
            for metric, value in sorted(sums.items())
        )
    return rows


def day_conflicts(history: History, rows: Sequence[Record]) -> set[str]:
    """The platforms whose day already has a row with another value than ``rows``.

    A later correction of a day supersedes the record it corrects, so the value
    that stands for a day is the last one listed."""
    standing = {item.day_key: item.value for item in history.records}
    return {
        row.platform
        for row in rows
        if row.day_key in standing and standing[row.day_key] != row.value
    }


def status_committed(path: Path) -> bool | None:
    """The ``committed`` flag of the status file, ``None`` when absent or odd."""
    try:
        raw = json.loads(_read(path, MAX_NUMBERS_BYTES))
    except (ConfigError, ValueError, RecursionError):
        return None
    flag = raw.get("committed") if isinstance(raw, dict) else None
    return flag if isinstance(flag, bool) else None
