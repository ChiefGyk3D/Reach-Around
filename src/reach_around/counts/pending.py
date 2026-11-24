# SPDX-License-Identifier: Apache-2.0
"""A small record of history writes whose numbers file may not have followed.

History is written before the status and numbers files. If a later write fails,
the history already holds the first value of that UTC day while the numbers file
still shows the old one. A retry the same day would then keep the first history
value (one value per day) and write a different number. To keep both sides in
step, the run ids of every attempt are listed in ``.<numbers file>.pending``
before history is touched and the file is removed once the numbers file is
written. The next run reads it and, for a day that an interrupted attempt
already recorded with another value, appends a correction instead of leaving the
old value standing.

Only run ids are stored (no values). Held under the same lock as the run.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from reach_around.counts.files import write_atomic
from reach_around.counts.history import History, HistoryError, Record

MAX_IDS = 16
MAX_BYTES = 2048
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CORRECTION_NOTE = "replaces the value of an interrupted run; the later fetch stands"


def pending_path(numbers: Path) -> Path:
    return numbers.with_name(f".{numbers.name}.pending")


class PendingError(HistoryError):
    """The pending note is there but cannot be trusted. Never quotes content.

    The note is left exactly as it is (nothing is deleted or rewritten), so no
    information is lost; a person inspects or removes it and runs again.
    """


_UNUSABLE = (
    "the pending note next to the numbers file cannot be read or is not valid; "
    "it was left as it is; inspect or remove it, then run again"
)
_BASE = re.compile(r"^base ([0-9a-f]{64}|none)$")


@dataclass(frozen=True)
class Note:
    ids: list[str]
    base: str | None  # digest of the numbers file when the latest run began


def read_note(path: Path) -> Note:
    """The note; empty only when there is no note. Anything else unusable raises.

    An absent note is the only state that means "nothing pending". A note that
    cannot be opened or read, is not a regular file, is too large, is not
    ASCII, or holds a line that is neither a run id nor the digest line raises
    ``PendingError`` so the run stops before history or the note is touched.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return Note([], None)
    except OSError:
        raise PendingError(_UNUSABLE) from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise PendingError(_UNUSABLE)
        raw = os.read(fd, MAX_BYTES + 1)
    except OSError:
        raise PendingError(_UNUSABLE) from None
    finally:
        os.close(fd)
    if not raw or len(raw) > MAX_BYTES:
        raise PendingError(_UNUSABLE)
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise PendingError(_UNUSABLE) from None
    ids: list[str] = []
    base: str | None = None
    for line in lines:
        found = _BASE.fullmatch(line)
        if found is not None and base is None:
            base = found.group(1)
        elif _ID.fullmatch(line):
            ids.append(line)
        else:
            raise PendingError(_UNUSABLE)
    return Note(ids[-MAX_IDS:], base)


def read_pending(path: Path) -> list[str]:
    """The run ids listed, oldest first; raises ``PendingError`` if unusable."""
    return read_note(path).ids


def numbers_digest(numbers: Path) -> str:
    """Digest of the numbers file as it is now, or ``none`` when absent."""
    try:
        data = numbers.read_bytes()
    except FileNotFoundError:
        return "none"
    return hashlib.sha256(data).hexdigest()


def live_ids(note: Note, numbers: Path) -> list[str]:
    """The ids whose numbers write may not have happened.

    The note records the numbers file's digest from when its latest run began.
    If the file has changed since, that run (and so every earlier one) committed
    its numbers write and only the note's removal was lost; nothing is pending.
    """
    if note.base is not None and note.base != numbers_digest(numbers):
        return []
    return note.ids


def mark_pending(
    path: Path, ids: Sequence[str], run_id: str, base: str | None = None
) -> None:
    """List ``run_id`` after the ids already pending; raises ``OSError``."""
    kept = [*ids, run_id][-MAX_IDS:]
    lines = kept if base is None else [*kept, f"base {base}"]
    write_atomic(path, "\n".join(lines) + "\n")


def clear_pending(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def reconcile(
    history: History, ids: Sequence[str], records: Sequence[Record]
) -> list[Record]:
    """Turn a record whose day an interrupted run recorded differently into a
    correction of that record. Everything else is returned as it is."""
    if not ids:
        return list(records)
    wanted = set(ids)
    held: dict[tuple[str, str, str, str], Record] = {}
    for item in history.records:
        if item.run_id in wanted:
            held[item.day_key] = item  # a later correction supersedes the record
    originals: dict[tuple[str, str, str, str], Record] = {
        r.day_key: r for r in history.records if r.type == "record"
    }
    out: list[Record] = []
    for new in records:
        old = held.get(new.day_key)
        if new.type != "record" or old is None or old.value == new.value:
            out.append(new)
            continue
        first = originals[new.day_key]
        out.append(
            Record(
                platform=first.platform,
                metric=first.metric,
                value=new.value,
                as_of_utc=first.as_of_utc,
                source=first.source,
                run_id=new.run_id,
                note=CORRECTION_NOTE,
                era=first.era,
                type="correction",
                api_provider=first.api_provider,
            )
        )
    return out
