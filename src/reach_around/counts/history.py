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

The one exception to "never rewritten" is ``redact_records`` (issue #586): an
explicit, owner-run command that replaces the lines of redacted records with
tombstones and re-chains every later line, so no value, note, era or digest of
the old bytes stays in the working file. It writes a new file beside the old
one under the same lock and renames it into place. It never runs during a
refresh. It does not touch git history of the file; see docs/social-history.md.
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
API_PROVIDERS = ("youtube_analytics", "youtube_data_api")
# YouTube Analytics records (#391) are identified ONLY by this provider tag. One
# rule everywhere: the report refuses it, and deletion, revocation and the
# retention notice select it. No metric-name guessing.
ANALYTICS_PROVIDER = "youtube_analytics"
REASONS = (
    "platform_expiry",
    "platform_revocation",
    "platform_request",
    "user_request",
    "owner_decision",
)
MAX_REDACT = 100_000

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_METRIC = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED = ("platform", "metric", "value", "as_of_utc", "source", "run_id", "prev")
_OPTIONAL = ("note", "era", "type", "api_provider")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOMB_FIELDS = frozenset(
    (
        "type",
        "platform",
        "metric",
        "as_of_utc",
        "source",
        "reason",
        "trigger_date",
        "redacted_at_utc",
        "run_id",
        "prev",
    )
)


NOTICE_OWNER_NOT_KEPT = (
    "notice: the owner and group of the old file could not be kept; "
    "the new file belongs to the user who ran this command"
)
_STALE_AFTER_SECONDS = 3600.0


class HistoryError(Exception):
    """The history file or a record is not acceptable. Never quotes content."""


class RedactDurabilityError(HistoryError):
    """The new file replaced the old one, but the folder sync failed afterward."""


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
    api_provider: str | None = None

    @property
    def day_key(self) -> tuple[str, str, str, str]:
        return (self.platform, self.metric, self.as_of_utc[:10], self.source)

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.platform, self.metric, self.as_of_utc, self.source)


@dataclass(frozen=True)
class Tombstone:
    """A redacted record: its key, why and when, and no value or free text."""

    platform: str
    metric: str
    as_of_utc: str
    source: str
    reason: str
    trigger_date: str  # YYYY-MM-DD
    redacted_at_utc: str
    run_id: str

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
    if record.api_provider is not None:
        if record.source != "api":
            raise HistoryError("api_provider is allowed only for API records")
        if record.api_provider not in API_PROVIDERS:
            raise HistoryError("api_provider is not recognized")
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


def _plain_date(name: str, value: object) -> date:
    if type(value) is not str or not _DATE.match(value):
        raise HistoryError(f"{name} must look like 2026-11-19")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HistoryError(f"{name} is not a real date") from None


def validate_tombstone(tomb: Tombstone, now: datetime | None = None) -> None:
    """Raise ``HistoryError`` unless ``tomb`` is a well-formed tombstone."""
    for name in (
        "platform",
        "metric",
        "as_of_utc",
        "source",
        "reason",
        "trigger_date",
        "redacted_at_utc",
        "run_id",
    ):
        if type(getattr(tomb, name)) is not str:
            raise HistoryError(f"{name} must be text")
    if not _SLUG.match(tomb.platform):
        raise HistoryError("platform must be a lowercase slug")
    if not _METRIC.match(tomb.metric):
        raise HistoryError("metric must be lowercase letters, digits, underscores")
    when = parse_as_of(tomb.as_of_utc)
    if now is not None and when > now.astimezone(UTC):
        raise HistoryError("as_of_utc is in the future")
    if tomb.source not in SOURCES:
        raise HistoryError("source must be api, hand_kept or imported")
    if tomb.reason not in REASONS:
        raise HistoryError("reason is not on the closed list")
    trigger = _plain_date("trigger_date", tomb.trigger_date)
    done = parse_as_of(tomb.redacted_at_utc)
    if now is not None and done > now.astimezone(UTC):
        raise HistoryError("redacted_at_utc is in the future")
    if trigger > done.date():
        raise HistoryError("trigger_date is after the redaction")
    if not _RUN_ID.match(tomb.run_id) or not tomb.run_id.startswith("redact-"):
        raise HistoryError("run_id of a tombstone must start with redact-")


