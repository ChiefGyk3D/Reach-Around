# SPDX-License-Identifier: Apache-2.0
"""The optional numbers file: platform counts measured outside the config.

Shape::

    {"version": 1, "platforms": {"<platform slug or item id>": {
        "count": 123, "as_of": "2026-10-24T12:00:00Z"}}}

The file is read at build time and never published. It is validated as strictly
as the config: a size cap, no duplicate keys, integer counts only, a real
timestamp. A key that names no platform in the config is ignored (the caller
reports how many); everything else wrong is a ``ConfigError`` naming the file.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from reach_around.config.blocks import MAX_COUNT, PLATFORM_SLUGS, Platforms
from reach_around.config.loader import ConfigError, _read

MAX_NUMBERS_BYTES = 256 * 1024
MAX_ENTRIES = 1000
MAX_KEY = 64
_EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)
_SKEW = timedelta(days=1)
_STAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})"
)


@dataclass(frozen=True)
class NumbersEntry:
    count: int
    as_of: datetime  # always UTC

    @property
    def as_of_date(self) -> date:
        return self.as_of.date()


@dataclass(frozen=True)
class Numbers:
    entries: Mapping[str, NumbersEntry]

    def lookup(self, item_id: str, platform: str) -> NumbersEntry | None:
        """The entry for one item: its own id first, then its platform slug."""
        return self.entries.get(item_id) or self.entries.get(platform)

    def unknown_keys(self, config: Any) -> list[str]:
        """Keys that are neither a platform slug nor an item id in ``config``."""
        known = set(PLATFORM_SLUGS)
        for tab in config.tabs:
            for block in tab.blocks:
                if isinstance(block, Platforms):
                    known.update(block.item_ids)
        return sorted(key for key in self.entries if key not in known)


def resolve_count(
    manual: int | None, manual_as_of: date | None, entry: NumbersEntry | None
) -> tuple[int | None, date | None]:
    """The count and date to show. The numbers file wins when the manual value
    has no date, or when its date is the same day or older."""
    if entry is None:
        return manual, manual_as_of
    if manual is None or manual_as_of is None or entry.as_of_date >= manual_as_of:
        return entry.count, entry.as_of_date
    return manual, manual_as_of


class _Bad(Exception):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _Bad(f"duplicate key {key!r}")
        out[key] = value
    return out


def _refuse_constant(name: str) -> Any:
    raise _Bad(f"{name} is not allowed")


def _stamp(key: str, value: Any, now: datetime) -> datetime:
    if type(value) is not str or _STAMP.fullmatch(value) is None:
        raise _Bad(f"{key}: as_of must be a timestamp like 2026-10-24T12:00:00Z")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _Bad(f"{key}: as_of is not a real date and time") from None
    stamp = parsed.astimezone(UTC)
    if stamp < _EARLIEST or stamp > now + _SKEW:
        raise _Bad(f"{key}: as_of is outside the allowed range")
    return stamp


def _entry(key: str, raw: Any, now: datetime) -> NumbersEntry:
    if not isinstance(raw, dict) or set(raw) != {"count", "as_of"}:
        raise _Bad(f"{key}: must be an object with exactly count and as_of")
    count = raw["count"]
    if type(count) is not int or not 0 <= count <= MAX_COUNT:
        raise _Bad(f"{key}: count must be a whole number from 0 to {MAX_COUNT}")
    return NumbersEntry(count=count, as_of=_stamp(key, raw["as_of"], now))


def _parse(data: bytes, now: datetime) -> Numbers:
    try:
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_refuse_constant,
        )
    except UnicodeDecodeError:
        raise _Bad("file is not valid UTF-8") from None
    except RecursionError:
        raise _Bad("nesting is too deep") from None
    except json.JSONDecodeError as exc:
        raise _Bad(f"invalid JSON: {exc.msg} at line {exc.lineno}") from None
    except ValueError as exc:
        if isinstance(exc, _Bad):
            raise
        raise _Bad(f"invalid JSON: {exc}") from None
    if not isinstance(doc, dict) or set(doc) != {"version", "platforms"}:
        raise _Bad("must be an object with exactly version and platforms")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise _Bad("version must be 1")
    platforms = doc["platforms"]
    if not isinstance(platforms, dict):
        raise _Bad("platforms must be an object")
    if len(platforms) > MAX_ENTRIES:
        raise _Bad(f"too many entries (limit {MAX_ENTRIES})")
    entries = {}
    for key, raw in platforms.items():
        if not 1 <= len(key) <= MAX_KEY:
            raise _Bad(f"platform key must be 1 to {MAX_KEY} characters")
        entries[key] = _entry(key, raw, now)
    return Numbers(entries=entries)


def parse_numbers(
    data: bytes, *, source: str = "numbers", now: datetime | None = None
) -> Numbers:
    """Validate numbers-file bytes; every failure is a ``ConfigError``."""
    when = now if now is not None else datetime.now(UTC)
    try:
        return _parse(data, when)
    except _Bad as exc:
        raise ConfigError(source, None, str(exc)) from None


def load_numbers(path: Path, *, now: datetime | None = None) -> Numbers:
    """Read and validate ``path``; every failure is a ``ConfigError``."""
    return parse_numbers(_read(path, MAX_NUMBERS_BYTES), source=str(path), now=now)
