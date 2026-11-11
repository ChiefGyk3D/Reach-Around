# SPDX-License-Identifier: Apache-2.0
"""The social-stats history store: one append-only JSON-lines file.

One JSON object per line. Every line carries ``prev``, the SHA-256 of the
previous line's bytes (without the newline; 64 zeros for the first line), so a
changed, removed or reordered earlier line breaks the chain and is found by
``read_history``. Appending never rewrites or truncates a line that was already
there: the file is opened in append mode under an exclusive lock, the whole
file is checked first, and the new bytes are fsynced. A file that fails the
check (including a last line cut short by a crash) is refused, never repaired
silently. Gaps stay gaps: a missing day is reported as missing, never as zero.
Everything is UTC. No network, no secrets, no personal data.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 2048
MAX_BATCH = 200
MAX_VALUE = 10**12
MAX_TEXT = 200
LOCK_WAIT_SECONDS = 30.0
_LOCK_POLL = 0.02

SOURCES = ("api", "hand_kept", "imported")
TYPES = ("record", "correction")

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_METRIC = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = ("platform", "metric", "value", "as_of_utc", "source", "run_id", "prev")
_OPTIONAL = ("note", "era", "type")


class HistoryError(Exception):
    """The history file or a record is not acceptable. Never quotes content."""


@dataclass(frozen=True)
class Record:
    platform: str
    metric: str
    value: int
    as_of_utc: str
    source: str
    run_id: str
    note: str | None = None
    era: str | None = None
    type: str = "record"

    @property
    def day_key(self) -> tuple[str, str, str, str]:
        return (self.platform, self.metric, self.as_of_utc[:10], self.source)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.platform, self.metric, self.as_of_utc, self.source)


@dataclass(frozen=True)
class Point:
    as_of_utc: str
    value: int
    source: str
    era: str | None
    note: str | None
    corrected: bool


def parse_as_of(text: str) -> datetime:
    if not isinstance(text, str) or not _AS_OF.match(text):
        raise HistoryError("as_of_utc must look like 2026-11-08T09:00:00Z")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        raise HistoryError("as_of_utc is not a real UTC time") from None


def _text(name: str, value: object) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_TEXT:
        raise HistoryError(f"{name} must be 1 to {MAX_TEXT} characters")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise HistoryError(f"{name} must not hold control characters")


def validate(record: Record, now: datetime | None = None) -> None:
    """Raise ``HistoryError`` unless ``record`` is a well-formed record.

    With ``now`` the as-of time must not be after it.
    """
    if not isinstance(record.platform, str) or not _SLUG.match(record.platform):
        raise HistoryError("platform must be a lowercase slug")
    if not isinstance(record.metric, str) or not _METRIC.match(record.metric):
        raise HistoryError("metric must be lowercase letters, digits, underscores")
    value = record.value
    if isinstance(value, bool) or not isinstance(value, int):
        raise HistoryError("value must be a whole number")
    if not 0 <= value <= MAX_VALUE:
        raise HistoryError(f"value must be between 0 and {MAX_VALUE}")
    when = parse_as_of(record.as_of_utc)
    if now is not None and when > now.astimezone(UTC):
        raise HistoryError("as_of_utc is in the future")
    if record.source not in SOURCES:
        raise HistoryError("source must be api or hand_kept")
    if not isinstance(record.run_id, str) or not _RUN_ID.match(record.run_id):
        raise HistoryError("run_id must be 1 to 64 letters, digits, . _ -")
    if record.type not in TYPES:
        raise HistoryError("type must be record or correction")
    for name in ("note", "era"):
        item = getattr(record, name)
        if item is not None:
            _text(name, item)
    if record.type == "correction" and record.note is None:
        raise HistoryError("a correction needs a note saying why")


def new_run_id(now: datetime) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"refresh-{stamp}-{secrets.token_hex(3)}"


def _line_bytes(record: Record, prev: str) -> bytes:
    obj: dict[str, Any] = {
        "platform": record.platform,
        "metric": record.metric,
        "value": record.value,
        "as_of_utc": record.as_of_utc,
        "source": record.source,
        "run_id": record.run_id,
        "prev": prev,
    }
    if record.note is not None:
        obj["note"] = record.note
    if record.era is not None:
        obj["era"] = record.era
    if record.type != "record":
        obj["type"] = record.type
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.encode("ascii")


def _digest(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in items:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = item
    return out


def _no_float(_: str) -> Any:
    raise ValueError("numbers must be whole")


def _no_constant(_: str) -> Any:
    raise ValueError("not a number")


def _parse_line(raw: bytes, number: int) -> tuple[Record, str]:
    where = f"line {number}"
    if not raw:
        raise HistoryError(f"{where}: blank line")
    if len(raw) > MAX_LINE_BYTES:
        raise HistoryError(f"{where}: line too long")
    try:
        text = raw.decode("utf-8")
        obj = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise HistoryError(f"{where}: not strict JSON") from None
    if not isinstance(obj, dict):
        raise HistoryError(f"{where}: not a JSON object")
    keys = set(obj)
    if not set(_REQUIRED) <= keys or keys - set(_REQUIRED) - set(_OPTIONAL):
        raise HistoryError(f"{where}: wrong set of fields")
    prev = obj["prev"]
    if not isinstance(prev, str) or not _HASH.match(prev):
        raise HistoryError(f"{where}: prev is not a SHA-256 hex digest")
    record = Record(
        platform=obj["platform"],
        metric=obj["metric"],
        value=obj["value"],
        as_of_utc=obj["as_of_utc"],
        source=obj["source"],
        run_id=obj["run_id"],
        note=obj.get("note"),
        era=obj.get("era"),
        type=obj.get("type", "record"),
    )
    try:
        validate(record)
    except HistoryError as exc:
        raise HistoryError(f"{where}: {exc}") from None
    return record, prev


def _parse(data: bytes) -> tuple[tuple[Record, ...], str]:
    """All records in ``data`` and the digest of the last line."""
    if not data:
        return (), GENESIS
    if not data.endswith(b"\n"):
        raise HistoryError("the last line is truncated (no newline); not repaired")
    records: list[Record] = []
    seen: set[tuple[str, str, str, str]] = set()
    days: set[tuple[str, str, str, str]] = set()
    expect = GENESIS
    for number, raw in enumerate(data[:-1].split(b"\n"), start=1):
        record, prev = _parse_line(raw, number)
        if prev != expect:
            raise HistoryError(f"line {number}: the hash chain is broken")
        if record.type == "record":
            if record.key in seen:
                raise HistoryError(f"line {number}: duplicate record")
            if record.day_key in days:
                raise HistoryError(f"line {number}: a second record for that UTC day")
            seen.add(record.key)
            days.add(record.day_key)
        elif record.key not in seen:
            raise HistoryError(f"line {number}: a correction with no earlier record")
        records.append(record)
        expect = _digest(raw)
    return tuple(records), expect


class Series:
    """The points of one platform and metric, corrections applied."""

    def __init__(self, records: Sequence[Record]) -> None:
        originals: dict[tuple[str, str, str, str], Record] = {}
        fixes: dict[tuple[str, str, str, str], Record] = {}
        for record in records:
            if record.type == "record":
                originals[record.key] = record
            else:
                fixes[record.key] = record
        points = []
        for key, original in originals.items():
            fix = fixes.get(key)
            use = fix if fix is not None else original
            points.append(
                Point(
                    original.as_of_utc,
                    use.value,
                    original.source,
                    use.era if use.era is not None else original.era,
                    use.note if fix is not None else original.note,
                    fix is not None,
                )
            )
        self._points = sorted(points, key=lambda p: (p.as_of_utc, p.source))

    def points(self) -> list[Point]:
        return list(self._points)

    def by_day(self) -> dict[date, Point]:
        """The latest point of each UTC day that has one."""
        days: dict[date, Point] = {}
        for point in self._points:
            days[parse_as_of(point.as_of_utc).date()] = point
        return days

    def gaps(self, first: date, last: date) -> list[date]:
        """Days from ``first`` to ``last`` with no point. Never filled with zero."""
        have = self.by_day()
        out = []
        day = first
        while day <= last:
            if day not in have:
                out.append(day)
            day += timedelta(days=1)
        return out


@dataclass(frozen=True)
class History:
    records: tuple[Record, ...]

    def series(self, platform: str, metric: str) -> Series:
        return Series(
            [r for r in self.records if r.platform == platform and r.metric == metric]
        )


def _lock(
    fd: int,
    mode: int,
    wait: float,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    deadline = clock() + wait
    while True:
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if clock() >= deadline:
                raise HistoryError(
                    "the history file is locked by another run"
                ) from None
            time.sleep(_LOCK_POLL)


def _read_fd(fd: int, max_bytes: int) -> bytes:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise HistoryError("the history path is not a regular file")
    if info.st_size > max_bytes:
        raise HistoryError("the history file is too large")
    chunks: list[bytes] = []
    offset = 0
    while offset < info.st_size:
        chunk = os.pread(fd, min(1 << 20, info.st_size - offset), offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _open_parent(path: Path) -> int:
    """The folder of ``path`` as a descriptor; a symlink as the folder is refused."""
    return os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def read_history(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> History:
    """Validate the whole file and return it. A missing file is an empty history."""
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise HistoryError("cannot open the history folder") from None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        except FileNotFoundError:
            return History(())
        except OSError:
            raise HistoryError("cannot open the history file (a symlink?)") from None
        try:
            _lock(fd, fcntl.LOCK_SH, LOCK_WAIT_SECONDS)
            data = _read_fd(fd, max_bytes)
        except OSError:
            raise HistoryError("cannot read the history file") from None
        finally:
            os.close(fd)
    finally:
        os.close(dir_fd)
    return History(_parse(data)[0])


def append_records(
    path: Path,
    records: Sequence[Record],
    *,
    now: datetime | None = None,
    max_bytes: int = MAX_FILE_BYTES,
    skip_days: bool = False,
) -> int:
    """Append ``records`` (all or none) and return how many were written.

    Earlier lines are never rewritten. Refuses a damaged file, a second normal
    record for the same platform, metric, UTC day and source, a correction
    without a note or an original, a future time and bad fields. With
    ``skip_days`` a record whose day is already recorded is left out instead of
    refused, so a refresh that runs twice in a day is safe to repeat.
    """
    if not records or len(records) > MAX_BATCH:
        raise HistoryError(f"append 1 to {MAX_BATCH} records at a time")
    moment = now if now is not None else datetime.now(UTC)
    for record in records:
        validate(record, moment)
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise HistoryError("cannot open the history folder") from None
    try:
        return _append(dir_fd, path, records, max_bytes, skip_days)
    finally:
        os.close(dir_fd)


def dump_new_history(records: Sequence[Record], *, now: datetime | None = None) -> str:
    """Validate and serialize a complete new history file."""
    if not records:
        raise HistoryError("a new history needs at least one record")
    moment = now if now is not None else datetime.now(UTC)
    lines: list[bytes] = []
    previous = GENESIS
    for record in records:
        validate(record, moment)
        line = _line_bytes(record, previous)
        lines.append(line)
        previous = _digest(line)
    data = b"\n".join(lines) + b"\n"
    if len(data) > MAX_FILE_BYTES:
        raise HistoryError("the history file would be too large")
    _parse(data)
    return data.decode("ascii")


def _append(
    dir_fd: int,
    path: Path,
    records: Sequence[Record],
    max_bytes: int,
    skip_days: bool,
) -> int:
    try:
        fd = os.open(
            path.name,
            os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
    except OSError:
        raise HistoryError("cannot open the history file (a symlink?)") from None
    original = -1
    try:
        _lock(fd, fcntl.LOCK_EX, LOCK_WAIT_SECONDS)
        os.fchmod(fd, 0o600)  # a pre-created file may be wider
        existing = _read_fd(fd, max_bytes)
        before, prev = _parse(existing)
        if skip_days:
            have = {r.day_key for r in before if r.type == "record"}
            records = [
                r for r in records if r.type != "record" or r.day_key not in have
            ]
            if not records:
                return 0
        new = b""
        for record in records:
            line = _line_bytes(record, prev)
            new += line + b"\n"
            prev = _digest(line)
        _parse(existing + new)  # duplicates and corrections, before any write
        if len(existing) + len(new) > max_bytes:
            raise HistoryError("the history file would be too large")
        original = len(existing)
        _write_all(fd, new)
        os.fsync(fd)
        if not existing:
            os.fsync(dir_fd)
        return len(records)
    except OSError:
        if original >= 0:
            _roll_back(fd, original)
        raise HistoryError("cannot write the history file") from None
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _roll_back(fd: int, size: int) -> None:
    """Remove only the bytes this call added; say so if that fails too."""
    try:
        os.ftruncate(fd, size)
        os.fsync(fd)
    except OSError:
        raise HistoryError(
            "the write may be partly on disk; run history-verify before trying again"
        ) from None