def new_redact_run_id(now: datetime) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"redact-{stamp}-{secrets.token_hex(3)}"


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
    if record.api_provider is not None:
        obj["api_provider"] = record.api_provider
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return text.encode("ascii")


def _tomb_line(tomb: Tombstone, prev: str) -> bytes:
    obj: dict[str, Any] = {
        "type": "tombstone",
        "platform": tomb.platform,
        "metric": tomb.metric,
        "as_of_utc": tomb.as_of_utc,
        "source": tomb.source,
        "reason": tomb.reason,
        "trigger_date": tomb.trigger_date,
        "redacted_at_utc": tomb.redacted_at_utc,
        "run_id": tomb.run_id,
        "prev": prev,
    }
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


def _parse_line(
    raw: bytes, number: int, now: datetime | None = None
) -> tuple[Record | Tombstone, str]:
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
    if obj.get("type") == "tombstone":
        return _parse_tombstone(obj, keys, where, now)
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
        api_provider=obj.get("api_provider"),
    )
    try:
        validate(record, now)
    except HistoryError as exc:
        raise HistoryError(f"{where}: {exc}") from None
    return record, prev


def _parse_tombstone(
    obj: dict[str, Any], keys: set[str], where: str, now: datetime | None
) -> tuple[Tombstone, str]:
    if keys != _TOMB_FIELDS:
        raise HistoryError(f"{where}: wrong set of fields for a tombstone")
    prev = obj["prev"]
    if type(prev) is not str or not _HASH.match(prev):
        raise HistoryError(f"{where}: prev is not a SHA-256 hex digest")
    tomb = Tombstone(
        platform=obj["platform"],
        metric=obj["metric"],
        as_of_utc=obj["as_of_utc"],
        source=obj["source"],
        reason=obj["reason"],
        trigger_date=obj["trigger_date"],
        redacted_at_utc=obj["redacted_at_utc"],
        run_id=obj["run_id"],
    )
    try:
        validate_tombstone(tomb, now)
    except HistoryError as exc:
        raise HistoryError(f"{where}: {exc}") from None
    return tomb, prev


def _parse(
    data: bytes, now: datetime | None = None
) -> tuple[tuple[Record | Tombstone, ...], str]:
    """All lines in ``data`` and the digest of the last line."""
    if not data:
        return (), GENESIS
    if not data.endswith(b"\n"):
        raise HistoryError("the last line is truncated (no newline); not repaired")
    entries: list[Record | Tombstone] = []
    seen: dict[tuple[str, str, str, str], str | None] = {}  # key -> its provider
    redacted: set[tuple[str, str, str, str]] = set()
    days: set[tuple[str, str, str, str]] = set()
    expect = GENESIS
    for number, raw in enumerate(data[:-1].split(b"\n"), start=1):
        entry, prev = _parse_line(raw, number, now)
        if prev != expect:
            raise HistoryError(f"line {number}: the hash chain is broken")
        if isinstance(entry, Tombstone):
            if entry.key in redacted:
                raise HistoryError(f"line {number}: duplicate tombstone")
            if entry.key in seen:
                raise HistoryError(f"line {number}: a tombstone over a live record")
            if entry.day_key in days:
                raise HistoryError(f"line {number}: a second record for that UTC day")
            redacted.add(entry.key)
            days.add(entry.day_key)
        elif entry.key in redacted:
            raise HistoryError(f"line {number}: that record was redacted")
        elif entry.type == "record":
            if entry.key in seen:
                raise HistoryError(f"line {number}: duplicate record")
            if entry.day_key in days:
                raise HistoryError(f"line {number}: a second record for that UTC day")
            seen[entry.key] = entry.api_provider
            days.add(entry.day_key)
        elif entry.key not in seen:
            raise HistoryError(f"line {number}: a correction with no earlier record")
        elif seen[entry.key] != entry.api_provider:
            # A correction keeps the provenance of the record it fixes, so
            # deletion, revocation and the notice still find its value.
            raise HistoryError(
                f"line {number}: a correction's api_provider must match its original"
            )
        entries.append(entry)
        expect = _digest(raw)
    return tuple(entries), expect


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
    tombstones: tuple[Tombstone, ...] = ()

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


