# SPDX-License-Identifier: Apache-2.0
"""The optional imported-history file: link-page numbers from the previous provider.

The kit can show a separate block "Before Reach Around" (#377). Its numbers come
from this one owner-supplied file, chosen by ``kit.imported_history``. It is not
the social history file: that file holds private analytics and tombstones and
the site build never reads it. This file holds only aggregate views and clicks
per period (made by ``counts history-export-imported``), so nothing per visitor,
per link, per referrer or per day can reach a page.

Shape::

    {"version": 1, "source": "imported", "note": NOTE, "platform": PLATFORM,
     "periods": [{"start": "2026-01-01", "end": "2026-01-31", "days": 31,
                  "views": 1234, "clicks": 56}]}

``source`` and ``note`` repeat the provenance vocabulary of the importer
(NOTE, PLATFORM and the others are its constants in counts/beacons.py)
(``source: imported``, ``note: Beacons``); neither is printed. ``days`` is how
many days of the period the export holds, so a partial period says so. The
numbers are never added to a collected number or to the headline total.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from reach_around.config.era import SPANS, classify_window, era_text
from reach_around.config.loader import ConfigError, _read
from reach_around.counts.beacons import IMPORT_NOTE, IMPORT_PLATFORM, IMPORT_SOURCE

MAX_IMPORTED_BYTES = 256 * 1024
MAX_PERIODS = 120
MAX_VALUE = 10**12
PLATFORM = IMPORT_PLATFORM
SOURCE = IMPORT_SOURCE

IMPORTED_HEADING = "Before Reach Around"
# One neutral sentence; the owner approves the wording (see the pull request).
IMPORTED_NOTE = (
    "Link-page views and clicks imported from the previous provider; they are not "
    "comparable to numbers collected by Reach Around and are not added to any total."
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImportedPeriod(_Model):
    start: date
    end: date
    days: StrictInt = Field(ge=1, le=3660)
    views: StrictInt = Field(ge=0, le=MAX_VALUE)
    clicks: StrictInt = Field(ge=0, le=MAX_VALUE)

    @model_validator(mode="after")
    def _ordered(self) -> ImportedPeriod:
        if self.end < self.start:
            raise ValueError("end is before start")
        if self.days > (self.end - self.start).days + 1:
            raise ValueError("days is more than the period holds")
        return self

    @property
    def length(self) -> int:
        return (self.end - self.start).days + 1


class ImportedHistory(_Model):
    version: StrictInt
    source: StrictStr
    note: StrictStr
    platform: StrictStr
    periods: tuple[ImportedPeriod, ...] = Field(min_length=1, max_length=MAX_PERIODS)

    @field_validator("version")
    @classmethod
    def _version(cls, value: int) -> int:
        if value != 1:
            raise ValueError("version must be 1")
        return value

    @field_validator("source")
    @classmethod
    def _source(cls, value: str) -> str:
        if value != SOURCE:
            raise ValueError(f"source must be {SOURCE!r}")
        return value

    @field_validator("platform")
    @classmethod
    def _platform(cls, value: str) -> str:
        if value != PLATFORM:
            raise ValueError(f"platform must be {PLATFORM!r}")
        return value

    @field_validator("note")
    @classmethod
    def _note(cls, value: str) -> str:
        if value != IMPORT_NOTE:
            raise ValueError("note is not the importer's")
        return value

    @model_validator(mode="after")
    def _no_overlap(self) -> ImportedHistory:
        last: date | None = None
        for period in self.periods:
            if last is not None and period.start <= last:
                raise ValueError("periods must be in order and must not overlap")
            last = period.end
        return self


class _Bad(Exception):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _Bad("duplicate key in the file")
        out[key] = value
    return out


def _refuse_constant(name: str) -> Any:
    raise _Bad("a non-finite number is not allowed")


def parse_imported(data: bytes, *, source: str = "imported history") -> ImportedHistory:
    """Validate imported-history bytes; every failure is a ``ConfigError``.

    Messages never repeat a value from the file."""
    try:
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_refuse_constant,
        )
        if not isinstance(doc, dict):
            raise _Bad("must be a JSON object")
        return ImportedHistory.model_validate(doc)
    except UnicodeDecodeError:
        raise ConfigError(source, None, "file is not valid UTF-8") from None
    except RecursionError:
        raise ConfigError(source, None, "nesting is too deep") from None
    except _Bad as exc:
        raise ConfigError(source, None, str(exc)) from None
    except ValidationError:
        # Fixed words only: a key name or value from the file is never echoed.
        raise ConfigError(source, None, "not an imported-history file") from None
    except ValueError:
        raise ConfigError(source, None, "not valid JSON") from None


def load_imported(path: Path) -> ImportedHistory:
    """Read and validate ``path``; every failure is a ``ConfigError``."""
    return parse_imported(_read(path, MAX_IMPORTED_BYTES), source=str(path))


def imported_source(config: Any, config_path: Path) -> Path | None:
    """The file ``kit.imported_history`` names, beside the config, or ``None``.

    The path stays inside the config's folder, is not a symbolic link, and may
    not be the social history file (``numbers.history_file``): the build never
    reads that one."""
    kit = config.kit
    if kit is None or kit.imported_history is None:
        return None
    root = config_path.resolve(strict=True).parent
    named = Path(kit.imported_history)
    try:
        parent = (root / named).parent.resolve(strict=True)
    except OSError:
        raise ConfigError(
            str(config_path), None, "kit.imported_history: its folder does not exist"
        ) from None
    if not parent.is_relative_to(root):
        raise ConfigError(
            str(config_path), None, "kit.imported_history leaves the config's folder"
        )
    target = parent / named.name
    numbers = config.numbers
    if numbers is not None and numbers.history_file is not None:
        history = (root / numbers.history_file).parent.resolve() / Path(
            numbers.history_file
        ).name
        if target == history or (
            target.exists() and history.exists() and target.samefile(history)
        ):
            raise ConfigError(
                str(config_path),
                None,
                "kit.imported_history must not be numbers.history_file: the site "
                "build never reads the social history file",
            )
    return target


def display_rows(
    history: ImportedHistory | None, live_day: date | None
) -> list[dict[str, Any]]:
    """The rows the block prints, in order. With the cutover in force a period
    that is not wholly before it is left out (the collected numbers cover it)."""
    if history is None:
        return []
    rows: list[dict[str, Any]] = []
    for period in history.periods:
        era = classify_window(period.start, period.end, live_day)
        if era == SPANS or era == "after":
            continue
        rows.append(
            {
                "label": period.start.strftime("%Y-%m")
                if period.start.day == 1
                and period.end.month == period.start.month
                and period.end.year == period.start.year
                else f"{period.start.isoformat()} to {period.end.isoformat()}",
                "start": period.start.isoformat(),
                "views": f"{period.views:,}",
                "clicks": f"{period.clicks:,}",
                "partial": (
                    f"{period.days} of {period.length} days"
                    if period.days < period.length
                    else ""
                ),
                "era": era_text(era),
            }
        )
    return rows


def imported_strings(history: ImportedHistory | None) -> list[tuple[str, str]]:
    """Leak canaries: every printed number, with its label, both ways round, as
    plain and as comma-grouped digits."""
    found: list[tuple[str, str]] = []
    if history is None:
        return found
    for index, period in enumerate(history.periods):
        for field, value in (("views", period.views), ("clicks", period.clicks)):
            for rendered in (str(value), f"{value:,}"):
                where = f"kit.imported_history[{index}].{field}"
                found.append((where, f"{rendered} {field}"))
                found.append((where, f"{field} {rendered}"))
    return found


def expected_text(
    history: ImportedHistory | None, live_day: date | None
) -> list[tuple[str, str]]:
    """What a page that prints the block must contain (for the PDF text check)."""
    rows = display_rows(history, live_day)
    if not rows:
        return []
    found = [("the imported history heading", IMPORTED_HEADING)]
    found.append(("the imported history note", IMPORTED_NOTE))
    for row in rows:
        where = f"the imported history row {row['label']!r}"
        found.append((where, f"{row['views']} views"))
        found.append((where, f"{row['clicks']} clicks"))
    return found
