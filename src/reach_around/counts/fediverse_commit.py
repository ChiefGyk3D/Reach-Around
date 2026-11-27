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
   a run that finds a leftover staged file or a live pending note says
   ``committed: false`` at once, and never ``true`` while a note is live.
5. Staged and temp files of this snapshot are swept at the start of a run
   (``sweep_stale``); a committed snapshot is never touched.

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

import json
import re
import stat
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path

from reach_around.config import ConfigError
from reach_around.config.loader import _read
from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts import bluesky_feed, mastodon_feed
from reach_around.counts.history import History, Record

SAME_DAY_KEPT = "same_day_kept"
# A snapshot older than this many days is not turned into rows again: a run that
# finds one leaves a gap (never a number), as a failed read does.
RECOVERY_DAYS = 1

_SUMS = {
    "bluesky": bluesky_feed.snapshot_aggregates,
    "mastodon": mastodon_feed.snapshot_aggregates,
}


def sweep_stale(snapshot: Path) -> int:
    """Remove the staged and temp copies a crash left beside ``snapshot``.

    Only plain files whose name is exactly one this module's writers make
    (``.<name>.<16 hex>.staged`` or ``.tmp``, or the temp copy of a staged file)
    are removed. A link, a folder or the committed snapshot itself is never
    touched. Returns how many were removed. Raises ``OSError`` if the folder
    cannot be read.
    """
    pattern = re.compile(
        rf"\.{{1,2}}{re.escape(snapshot.name)}\.[0-9a-f]{{16}}\.(?:staged|tmp)"
        r"(?:\.[0-9a-f]{16}\.tmp)?"
    )
    removed = 0
    for entry in snapshot.parent.iterdir():
        if entry.name == snapshot.name or not pattern.fullmatch(entry.name):
            continue
        try:
            if not stat.S_ISREG(entry.lstat().st_mode):
                continue
            entry.unlink()
        except FileNotFoundError:
            continue
        removed += 1
    return removed


def snapshot_rows(
    snapshots: Mapping[str, Mapping[str, object]],
    run_id: str,
    today: date,
) -> list[Record]:
    """The history rows of the stored snapshots: one per platform and metric,
    dated by the snapshot's ``as_of`` day. Only snapshots from the last
    ``RECOVERY_DAYS`` days up to ``today`` are used."""
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
        if day > today or day < today - timedelta(days=RECOVERY_DAYS):
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