def _same_file(dir_fd: int, name: str, fd: int) -> bool:
    """True while ``name`` in the folder is still the file ``fd`` holds."""
    try:
        now = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    held = os.fstat(fd)
    return (now.st_dev, now.st_ino) == (held.st_dev, held.st_ino)


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
    """The folder of ``path`` as a descriptor, walking the whole chain.

    Each component is opened relative to the one before it with ``O_NOFOLLOW``,
    starting at the filesystem root (absolute path) or the current directory
    (relative path), so a symlink anywhere in the chain is refused and nothing
    outside the apparent path is ever opened. (``openat2`` with
    ``RESOLVE_NO_SYMLINKS`` is not used: it is not in the standard library.)
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    folder = path.parent
    current = os.open("/" if folder.is_absolute() else ".", flags)
    try:
        for part in folder.parts:
            if part in ("/", "."):
                continue
            try:
                following = os.open(part, flags, dir_fd=current)
            except OSError:
                try:
                    is_link = stat.S_ISLNK(
                        os.stat(part, dir_fd=current, follow_symlinks=False).st_mode
                    )
                except OSError:
                    is_link = False
                if is_link:
                    raise HistoryError(
                        "the history folder path holds a symlink; refused"
                    ) from None
                raise
            os.close(current)
            current = following
        return current
    except BaseException:
        os.close(current)
        raise


def read_history(path: Path, *, max_bytes: int = MAX_FILE_BYTES) -> History:
    """Validate the whole file and return it. A missing file is an error."""
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise HistoryError("cannot open the history folder") from None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        except FileNotFoundError:
            raise HistoryError("cannot open the history file") from None
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
    entries = _parse(data, datetime.now(UTC))[0]
    return History(
        tuple(e for e in entries if isinstance(e, Record)),
        tuple(e for e in entries if isinstance(e, Tombstone)),
    )


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
        if not _same_file(dir_fd, path.name, fd):
            raise HistoryError(
                "the history file was replaced while waiting (a redaction?); run again"
            )
        os.fchmod(fd, 0o600)  # a pre-created file may be wider
        existing = _read_fd(fd, max_bytes)
        before, prev = _parse(existing)
        if skip_days:
            have = {
                r.day_key
                for r in before
                if isinstance(r, Tombstone) or r.type == "record"
            }
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


def tombstoned(history: History) -> frozenset[tuple[str, str, str, str]]:
    """Keys (platform, metric, as-of, source) that are redacted: gaps, never zero."""
    return frozenset(t.key for t in history.tombstones)


def _need_hand_kept_flag() -> HistoryError:
    return HistoryError(
        "only api rows are redacted; hand-kept and imported rows need "
        "--include-hand-kept"
    )


def plan_redaction(
    history: History,
    *,
    platform: str,
    source: str | None,
    before: str | None,
    metric: str | None = None,
    include_hand_kept: bool = False,
) -> list[Record]:
    """Pure. The live records a redaction would blank. Never touches disk.

    Rows from ``api`` only, unless ``include_hand_kept`` is true. ``before`` is a
    UTC date (``YYYY-MM-DD``): rows on earlier days are chosen; ``None`` chooses
    every day.
    """
    if type(platform) is not str or not _SLUG.match(platform):
        raise HistoryError("platform must be a lowercase slug")
    if metric is not None and (type(metric) is not str or not _METRIC.match(metric)):
        raise HistoryError("metric must be lowercase letters, digits, underscores")
    if source is not None:
        if type(source) is not str or source not in SOURCES:
            raise HistoryError("source must be api, hand_kept or imported")
        if source != "api" and include_hand_kept is not True:
            raise _need_hand_kept_flag()
    cut = _plain_date("before", before).isoformat() if before is not None else None
    chosen: list[Record] = []
    for record in history.records:
        if record.type != "record" or record.platform != platform:
            continue
        if metric is not None and record.metric != metric:
            continue
        if source is not None and record.source != source:
            continue
        if source is None and record.source != "api" and include_hand_kept is not True:
            continue
        if cut is not None and record.as_of_utc[:10] >= cut:
            continue
        chosen.append(record)
    return chosen


def redact_records(
    path: Path,
    plan: Sequence[Record],
    *,
    reason: str,
    trigger_date: str,
    now: datetime | None = None,
    confirm: bool = False,
    include_hand_kept: bool = False,
    max_bytes: int = MAX_FILE_BYTES,
    lock_wait: float = LOCK_WAIT_SECONDS,
    notices: list[str] | None = None,
) -> int:
    """Replace the planned records with tombstones and re-chain; return the count.

    Owner-run only, never during a refresh. Refuses unless ``confirm`` is ``True``.
    Under the exclusive lock the whole file is checked, a new file is written
    beside the old one, fsynced and renamed into place. The corrections of a
    redacted record are removed with it. A later line's ``prev`` is recomputed,
    so no digest of the old bytes stays. A plan that does not match the file
    (rows already redacted, or any field of a planned record differs from the
    file) is refused, as is a plan with a duplicate; an empty plan writes nothing.

    A failure before the rename leaves the original file. If the rename
    succeeded and the folder sync failed, ``RedactDurabilityError`` is raised:
    the new file is in place, only its durability is uncertain. The new file
    keeps the old owner and group where permitted (a fixed notice is appended to
    ``notices`` when not) and mode 0600. ACLs, extended attributes and security
    labels are not copied.
    """
    if confirm is not True:
        raise HistoryError("redaction needs confirm=True; nothing was written")
    if type(reason) is not str or reason not in REASONS:
        raise HistoryError("reason is not on the closed list")
    trigger = _plain_date("trigger_date", trigger_date)
    moment = (now if now is not None else datetime.now(UTC)).astimezone(UTC)
    if trigger > moment.date():
        raise HistoryError("trigger_date is in the future")
    if len(plan) > MAX_REDACT:
        raise HistoryError(f"redact at most {MAX_REDACT} records at a time")
    planned: dict[tuple[str, str, str, str], bytes] = {}
    for item in plan:
        if type(item) is not Record or item.type != "record":
            raise HistoryError("the plan holds something that is not a record")
        validate(item)
        if item.source != "api" and include_hand_kept is not True:
            raise _need_hand_kept_flag()
        if item.key in planned:
            raise HistoryError("the plan holds a duplicate record")
        planned[item.key] = _line_bytes(item, GENESIS)
    if not planned:
        return 0
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise HistoryError("cannot open the history folder") from None
    try:
        return _redact(
            dir_fd,
            path,
            planned,
            reason,
            trigger_date,
            moment,
            max_bytes,
            lock_wait,
            notices,
        )
    finally:
        os.close(dir_fd)


def _redact(
    dir_fd: int,
    path: Path,
    planned: dict[tuple[str, str, str, str], bytes],
    reason: str,
    trigger_date: str,
    moment: datetime,
    max_bytes: int,
    lock_wait: float,
    notices: list[str] | None,
) -> int:
    try:
        fd = os.open(
            path.name,
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=dir_fd,
        )
    except OSError:
        raise HistoryError(
            "cannot open the history file (missing or a symlink?)"
        ) from None
    try:
        _lock(fd, fcntl.LOCK_EX, lock_wait)
        if not _same_file(dir_fd, path.name, fd):
            raise HistoryError("the history file changed while locking; run again")
        try:
            existing = _read_fd(fd, max_bytes)
        except OSError:
            raise HistoryError("cannot read the history file") from None
        entries, _ = _parse(existing)
        keys = set(planned)
        live = {
            e.key: _line_bytes(e, GENESIS)
            for e in entries
            if isinstance(e, Record) and e.type == "record"
        }
        if any(live.get(key) != line for key, line in planned.items()):
            raise HistoryError(
                "the plan does not match the file (rows already redacted or "
                "changed); plan again"
            )
        stamp = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
        run_id = new_redact_run_id(moment)
        raws = existing[:-1].split(b"\n")
        first = next(
            i for i, e in enumerate(entries) if isinstance(e, Record) and e.key in keys
        )
        out: list[bytes] = list(raws[:first])
        prev = _digest(raws[first - 1]) if first else GENESIS
        for entry in entries[first:]:
            if isinstance(entry, Tombstone):
                line = _tomb_line(entry, prev)
            elif entry.key in keys:
                if entry.type != "record":
                    continue  # a correction goes with the record it corrected
                tomb = Tombstone(
                    platform=entry.platform,
                    metric=entry.metric,
                    as_of_utc=entry.as_of_utc,
                    source=entry.source,
                    reason=reason,
                    trigger_date=trigger_date,
                    redacted_at_utc=stamp,
                    run_id=run_id,
                )
                validate_tombstone(tomb, moment)
                line = _tomb_line(tomb, prev)
            else:
                line = _line_bytes(entry, prev)
            out.append(line)
            prev = _digest(line)
        data = b"\n".join(out) + b"\n"
        if len(data) > max_bytes:
            raise HistoryError("the history file would be too large")
        _parse(data)  # the result must verify before it replaces anything
        info = os.fstat(fd)
        _replace_file(dir_fd, path.name, data, (info.st_uid, info.st_gid), notices)
        return len(keys)
    finally:
        os.close(fd)


def _replace_file(
    dir_fd: int,
    name: str,
    data: bytes,
    owner: tuple[int, int],
    notices: list[str] | None,
) -> None:
    """Write ``data`` beside ``name``, fsync, rename over it, fsync the folder."""
    temp = f".{name}.redact-{secrets.token_hex(4)}.tmp"
    try:
        tfd = os.open(
            temp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
    except OSError:
        raise HistoryError("cannot write the history file") from None
    try:
        try:
            _write_all(tfd, data)
            try:
                os.fchown(tfd, owner[0], owner[1])
            except PermissionError:
                if notices is not None:
                    notices.append(NOTICE_OWNER_NOT_KEPT)
            os.fsync(tfd)
        finally:
            os.close(tfd)
        os.rename(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError:
        try:
            os.unlink(temp, dir_fd=dir_fd)
        except OSError:
            pass
        raise HistoryError("cannot write the history file") from None
    try:
        os.fsync(dir_fd)
    except OSError:
        raise RedactDurabilityError(
            "the new file replaced the file, but the folder sync failed, so "
            "durability is uncertain; run history-verify"
        ) from None


def _stale_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"^\." + re.escape(name) + r"\.redact-[0-9a-f]{8}\.tmp$")


def _is_stale(dir_fd: int, name: str, pattern: re.Pattern[str]) -> bool:
    if not pattern.match(name):
        return False
    try:
        info = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and info.st_mtime < time.time() - _STALE_AFTER_SECONDS
    )


def find_stale_redaction_temps(path: Path) -> list[str]:
    """Names of leftover ``.<file>.redact-*.tmp`` files a crashed redaction left.

    Only regular files in the history folder, owned by the invoking user, of
    exactly that name pattern and older than an hour. They hold the proposed
    redacted output, so they are private history and should not linger.
    """
    pattern = _stale_pattern(path.name)
    dir_fd = _open_parent(path)
    try:
        with os.scandir(dir_fd) as entries:
            names = [e.name for e in entries if pattern.match(e.name)]
        return sorted(n for n in names if _is_stale(dir_fd, n, pattern))
    except OSError:
        raise HistoryError("cannot list the history folder") from None
    finally:
        os.close(dir_fd)


def remove_stale_redaction_temps(path: Path, names: Sequence[str]) -> int:
    """Remove the named stale temporaries; anything else in ``names`` is ignored."""
    pattern = _stale_pattern(path.name)
    dir_fd = _open_parent(path)
    removed = 0
    try:
        for name in names:
            if type(name) is str and _is_stale(dir_fd, name, pattern):
                try:
                    os.unlink(name, dir_fd=dir_fd)
                except FileNotFoundError:
                    continue
                except OSError:
                    raise HistoryError("cannot remove a stale temporary file") from None
                removed += 1
    finally:
        os.close(dir_fd)
    return removed
