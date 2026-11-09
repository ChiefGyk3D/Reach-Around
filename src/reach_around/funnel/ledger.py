# SPDX-License-Identifier: Apache-2.0
"""The release ledger for the source and country by destination tables.

THREAT MODEL. The cell report is run by the owner, on the owner's machine, over
the owner's private data. The owner is a trusted operator. The ledger defends
against ACCIDENTS: releasing a week and then a month over the same days,
repeating a period with a different (stale or partial) export, losing the file
or restoring an old copy by mistake. It does not defend against an operator who
edits his own files on purpose, or a hostile caller inside the process.

Mandatory for every cell release, weeks and months alike: without a readable,
verified ledger the cell tables are not released. The file is created only by
``init_ledger`` (an explicit command), never silently, and lives in the private
instance repo; commit it TOGETHER with its ``.head`` file after each release so
the history is not lost. A release is recorded BEFORE anything is shown (see
``reach_around.funnel.release``).

An append-only JSON-lines file, hash-chained like the social-stats history
(``reach_around.counts.history``): every line carries ``prev``, the SHA-256 of
the previous line's bytes, appends are fsynced under an exclusive lock, and a
file that fails any check (a changed, removed or reordered line, a cut-short
last line, a symlink, a record that breaks the rule below) is refused, never
repaired. One record per released period: site, period kind (week or month),
first and last UTC day, when it was released, and a digest of the exact input
cells behind it. The digest is a hash of private counts: keep the ledger private.
A small ``.head`` file next to it (sequence number and last chain hash, written
atomically) catches a ledger cut short or replaced by an older copy.

The rule it keeps: for one site, a week and a month that share any UTC day are
never both released, because a reader holding both can difference them. A
repeat release of the exact same period is allowed (and not written again).
"""

from __future__ import annotations

import fcntl
import hashlib
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
    _lock,
    _no_constant,
    _no_float,
    _open_parent,
    _pairs,
    _read_fd,
    _write_all,
)

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_LINE_BYTES = 640
MAX_RECORDS = 20_000
LOCK_WAIT_SECONDS = 30.0
KINDS = ("week", "month")
TYPES = ("release", "supersede")
HEAD_SUFFIX = ".head"
RECOVERY = (
    "restore the ledger and its .head file together from git (they are "
    "committed together); never edit them by hand"
)

_SITE = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = {"site", "kind", "start", "end", "released_at", "digest", "type", "prev"}


class LedgerError(HistoryError):
    """The ledger is unusable or the release would overlap. Never quotes content."""


@dataclass(frozen=True)
class Release:
    site: str
    kind: str
    start: str
    end: str
    released_at: str
    digest: str = "0" * 64
    type: str = "release"

    def same_period(self, other: Release) -> bool:
        return (self.site, self.kind, self.start, self.end) == (
            other.site,
            other.kind,
            other.start,
            other.end,
        )

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
    if release.type not in TYPES:
        raise LedgerError("the record type is not release or supersede")
    if not _HASH.match(release.digest):
        raise LedgerError("the digest is not a SHA-256 hex digest")
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
        "digest": release.digest,
        "type": release.type,
        "prev": prev,
    }
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("ascii")


def _digest(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


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
        obj["site"],
        obj["kind"],
        obj["start"],
        obj["end"],
        obj["released_at"],
        obj["digest"],
        obj["type"],
    )
    try:
        validate(release)
    except LedgerError as exc:
        raise LedgerError(f"{where}: {exc}") from None
    return release, obj["prev"]


def latest_for(existing: Sequence[Release], new: Release) -> Release | None:
    found = None
    for old in existing:
        if old.same_period(new):
            found = old
    return found


def check_new(
    existing: Sequence[Release], new: Release, *, supersede: bool = False
) -> str:
    """Decide what recording ``new`` means: 'repeat', 'release' or 'supersede'.

    Raises when it overlaps a period of the other kind, when a repeat of a period
    carries different data (unless ``supersede``), or when periods of one kind
    overlap without being the same period.
    """
    for old in existing:
        if old.site != new.site or old.same_period(new):
            continue
        if old.kind != new.kind and old.overlaps(new):
            raise LedgerError(
                f"the {new.kind} {new.start} to {new.end} shares a day with a "
                f"{old.kind} already released ({old.start} to {old.end}); a "
                "week and a month over the same day would let a reader "
                "difference them"
            )
        if old.kind == new.kind and old.overlaps(new):
            raise LedgerError("periods of one kind must not overlap")
    prior = latest_for(existing, new)
    if prior is None:
        return "release"
    if prior.digest == new.digest:
        return "repeat"
    if supersede:
        return "supersede"
    raise LedgerError(
        "this period was released before with different data: the export "
        "behind it changed (a partial or stale export, or late counts). "
        "Re-run with the same export as before, or record a correction "
        "with --supersede (it is recorded too, and two releases of one "
        "period can let a reader difference them)"
    )


