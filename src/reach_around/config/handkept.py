# SPDX-License-Identifier: Apache-2.0
"""Hand-kept measure entries (kit v2, slice 6, #631).

An entry is one registry measure the owner typed, or imported from a platform
export, for a platform that has no collector. It carries the registry key, so
the unit, the definition and the default audience come from the registry and are
never restated. Every entry compiles to a ``Measure`` labeled ``hand-kept``; an
imported one names its export in the method line (spec 6.2: there is no fourth
label).

The wall. A measure is never visible more widely than the display policy of the
field it came from. An entry names that field (default ``analytics``); the
measure takes the narrower of the registry default and that field's policy. An
export adds a floor in code (``EXPORTS``): the file cannot name a floor, a
visibility or a wider field than the importer set, so a hand-edited file cannot
publish what an importer made private.

The file ``hand-kept.json`` is data an owner or an importer wrote and a build
reads, so it is read strictly: a regular file (never a link, a pipe or a
directory), at most ``MAX_BYTES``, at most ``MAX_ENTRIES`` entries, exactly the
keys below, no duplicate keys, no ``NaN``. Errors name the field that is wrong
and never print a value from the file.

Nothing here opens a network connection.
"""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from reach_around.config.cutover import MIN_DAY, utc_today
from reach_around.config.measures import (
    REGISTRY,
    Measure,
    MeasureVisibility,
    narrow,
    source_level,
)
from reach_around.config.numbers import STALE_DAYS
from reach_around.config.trust import HAND_KEPT, Trust

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 2000
MAX_VALUE = 10**12
MAX_NOTE = 200
DEFAULT_FIELD = "analytics"
FILE_NAME = "hand-kept.json"

_PLATFORM = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENTRY_KEYS = frozenset(
    {
        "platform",
        "key",
        "value",
        "as_of",
        "period_start",
        "period_end",
        "note",
        "export",
        "field",
    }
)
_REQUIRED = frozenset({"platform", "key", "value", "as_of"})


class HandKeptError(ValueError):
    """A refused entry or file. The message names a field, never a value."""


@dataclass(frozen=True)
class ExportInfo:
    """One platform export an importer reads. The floor is set here, in code."""

    label: str  # printed in the method line: "Imported from the <label> export"
    field: str  # the config field whose display policy the measures start from
    floor: MeasureVisibility  # the narrowest level any measure of it may be wider than


# YouTube Analytics figures are the channel owner's authorized data and stay
# private (owner decision 2026-11-19, docs/youtube-analytics.md).
EXPORTS: Mapping[str, ExportInfo] = {
    "youtube-analytics": ExportInfo("YouTube analytics", "analytics", "private"),
}


@dataclass(frozen=True)
class HandKeptEntry:
    platform: str
    key: str
    value: int
    as_of: date
    period: tuple[date, date] | None = None
    note: str = ""
    export: str = ""  # a key of EXPORTS, or "" when the owner typed it
    field: str = DEFAULT_FIELD

    @property
    def identity(self) -> tuple[str, str, date, tuple[date, date] | None]:
        return (self.platform, self.key, self.as_of, self.period)


def _bad_note(text: str) -> bool:
    return len(text) > MAX_NOTE or any(
        ord(ch) < 32 or ord(ch) == 127 or ch in "<>" for ch in text
    )


