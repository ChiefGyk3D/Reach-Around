# SPDX-License-Identifier: Apache-2.0
"""The release ledger for the source and country by destination tables.

An append-only JSON-lines file, hash-chained like the social-stats history
(``reach_around.counts.history``): every line carries ``prev``, the SHA-256 of
the previous line's bytes, appends are fsynced under an exclusive lock, and a
file that fails any check (a changed, removed or reordered line, a cut-short
last line, a symlink, a record that breaks the rule below) is refused, never
repaired. One record per released period: site, period kind (week or month),
first and last UTC day, and when it was released. No secrets, no counts.

The rule it keeps: for one site, a week and a month that share any UTC day are
never both released, because a reader holding both can difference them. A
repeat release of the exact same period is allowed (and not written again).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from reach_around.counts.history import (
    GENESIS,
    HistoryError,
    _digest,
    _lock,
    _no_constant,
    _no_float,
    _open_parent,
    _pairs,
    _read_fd,
    _write_all,
)

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 512
MAX_RECORDS = 20_000
LOCK_WAIT_SECONDS = 30.0
KINDS = ("week", "month")

_SITE = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {"site", "kind", "start", "end", "released_at", "prev"}


class LedgerError(HistoryError):
    """The ledger is unusable or the release would overlap. Never quotes content."""


@dataclass(frozen=True)
class Release:
    site: str
    kind: str
    start: str
    end: str
    released_at: str

    def overlaps(self, other: Release) -> bool:
        return self.site == other.site and not (
            self.end < other.start or other.end < self.start
        )


def _day(text: object, where: str) -> date:
    if not isinstance(text, str) or not _DAY.match(text):
        raise LedgerError(f"{where}: not a UTC day")
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise LedgerError(f"{where}: not a real day") from None


def validate(release: Release) -> None:
    if not _SITE.match(release.site):
        raise LedgerError("the site is not a short lowercase slug")
    if release.kind not in KINDS:
        raise LedgerError("the period kind is not week or month")
    start, end = _day(release.start, "start"), _day(release.end, "end")
    if release.kind == "week":
        if start.weekday() != 0 or end != start + timedelta(days=6):
            raise LedgerError("a week must be Monday to Sunday")
    else:
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        if start.day != 1 or end != nxt - timedelta(days=1):
            raise LedgerError("a month must be the first to the last day")
    if not _STAMP.match(release.released_at):
        raise LedgerError("released_at is not a UTC time")
    try:
        datetime.strptime(release.released_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise LedgerError("released_at is not a real time") from None


def _line(release: Release, prev: str) -> bytes:
    obj = {
        "site": release.site,
        "kind": release.kind,
        "start": release.start,
        "end": release.end,
        "released_at": release.released_at,
        "prev": prev,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")


def _parse_line(raw: bytes, number: int) -> tuple[Release, str]:
    where = f"line {number}"
    if not raw or len(raw) > MAX_LINE_BYTES:
        raise LedgerError(f"{where}: blank or too long")
    try:
        obj = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LedgerError(f"{where}: not strict JSON") from None
    if not isinstance(obj, dict) or set(obj) != _FIELDS:
        raise LedgerError(f"{where}: wrong set of fields")
    if not all(isinstance(v, str) for v in obj.values()):
        raise LedgerError(f"{where}: every field is text")
    if not _HASH.match(obj["prev"]):
        raise LedgerError(f"{where}: prev is not a SHA-256 hex digest")
    release = Release(
        obj["site"], obj["kind"], obj["start"], obj["end"], obj["released_at"]
    )
    try:
        validate(release)
    except LedgerError as exc:
        raise LedgerError(f"{where}: {exc}") from None
    return release, obj["prev"]


def check_new(existing: Sequence[Release], new: Release) -> bool:
    """True if ``new`` is already recorded; raises if it overlaps the other kind."""
    for old in existing:
        if old.site != new.site:
            continue
        if old.kind == new.kind and (old.start, old.end) == (new.start, new.end):
            return True
        if old.kind != new.kind and old.overlaps(new):
            raise LedgerError(
                f"the {new.kind} {new.start} to {new.end} shares a day with a "
                f"{old.kind} already released ({old.start} to {old.end}); a "
                "week and a month over the same day would let a reader "
                "difference them"
            )
        if old.kind == new.kind and old.overlaps(new):
            raise LedgerError("periods of one kind must not overlap")
    return False


def _parse(data: bytes) -> tuple[list[Release], str]:
    if not data:
        return [], GENESIS
    if not data.endswith(b"\n"):
        raise LedgerError("the last line is truncated; not repaired")
    out: list[Release] = []
    expect = GENESIS
    lines = data[:-1].split(b"\n")
    if len(lines) > MAX_RECORDS:
        raise LedgerError("too many records")
    for number, raw in enumerate(lines, start=1):
        release, prev = _parse_line(raw, number)
        if prev != expect:
            raise LedgerError(f"line {number}: the hash chain is broken")
        try:
            check_new(out, release)  # a file that contradicts the rule is corrupt
        except LedgerError:
            raise LedgerError(f"line {number}: breaks the release rule") from None
        out.append(release)
        expect = _digest(raw)
    return out, expect


def read_ledger(path: Path) -> list[Release]:
    """Validate the whole file. A missing file is an empty ledger."""
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise LedgerError("cannot open the ledger folder") from None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        except FileNotFoundError:
            return []
        except OSError:
            raise LedgerError("cannot open the ledger (a symlink?)") from None
        try:
            _lock(fd, fcntl.LOCK_SH, LOCK_WAIT_SECONDS)
            return _parse(_read_fd(fd, MAX_FILE_BYTES))[0]
        except OSError:
            raise LedgerError("cannot read the ledger") from None
        finally:
            os.close(fd)
    except HistoryError as exc:
        raise LedgerError(str(exc)) from None
    finally:
        os.close(dir_fd)


def record_release(path: Path, release: Release) -> bool:
    """Check ``release`` against the ledger and append it; False if already there.

    Raises ``LedgerError`` (and writes nothing) when the file is damaged or the
    release overlaps a period of the other kind.
    """
    validate(release)
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise LedgerError("cannot open the ledger folder") from None
    try:
        try:
            fd = os.open(
                path.name,
                os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
        except OSError:
            raise LedgerError("cannot open the ledger (a symlink?)") from None
        try:
            _lock(fd, fcntl.LOCK_EX, LOCK_WAIT_SECONDS)
            os.fchmod(fd, 0o600)
            existing = _read_fd(fd, MAX_FILE_BYTES)
            before, prev = _parse(existing)
            if check_new(before, release):
                return False
            line = _line(release, prev) + b"\n"
            if len(existing) + len(line) > MAX_FILE_BYTES:
                raise LedgerError("the ledger would be too large")
            _write_all(fd, line)
            os.fsync(fd)
            if not existing:
                os.fsync(dir_fd)
            return True
        except OSError:
            raise LedgerError("cannot write the ledger") from None
        finally:
            os.close(fd)
    except HistoryError as exc:
        raise LedgerError(str(exc)) from None
    finally:
        os.close(dir_fd)


def now_stamp(now: datetime | None = None) -> str:
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def as_dict(release: Release) -> dict[str, Any]:
    return {
        "site": release.site,
        "kind": release.kind,
        "start": release.start,
        "end": release.end,
    }
