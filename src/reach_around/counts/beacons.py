# SPDX-License-Identifier: Apache-2.0
"""Strict parser for a Beacons Link in Bio analytics CSV export."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_BEACONS_BYTES = 5 * 1024 * 1024
MAX_BEACONS_ROWS = 100_000

_HEADER = ("Section", "Item", "Detail", "Views", "Clicks", "CTR (%)")
_SECTIONS = frozenset(
    {"Total", "Daily", "Links", "Social icons", "Sources", "Devices", "Countries"}
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_INTEGER = re.compile(r"^(0|[1-9][0-9]*)$")


class BeaconsImportError(Exception):
    """A Beacons export is malformed. Messages never repeat field values."""


@dataclass(frozen=True)
class BeaconsDay:
    day: date
    views: int
    clicks: int


@dataclass(frozen=True)
class BeaconsExport:
    row_count: int
    days: tuple[BeaconsDay, ...]


def _fail(row: int | None, message: str) -> BeaconsImportError:
    where = "header" if row is None else f"row {row}"
    return BeaconsImportError(f"{where}: {message}")


def _text(value: str, row: int, field: str, *, empty: bool = False) -> None:
    if not empty and not value:
        raise _fail(row, f"{field} must not be empty")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise _fail(row, f"{field} contains a control character")
    if value.startswith(_FORMULA_PREFIXES):
        raise _fail(row, f"{field} has a formula-injection prefix")


def _empty(value: str, row: int, field: str) -> None:
    if value:
        raise _fail(row, f"{field} must be empty for this section")


def _count(value: str, row: int, field: str) -> int:
    if not _INTEGER.fullmatch(value):
        raise _fail(row, f"{field} must be a non-negative whole number")
    return int(value)


def _ctr(value: str, row: int) -> None:
    try:
        rate = Decimal(value)
    except InvalidOperation:
        raise _fail(row, "CTR must be a non-negative finite number") from None
    if not rate.is_finite() or rate < 0:
        raise _fail(row, "CTR must be a non-negative finite number")


def _parse_day(value: str, row: int) -> date:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise _fail(row, "Daily Item must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _fail(row, "Daily Item must be a real date") from None


def _validate_non_daily(row: int, values: tuple[str, ...]) -> None:
    section, item, detail, views, clicks, ctr = values
    _text(item, row, "Item")
    _text(detail, row, "Detail", empty=True)
    if section == "Total":
        if item != "Link in Bio":
            raise _fail(row, "Total Item must be Link in Bio")
        _empty(detail, row, "Detail")
        _count(views, row, "Views")
        _count(clicks, row, "Clicks")
        _ctr(ctr, row)
    elif section in ("Links", "Social icons"):
        _text(detail, row, "Detail")
        _empty(views, row, "Views")
        _count(clicks, row, "Clicks")
        _empty(ctr, row, "CTR")
    else:
        _empty(detail, row, "Detail")
        _count(views, row, "Views")
        _count(clicks, row, "Clicks")
        _ctr(ctr, row)


def parse_beacons_csv(data: bytes) -> BeaconsExport:
    """Parse exactly the verified Beacons Link in Bio analytics CSV shape."""
    if len(data) > MAX_BEACONS_BYTES:
        raise BeaconsImportError(f"file is too large (limit {MAX_BEACONS_BYTES} bytes)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise BeaconsImportError("file is not valid UTF-8") from None
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise BeaconsImportError("file is not strict CSV") from None
    if not rows or tuple(rows[0]) != _HEADER:
        raise _fail(None, "expected Section,Item,Detail,Views,Clicks,CTR (%)")
    data_rows = rows[1:]
    if len(data_rows) > MAX_BEACONS_ROWS:
        raise BeaconsImportError(
            f"file has too many data rows (limit {MAX_BEACONS_ROWS})"
        )
    if not data_rows:
        raise BeaconsImportError("file has no data rows")

    total_count = 0
    days: list[BeaconsDay] = []
    seen_days: set[date] = set()
    for number, cells in enumerate(data_rows, start=2):
        if len(cells) != len(_HEADER):
            raise _fail(number, "wrong number of columns")
        values = tuple(cells)
        section, item, detail, views, clicks, ctr = values
        if section not in _SECTIONS:
            raise _fail(number, "unknown section")
        _text(section, number, "Section")
        if section == "Total":
            total_count += 1
        if section == "Daily":
            _text(item, number, "Item")
            _empty(detail, number, "Detail")
            day = _parse_day(item, number)
            if day in seen_days:
                raise _fail(number, "duplicate day")
            seen_days.add(day)
            days.append(
                BeaconsDay(
                    day=day,
                    views=_count(views, number, "Views"),
                    clicks=_count(clicks, number, "Clicks"),
                )
            )
            _empty(ctr, number, "CTR")
        else:
            _validate_non_daily(number, values)
    if total_count != 1:
        raise BeaconsImportError("file must have exactly one Total row")
    if not days:
        raise BeaconsImportError("file must have at least one Daily row")
    return BeaconsExport(len(data_rows), tuple(days))
