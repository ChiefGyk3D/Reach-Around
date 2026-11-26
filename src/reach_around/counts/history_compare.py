# SPDX-License-Identifier: Apache-2.0
"""Compare two versions of the history file: append-only, or a reviewed
tombstone-only rewrite (issue #619, design in
docs/superpowers/specs/2026-11-23-labeled-tombstone-rewrite-design.md).

``compare_history`` is pure: it takes the bytes of the old and the new file and
says which of two things the change is, or refuses with ``HistoryError``.

* ``append``: the new file begins with every byte of the old one, and every
  added line is a canonical record or correction.
* ``rewrite``: only when the caller allows it. Every changed line is an ``api``
  record that became a tombstone (its corrections go with it), every other line
  keeps its parsed content, order and place, every later line is re-chained and
  canonical, no tombstone is removed, and every field of every new tombstone
  and of the committed manifest is derived from public inputs
  (``rewrite_metadata``), so the redacted value has no field to hide in.

Anything else is refused. Messages are fixed words with counts, never a value,
a note or a digest.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from reach_around.counts.history import (
    GENESIS,
    HistoryError,
    Record,
    Tombstone,
    _digest,
    _line_bytes,
    _no_constant,
    _no_float,
    _pairs,
    _parse,
    _plain_date,
    _tomb_line,
    rewrite_metadata,
)

HISTORY_REWRITE_MAX = 500
REWRITE_MAX_PERCENT = 10
COMPARE_MAX_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_NUMBERS_BYTES = 64 * 1024
# Values with fewer digits are skipped by the digit-token belt: they would match
# dates and counts by chance. The derived metadata is the control, not the scan.
MIN_SCANNED_DIGITS = 6

_Key = tuple[str, str, str, str]


@dataclass(frozen=True)
class CompareResult:
    mode: str  # "append" or "rewrite"
    replaced: int = 0
    dropped: int = 0
    manifest_name: str | None = None


def read_regular(path: str, cap: int) -> bytes:
    """The bytes of a regular file of at most ``cap`` bytes; never a link."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise HistoryError("cannot open an input file (missing or a link?)") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise HistoryError("an input is not a regular file")
        if info.st_size > cap:
            raise HistoryError("an input file is too large")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise HistoryError("an input file is too large")
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        raise HistoryError("cannot read an input file") from None
    finally:
        os.close(fd)


def _split(data: bytes) -> list[bytes]:
    return data[:-1].split(b"\n") if data else []


def _canonical(entry: Record | Tombstone, prev: str) -> bytes:
    if isinstance(entry, Tombstone):
        return _tomb_line(entry, prev)
    return _line_bytes(entry, prev)


def _compare_append(
    old_count: int,
    new_entries: tuple[Record | Tombstone, ...],
    new_lines: list[bytes],
) -> CompareResult:
    prev = _digest(new_lines[old_count - 1]) if old_count else GENESIS
    for entry, raw in zip(new_entries[old_count:], new_lines[old_count:], strict=True):
        if isinstance(entry, Tombstone):
            raise HistoryError("an appended line is a tombstone")
        if raw != _canonical(entry, prev):
            raise HistoryError("an appended line is not in the canonical form")
        prev = _digest(raw)
    return CompareResult("append")


def _numbers(data: bytes | None) -> dict[str, Any]:
    if data is None:
        return {"version": 1, "platforms": {}}
    if len(data) > MAX_NUMBERS_BYTES:
        raise HistoryError("numbers.json is too large")
    try:
        obj = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise HistoryError("numbers.json is not strict JSON") from None
    if (
        not isinstance(obj, dict)
        or "platforms" not in obj
        or not isinstance(obj["platforms"], dict)
    ):
        raise HistoryError("numbers.json has an unexpected shape")
    return obj


def _check_numbers(
    old: bytes | None, new: bytes | None, gone: dict[str, set[tuple[int, str]]]
) -> None:
    """NEW equals OLD with exactly the entries matching a replaced record removed."""
    before = _numbers(old)
    after = _numbers(new)
    expected: dict[str, Any] = {}
    for ident, entry in before["platforms"].items():
        if _matches(ident, entry, gone):
            continue
        expected[ident] = entry
    rest_before = {k: v for k, v in before.items() if k != "platforms"}
    rest_after = {k: v for k, v in after.items() if k != "platforms"}
    if rest_before != rest_after or after["platforms"] != expected:
        raise HistoryError(
            "numbers.json must equal the old one with exactly the entries that "
            "match a replaced record removed"
        )


def _matches(ident: str, entry: Any, gone: dict[str, set[tuple[int, str]]]) -> bool:
    if not isinstance(entry, dict):
        return False
    count = entry.get("count")
    as_of = entry.get("as_of")
    if type(count) is not int or type(as_of) is not str:
        return False
    return (count, as_of[:10]) in gone.get(ident, set())