def _check_sequence(out: Sequence[Release], release: Release) -> None:
    """The rules a file must keep, whatever wrote it."""
    if release.type == "release":
        if latest_for(out, release) is not None:
            raise LedgerError("a second plain release of one period")
        check_new(out, release)
    else:
        if latest_for(out, release) is None:
            raise LedgerError("a correction with no earlier release")
        check_new(out, release, supersede=True)


def _parse(data: bytes) -> tuple[list[Release], list[str]]:
    """The records and the chain hash after each of them."""
    if not data:
        return [], []
    if not data.endswith(b"\n"):
        raise LedgerError("the last line is truncated; not repaired")
    out: list[Release] = []
    hashes: list[str] = []
    expect = GENESIS
    lines = data[:-1].split(b"\n")
    if len(lines) > MAX_RECORDS:
        raise LedgerError("too many records")
    for number, raw in enumerate(lines, start=1):
        release, prev = _parse_line(raw, number)
        if prev != expect:
            raise LedgerError(f"line {number}: the hash chain is broken")
        try:
            _check_sequence(out, release)
        except LedgerError:
            raise LedgerError(f"line {number}: breaks the release rule") from None
        out.append(release)
        expect = _digest(raw)
        hashes.append(expect)
    return out, hashes


# ---- the head file ---------------------------------------------------------


def _head_name(name: str) -> str:
    return name + HEAD_SUFFIX


