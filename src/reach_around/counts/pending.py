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

import os
import re
from collections.abc import Sequence
from pathlib import Path

from reach_around.counts.files import write_atomic
from reach_around.counts.history import History, Record

MAX_IDS = 16
MAX_BYTES = 2048
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CORRECTION_NOTE = "replaces the value of an interrupted run; the later fetch stands"


def pending_path(numbers: Path) -> Path:
    return numbers.with_name(f".{numbers.name}.pending")


def read_pending(path: Path) -> list[str]:
    """The run ids listed, oldest first; empty when there is no usable file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        raw = os.read(fd, MAX_BYTES + 1)
    except OSError:
        return []
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        return []
    ids = [
        line
        for line in raw.decode("ascii", "ignore").splitlines()
        if _ID.fullmatch(line)
    ]
    return ids[-MAX_IDS:]


def mark_pending(path: Path, ids: Sequence[str], run_id: str) -> None:
    """List ``run_id`` after the ids already pending; raises ``OSError``."""
    kept = [*ids, run_id][-MAX_IDS:]
    write_atomic(path, "\n".join(kept) + "\n")


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
