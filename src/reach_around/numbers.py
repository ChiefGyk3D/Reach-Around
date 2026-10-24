# SPDX-License-Identifier: Apache-2.0
"""The numbers file: one validated count per platform.

Shape (version 1)::

    {"version": 1,
     "platforms": {"<slug>": {"count": 1234, "as_of": "2026-10-24T09:00:00Z"}}}

``counts refresh`` writes it; the build overlays it on the platforms block.
This module is the single definition of the shape: the platforms work must
call ``load_numbers`` rather than parse the file again.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_COUNT = 10**10
MAX_FILE_BYTES = 1_048_576
MAX_PLATFORMS = 200

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class NumbersError(Exception):
    """Raised for every malformed numbers file."""


@dataclass(frozen=True)
class PlatformCount:
    count: int
    as_of: str


Numbers = dict[str, PlatformCount]


def utc_stamp(moment: datetime) -> str:
    """``moment`` as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_count(value: object) -> int:
    """Return ``value`` if it is an int from 0 to 10**10; bool and float are refused."""
    if type(value) is not int:
        raise NumbersError("count must be an integer")
    if not 0 <= value <= MAX_COUNT:
        raise NumbersError(f"count must be between 0 and {MAX_COUNT}")
    return value


def _check_as_of(value: object) -> str:
    if not isinstance(value, str) or not _AS_OF.match(value):
        raise NumbersError("as_of must be a UTC time like 2026-10-24T09:00:00Z")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise NumbersError("as_of is not a real date and time") from None
    return value


def check_slug(value: object) -> str:
    if not isinstance(value, str) or not _SLUG.match(value):
        raise NumbersError(f"platform slug {value!r} is not valid")
    return value


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise NumbersError(f"duplicate key {key!r}")
        out[key] = value
    return out


def parse_numbers(text: str) -> Numbers:
    """Parse and validate numbers-file text; every failure is a ``NumbersError``."""
    try:
        raw = json.loads(text, object_pairs_hook=_no_duplicates)
    except NumbersError:
        raise
    except (ValueError, RecursionError):
        raise NumbersError("not valid JSON") from None
    if not isinstance(raw, dict) or set(raw) != {"version", "platforms"}:
        raise NumbersError('top level must have exactly "version" and "platforms"')
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise NumbersError("version must be 1")
    platforms = raw["platforms"]
    if not isinstance(platforms, dict) or len(platforms) > MAX_PLATFORMS:
        raise NumbersError("platforms must be a mapping of at most 200 entries")
    out: Numbers = {}
    for slug, entry in platforms.items():
        check_slug(slug)
        if not isinstance(entry, dict) or set(entry) != {"count", "as_of"}:
            raise NumbersError(f'{slug}: needs exactly "count" and "as_of"')
        try:
            out[slug] = PlatformCount(
                check_count(entry["count"]), _check_as_of(entry["as_of"])
            )
        except NumbersError as exc:
            raise NumbersError(f"{slug}: {exc}") from None
    return out


def dump_numbers(numbers: Numbers) -> str:
    """Validated, deterministic numbers-file text (sorted keys, trailing newline)."""
    platforms: dict[str, dict[str, Any]] = {}
    for slug in sorted(numbers):
        check_slug(slug)
        entry = numbers[slug]
        platforms[slug] = {
            "count": check_count(entry.count),
            "as_of": _check_as_of(entry.as_of),
        }
    text = json.dumps({"version": 1, "platforms": platforms}, indent=2) + "\n"
    parse_numbers(text)  # round trip: what we write is what the build accepts
    return text


def read_capped(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    """Read a regular file (no symlink, no FIFO) of at most ``max_bytes``."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise NumbersError(f"cannot read {path}: {exc.strerror}") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise NumbersError(f"{path} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise NumbersError(f"cannot read {path}: {exc.strerror}") from None
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise NumbersError(f"{path} is larger than {max_bytes} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise NumbersError(f"{path} is not valid UTF-8") from None


def load_numbers(path: Path) -> Numbers:
    """Load and validate the numbers file at ``path``."""
    try:
        return parse_numbers(read_capped(path))
    except NumbersError as exc:
        raise NumbersError(f"{path}: {exc}") from None


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path``: temp file beside it, fsync, rename, mode 0600."""
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(
        dir=directory, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