def make_entry(
    *,
    platform: str,
    key: str,
    value: Any,
    as_of: date,
    unit: str | None = None,
    period: tuple[date, date] | None = None,
    note: str = "",
    export: str = "",
    field: str | None = None,
    today: date | None = None,
) -> HandKeptEntry:
    """Validate one entry against the registry. ``unit``, when given, must be the
    registry's own unit for the key (a check for the person typing it)."""
    day = today if today is not None else utc_today()
    if not isinstance(platform, str) or not _PLATFORM.match(platform):
        raise HandKeptError(
            "platform must be lowercase letters, digits, hyphens or underscores"
        )
    reg = REGISTRY.get(key) if isinstance(key, str) else None
    if reg is None:
        raise HandKeptError("key is not in the measure registry")
    if reg.computed:
        raise HandKeptError(
            "this measure is worked out by the build; it cannot be typed"
        )
    if unit is not None and unit != reg.unit:
        raise HandKeptError("unit does not match the registry unit for this key")
    if type(value) is not int:
        raise HandKeptError("value must be a whole number")
    if abs(value) > MAX_VALUE or (value < 0 and not reg.signed):
        raise HandKeptError("value is out of range for this measure")
    if not isinstance(as_of, date) or as_of < MIN_DAY or as_of > day:
        raise HandKeptError("as_of must be a day from 2000-01-01 up to today (UTC)")
    if period is not None:
        start, end = period
        if start < MIN_DAY or start > end or end > as_of:
            raise HandKeptError(
                "period must start on or before it ends, and end by as_of"
            )
    if not isinstance(note, str) or _bad_note(note):
        raise HandKeptError("note must be plain text of at most 200 characters")
    if export:
        info = EXPORTS.get(export)
        if info is None:
            raise HandKeptError("export is not a known export")
        if field is not None and field != info.field:
            raise HandKeptError("field is set by the export and cannot be changed")
        chosen = info.field
    else:
        chosen = DEFAULT_FIELD if field is None else field
    from reach_around.config.kit import KIT_FIELD_POLICY

    if chosen not in KIT_FIELD_POLICY:
        raise HandKeptError("field is not a known source field")
    return HandKeptEntry(
        platform=platform,
        key=key,
        value=value,
        as_of=as_of,
        period=period,
        note=note,
        export=export,
        field=chosen,
    )


def to_measure(entry: HandKeptEntry) -> Measure:
    """The measure an entry compiles to: hand-kept, never wider than its source."""
    reg = REGISTRY[entry.key]
    visibility = narrow(reg.visibility, source_level(entry.field))
    method = "Typed by the owner"
    source_note = ""
    if entry.export:
        info = EXPORTS[entry.export]
        visibility = narrow(visibility, info.floor)
        method = f"Imported from the {info.label} export"
        source_note = f"{info.label} export"
    return Measure(
        platform=entry.platform,
        key=entry.key,
        label=reg.label,
        unit=reg.unit,
        value=entry.value,
        as_of=entry.as_of,
        visibility=visibility,
        block="tile",
        trust=Trust(HAND_KEPT, method),
        period=entry.period,
        note=entry.note,
        source_note=source_note,
    )


def stale_entries(
    entries: Iterable[HandKeptEntry],
    today: date,
    limit_days: int = STALE_DAYS,
    limits: Mapping[str, int] | None = None,
) -> list[tuple[HandKeptEntry, int]]:
    """The measures past their stale limit, with their age in whole days. Only the
    newest entry of each platform and key is judged: a newer value replaces an
    older one. Oldest first."""
    newest: dict[tuple[str, str], HandKeptEntry] = {}
    for entry in entries:
        slot = (entry.platform, entry.key)
        held = newest.get(slot)
        if held is None or entry.as_of > held.as_of:
            newest[slot] = entry
    found = []
    for entry in newest.values():
        limit = (limits or {}).get(entry.platform, limit_days)
        days = (today - entry.as_of).days
        if days > limit:
            found.append((entry, days))
    return sorted(found, key=lambda item: (-item[1], item[0].platform, item[0].key))


# --- the file ----------------------------------------------------------------