def compare_history(
    old: bytes,
    new: bytes,
    *,
    allow_tombstones: bool = False,
    manifest: bytes | None = None,
    manifest_name: str | None = None,
    pr_day: str | None = None,
    numbers_old: bytes | None = None,
    numbers_new: bytes | None = None,
    max_replaced: int = HISTORY_REWRITE_MAX,
) -> CompareResult:
    old_entries, _ = _parse(old)
    new_entries, _ = _parse(new)
    old_lines = _split(old)
    new_lines = _split(new)
    if new.startswith(old):
        return _compare_append(len(old_entries), new_entries, new_lines)
    if not allow_tombstones:
        raise HistoryError(
            "the history is not append-only and no tombstone rewrite is allowed"
        )
    old_tombs = {e.key for e in old_entries if isinstance(e, Tombstone)}
    new_tombs = {e.key for e in new_entries if isinstance(e, Tombstone)}
    if not old_tombs <= new_tombs:
        raise HistoryError("a tombstone was removed or turned back into data")
    replaced = new_tombs - old_tombs
    if not replaced:
        raise HistoryError("no record was replaced by a tombstone")
    old_live: dict[_Key, Record] = {
        e.key: e for e in old_entries if isinstance(e, Record) and e.type == "record"
    }
    for key in replaced:
        record = old_live.get(key)
        if record is None:
            raise HistoryError("a tombstone does not replace an old record")
        if record.source != "api":
            raise HistoryError("only api records may be replaced by this route")
    if len(replaced) > max_replaced:
        raise HistoryError(f"more than {max_replaced} records in one rewrite")
    if len(replaced) * 100 > REWRITE_MAX_PERCENT * len(old_live):
        raise HistoryError(f"more than {REWRITE_MAX_PERCENT} percent of the records")

    # Walk both files by key and in order, never by line number.
    j = 0
    first: int | None = None
    dropped = 0
    for entry in old_entries:
        if isinstance(entry, Tombstone):
            if j >= len(new_entries) or new_entries[j] != entry:
                raise HistoryError("an old tombstone changed")
            j += 1
        elif entry.key in replaced:
            if entry.type != "record":
                dropped += 1
                continue
            if first is None:
                first = j
            got = new_entries[j] if j < len(new_entries) else None
            if (
                not isinstance(got, Tombstone)
                or got.key != entry.key
                or got.api_provider != entry.api_provider
            ):
                raise HistoryError("a replaced record is not a matching tombstone")
            j += 1
        else:
            if j >= len(new_entries) or new_entries[j] != entry:
                raise HistoryError("a kept line changed, moved or was removed")
            j += 1
    if j != len(new_entries) or first is None:
        raise HistoryError("the new file has lines the old one does not account for")

    tombs = [e for e in new_entries if isinstance(e, Tombstone) and e.key in replaced]
    shapes = {(t.reason, t.trigger_date, t.redacted_at_utc, t.run_id) for t in tombs}
    if len(shapes) != 1:
        raise HistoryError("the new tombstones do not share one set of metadata")
    reason, trigger, stamp, run_id = next(iter(shapes))
    if pr_day is None:
        raise HistoryError("the pull request day is required for a rewrite")
    pr = _plain_date("pr_day", pr_day)
    day = stamp[:10]
    if _plain_date("redacted_day", day) not in (pr, pr - timedelta(days=1)):
        raise HistoryError(
            "redacted_at_utc is not the pull request day or the day before"
        )
    want_id, want_stamp, want_manifest = rewrite_metadata(
        sorted(replaced), reason, trigger, day
    )
    if stamp != want_stamp or run_id != want_id:
        raise HistoryError("tombstone metadata is not in the derived form")
    if manifest is None or manifest_name is None:
        raise HistoryError("the manifest is missing")
    if len(manifest) > MAX_MANIFEST_BYTES or manifest != want_manifest.encode("ascii"):
        raise HistoryError("the manifest does not match the replaced records")
    if manifest_name != f"{want_id}.manifest":
        raise HistoryError("the manifest file name is not the derived run id")

    # Canonical bytes: identical before the first change, rebuilt after it.
    if new_lines[:first] != old_lines[:first]:
        raise HistoryError("a line before the first replaced record changed")
    for i in range(first, len(new_entries)):
        prev = _digest(new_lines[i - 1]) if i else GENESIS
        if new_lines[i] != _canonical(new_entries[i], prev):
            raise HistoryError("a rewritten line is not in the canonical form")

    # Belt: the replaced value must not appear as a token in any added text.
    added = [
        raw
        for entry, raw in zip(new_entries, new_lines, strict=True)
        if isinstance(entry, Tombstone) and entry.key in replaced
    ]
    added.append(manifest)
    for key in replaced:
        digits = str(old_live[key].value)
        if len(digits) < MIN_SCANNED_DIGITS:
            continue
        token = re.compile(rf"(?<![0-9A-Za-z]){digits}(?![0-9A-Za-z])")
        if any(token.search(part.decode("ascii")) for part in added):
            raise HistoryError("a replaced value appears in the added text")

    gone: dict[str, set[tuple[int, str]]] = {}
    for key in replaced:
        record = old_live[key]
        gone.setdefault(record.platform, set()).add(
            (record.value, record.as_of_utc[:10])
        )
    _check_numbers(numbers_old, numbers_new, gone)
    return CompareResult("rewrite", len(replaced), dropped, manifest_name)