def _read_head(dir_fd: int, name: str) -> tuple[int, str] | None:
    try:
        fd = os.open(_head_name(name), os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise LedgerError("cannot open the ledger head file (a symlink?)") from None
    try:
        data = _read_fd(fd, 1024)
    except OSError:
        raise LedgerError("cannot read the ledger head file") from None
    finally:
        os.close(fd)
    try:
        obj = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LedgerError("the ledger head file is not strict JSON") from None
    if (
        not isinstance(obj, dict)
        or set(obj) != {"seq", "head"}
        or not isinstance(obj["seq"], int)
        or isinstance(obj["seq"], bool)
        or obj["seq"] < 0
        or not isinstance(obj["head"], str)
        or not _HASH.match(obj["head"])
    ):
        raise LedgerError("the ledger head file is not valid")
    return obj["seq"], obj["head"]


def _write_head(dir_fd: int, name: str, seq: int, head: str) -> None:
    """Write the head file atomically: temp file, fsync, rename, fsync the folder."""
    tmp = _head_name(name) + ".tmp"
    data = json.dumps({"head": head, "seq": seq}, sort_keys=True).encode("ascii")
    try:
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.rename(tmp, _head_name(name), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except OSError:
        raise LedgerError("cannot write the ledger head file") from None


def _verify_head(
    dir_fd: int, name: str, records: Sequence[Release], hashes: Sequence[str]
) -> None:
    """The ledger must match its head file; anything else fails closed."""
    head = _read_head(dir_fd, name)
    n = len(records)
    if head is None:
        if n:
            raise LedgerError(
                "the ledger has records but its .head file is missing, so a cut "
                f"or replaced ledger cannot be ruled out; {RECOVERY} (or, only if "
                "you know the ledger is whole, run funnel-ledger-reanchor)"
            )
        return
    seq, last = head
    if seq > n:
        raise LedgerError(
            f"the ledger is shorter than its .head file (a record was lost or an "
            f"older copy was restored); {RECOVERY}"
        )
    chain = GENESIS if seq == 0 else hashes[seq - 1]
    if last != chain:
        raise LedgerError(f"the ledger and its .head file disagree; {RECOVERY}")
    if seq < n - 1:
        raise LedgerError(
            f"the ledger has records the .head file does not know; {RECOVERY}"
        )
    # seq == n - 1 is the one crash window (appended, head not yet written):
    # accepted here and repaired by the next record.


def _open_ledger(path: Path, flags: int) -> tuple[int, int]:
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise LedgerError("cannot open the ledger folder") from None
    try:
        fd = os.open(path.name, flags | os.O_NOFOLLOW, dir_fd=dir_fd)
    except FileNotFoundError:
        os.close(dir_fd)
        raise LedgerError(
            "no release ledger at that path; create it once with "
            "'reach-around funnel-ledger-init FILE' and commit it with its "
            ".head file"
        ) from None
    except OSError:
        os.close(dir_fd)
        raise LedgerError("cannot open the ledger (a symlink?)") from None
    return dir_fd, fd


def read_ledger(path: Path) -> list[Release]:
    """Validate the whole ledger and its head file; return the records."""
    dir_fd, fd = _open_ledger(path, os.O_RDONLY)
    try:
        _lock(fd, fcntl.LOCK_SH, LOCK_WAIT_SECONDS)
        records, hashes = _parse(_read_fd(fd, MAX_FILE_BYTES))
        _verify_head(dir_fd, path.name, records, hashes)
        return records
    except HistoryError as exc:
        raise LedgerError(str(exc)) from None
    except OSError:
        raise LedgerError("cannot read the ledger") from None
    finally:
        os.close(fd)
        os.close(dir_fd)


def record_release(path: Path, release: Release, *, supersede: bool = False) -> str:
    """Check ``release`` against the ledger and append it: 'release', 'supersede'
    or 'repeat' (nothing written). Raises ``LedgerError``, having written
    nothing, when the ledger is missing or damaged, cannot be written, the period
    overlaps one of the other kind, or it repeats a period with different data.
    """
    validate(release)
    dir_fd, fd = _open_ledger(path, os.O_RDWR | os.O_APPEND)
    try:
        _lock(fd, fcntl.LOCK_EX, LOCK_WAIT_SECONDS)
        os.fchmod(fd, 0o600)
        existing = _read_fd(fd, MAX_FILE_BYTES)
        before, hashes = _parse(existing)
        _verify_head(dir_fd, path.name, before, hashes)
        what = check_new(before, release, supersede=supersede)
        if what == "repeat":
            return what
        stamped = Release(
            release.site,
            release.kind,
            release.start,
            release.end,
            release.released_at,
            release.digest,
            what,
        )
        prev = hashes[-1] if hashes else GENESIS
        line = _line(stamped, prev)
        if len(existing) + len(line) + 1 > MAX_FILE_BYTES:
            raise LedgerError("the ledger would be too large")
        _write_all(fd, line + b"\n")
        os.fsync(fd)
        _write_head(dir_fd, path.name, len(before) + 1, _digest(line))
        return what
    except HistoryError as exc:
        raise LedgerError(str(exc)) from None
    except OSError:
        raise LedgerError("cannot write the ledger") from None
    finally:
        os.close(fd)
        os.close(dir_fd)


def init_ledger(path: Path) -> None:
    """Create an empty ledger and its head file (owner-only). Never overwrites."""
    try:
        dir_fd = _open_parent(path)
    except OSError:
        raise LedgerError("cannot open the ledger folder") from None
    try:
        try:
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            raise LedgerError("a file is already there; not overwritten") from None
        except OSError:
            raise LedgerError("cannot create the ledger") from None
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _write_head(dir_fd, path.name, 0, GENESIS)
    finally:
        os.close(dir_fd)


def reanchor_ledger(path: Path) -> int:
    """Rewrite the head file from a ledger that verifies on its own.

    Only for a ledger known to be whole whose head file was lost. It cannot
    detect a ledger that was cut before this call. Returns the record count.
    """
    dir_fd, fd = _open_ledger(path, os.O_RDONLY)
    try:
        _lock(fd, fcntl.LOCK_SH, LOCK_WAIT_SECONDS)
        records, hashes = _parse(_read_fd(fd, MAX_FILE_BYTES))
        _write_head(dir_fd, path.name, len(records), hashes[-1] if hashes else GENESIS)
        return len(records)
    except HistoryError as exc:
        raise LedgerError(str(exc)) from None
    except OSError:
        raise LedgerError("cannot read the ledger") from None
    finally:
        os.close(fd)
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