def read_regular(path: Path, max_bytes: int, what: str) -> bytes:
    """The bytes of a regular file of at most ``max_bytes``; never follows a link,
    never blocks on a pipe."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise HandKeptError(f"cannot open {what}: {exc.strerror}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise HandKeptError(f"{what} is not a regular file")
        if info.st_size > max_bytes:
            raise HandKeptError(f"{what} is larger than {max_bytes} bytes")
        data = os.read(fd, max_bytes + 1)
    except OSError as exc:
        raise HandKeptError(f"cannot read {what}: {exc.strerror}") from None
    finally:
        os.close(fd)
    if len(data) > max_bytes:
        raise HandKeptError(f"{what} is larger than {max_bytes} bytes")
    return data


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise HandKeptError("the file repeats a key")
        out[key] = value
    return out


def _refuse_constant(_name: str) -> Any:
    raise HandKeptError("NaN and Infinity are not allowed")


def _day(value: Any, name: str) -> date:
    if not isinstance(value, str) or not _DAY.match(value):
        raise HandKeptError(f"{name} must be a day written YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HandKeptError(f"{name} must be a real day") from None


def _from_json(raw: Any, today: date) -> HandKeptEntry:
    if not isinstance(raw, dict) or not _REQUIRED <= set(raw) <= _ENTRY_KEYS:
        raise HandKeptError("an entry has missing or unknown keys")
    start, end = raw.get("period_start"), raw.get("period_end")
    if (start is None) != (end is None):
        raise HandKeptError("period_start and period_end go together")
    period = (
        None
        if start is None
        else (_day(start, "period_start"), _day(end, "period_end"))
    )
    export = raw.get("export", "")
    if export and "field" in raw:
        raise HandKeptError("an imported entry cannot name a field")
    return make_entry(
        platform=raw["platform"],
        key=raw["key"],
        value=raw["value"],
        as_of=_day(raw["as_of"], "as_of"),
        period=period,
        note=raw.get("note", ""),
        export=export if isinstance(export, str) else "?",
        field=raw.get("field"),
        today=today,
    )


def _check_unique(entries: Iterable[HandKeptEntry]) -> None:
    seen: set[Any] = set()
    for entry in entries:
        if entry.identity in seen:
            raise HandKeptError("two entries share one platform, key, day and period")
        seen.add(entry.identity)


def load_entries(path: Path, *, today: date | None = None) -> list[HandKeptEntry]:
    """Every entry of ``hand-kept.json``, or ``[]`` when the file does not exist."""
    day = today if today is not None else utc_today()
    if not os.path.lexists(path):
        return []
    data = read_regular(path, MAX_BYTES, "the hand-kept file")
    try:
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_refuse_constant,
        )
    except UnicodeDecodeError:
        raise HandKeptError("the hand-kept file is not valid UTF-8") from None
    except RecursionError:
        raise HandKeptError("the hand-kept file is nested too deeply") from None
    except HandKeptError:
        raise
    except ValueError:
        raise HandKeptError("the hand-kept file is not valid JSON") from None
    if not isinstance(doc, dict) or set(doc) != {"version", "entries"}:
        raise HandKeptError("the file must hold exactly version and entries")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise HandKeptError("version must be 1")
    raw = doc["entries"]
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        raise HandKeptError(f"entries must be a list of at most {MAX_ENTRIES}")
    entries = [_from_json(item, day) for item in raw]
    _check_unique(entries)
    return entries


def _sort_key(entry: HandKeptEntry) -> tuple[Any, ...]:
    period = entry.period or (date.min, date.min)
    return (entry.platform, entry.key, entry.as_of, period)


def _to_json(entry: HandKeptEntry) -> dict[str, Any]:
    out: dict[str, Any] = {
        "platform": entry.platform,
        "key": entry.key,
        "value": entry.value,
        "as_of": entry.as_of.isoformat(),
    }
    if entry.period is not None:
        out["period_start"] = entry.period[0].isoformat()
        out["period_end"] = entry.period[1].isoformat()
    if entry.note:
        out["note"] = entry.note
    if entry.export:
        out["export"] = entry.export
    elif entry.field != DEFAULT_FIELD:
        out["field"] = entry.field
    return out


def write_entries(path: Path, entries: Iterable[HandKeptEntry]) -> None:
    """Write the file: sorted, one rename, never through a link."""
    ordered = sorted(entries, key=_sort_key)
    if len(ordered) > MAX_ENTRIES:
        raise HandKeptError(f"more than {MAX_ENTRIES} entries")
    _check_unique(ordered)
    if path.is_symlink() or path.is_dir():
        raise HandKeptError("the hand-kept file must be a regular file, not a link")
    text = (
        json.dumps(
            {"version": 1, "entries": [_to_json(e) for e in ordered]},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if len(text.encode()) > MAX_BYTES:
        raise HandKeptError(f"the file would be larger than {MAX_BYTES} bytes")
    try:
        fd, tmp = tempfile.mkstemp(prefix=".hand-kept.", dir=path.parent)
    except OSError as exc:
        raise HandKeptError(f"cannot write the file: {exc.strerror}") from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError as exc:
        Path(tmp).unlink(missing_ok=True)
        raise HandKeptError(f"cannot write the file: {exc.strerror}") from None
