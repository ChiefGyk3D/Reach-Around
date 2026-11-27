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
5. The unit note (``unit_path``) is written before the commit point and holds
   the digest of the numbers text of this run. A snapshot alone cannot prove that
   the numbers landed, so the note stays until a numbers file has been written
   after it (or the numbers on disk match its digest). While it stays the status
   is false, even if every source fails on the retry.
6. Rows are repaired whatever the age of the snapshot (a crash on day N is
   repaired by a run on day N+2). A snapshot dated after today (the clock went
   backward) cannot get rows yet: the run prints ``snapshot_in_future`` and the
   status stays false until the day arrives.
7. Staged and temp files of this snapshot are removed at the start of a run
   (``find_stale``, ``remove_stale``), after the status says false; a committed
   snapshot is never touched.

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
import stat
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from reach_around.config import ConfigError
from reach_around.config.loader import _read
from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts import bluesky_feed, mastodon_feed
from reach_around.counts.files import write_atomic
from reach_around.counts.history import History, Record

SAME_DAY_KEPT = "same_day_kept"
# The fixed word a run prints when a committed snapshot is dated after today (the
# clock went backward): its rows cannot be appended yet, and the status says so.
SNAPSHOT_IN_FUTURE = "snapshot_in_future"

_SUMS = {
    "bluesky": bluesky_feed.snapshot_aggregates,
    "mastodon": mastodon_feed.snapshot_aggregates,
}

_UNIT_DIGEST = re.compile(r"[0-9a-f]{64}")


def unit_path(numbers: Path) -> Path:
    """The unit note: written before the commit point, it holds the digest of the
    numbers text that belongs to the snapshot being committed, and is removed
    once a numbers file has been written (a snapshot alone cannot prove that)."""
    return numbers.with_name(f".{numbers.name}.unit")


def write_unit(numbers: Path, digest: str) -> None:
    """Write the unit note beside ``numbers`` (atomic, mode 0600). Raises
    ``OSError`` on failure."""
    write_atomic(unit_path(numbers), digest)


def unit_pending(numbers: Path) -> bool:
    """True while the unit note is there and its numbers have not landed.

    A note whose digest equals the numbers file on disk means the numbers write
    landed and only the removal was lost: it is removed here and counts as
    settled. A note that cannot be read, or differs, is unsettled (a later run
    that writes the numbers file removes it)."""
    note = unit_path(numbers)
    if not os.path.lexists(note):
        return False
    try:
        text = _read(note, 128).decode("ascii", errors="strict").strip()
    except (ConfigError, ValueError, OSError):
        return True
    if _UNIT_DIGEST.fullmatch(text) is None:
        return True
    try:
        landed = hashlib.sha256(numbers.read_bytes()).hexdigest() == text
    except OSError:
        return True
    if landed:
        try:
            note.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return True
    return not landed


def find_stale(snapshot: Path) -> list[Path]:
    """The staged and temp copies a crash left beside ``snapshot``.

    Only plain files whose name is exactly one this module's writers make
    (``.<name>.<16 hex>.staged`` or ``.tmp``, or the temp copy of a staged file)
    are listed. A link, a folder or the committed snapshot itself never is.
    Raises ``OSError`` if the folder cannot be read.
    """
    pattern = re.compile(
        rf"\.{{1,2}}{re.escape(snapshot.name)}\.[0-9a-f]{{16}}\.(?:staged|tmp)"
        r"(?:\.[0-9a-f]{16}\.tmp)?"
    )
    found: list[Path] = []
    for entry in snapshot.parent.iterdir():
        if entry.name == snapshot.name or not pattern.fullmatch(entry.name):
            continue
        try:
            if stat.S_ISREG(entry.lstat().st_mode):
                found.append(entry)
        except FileNotFoundError:
            continue
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
