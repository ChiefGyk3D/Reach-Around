# SPDX-License-Identifier: Apache-2.0
"""Import a YouTube analytics export as hand-kept measures (kit v2, slice 6, #631).

This is the pattern for the importers that follow: one small parser per platform
export, strict about what it accepts, bounded in size, offline, and producing
``HandKeptEntry`` values that name the export. The measures are private (see
``EXPORTS`` in ``handkept``); an importer cannot widen that.

Input: the CSV a creator downloads from YouTube Studio's analytics ("Chart
data"): a header row naming exactly ``Date``, ``Views`` and ``Watch time
(hours)``, then one row per UTC day (``YYYY-MM-DD``). YouTube may add a first row
whose date is ``Total``; it is skipped (a repeated one is refused), never added
in. The column names and the row shape are ASSUMED from a sample export, not
read from a vendor page (see docs/youtube-analytics.md). Anything else is
refused.

Output: two entries for the whole export, ``views`` and ``watch_hours``, each the
sum of the daily rows. Watch time is added in exact decimal arithmetic and
rounded half up to whole hours. The period is the first to the last day found;
``as_of`` is the last day.

Privacy: the shared import guard (``import_guard``, #784) refuses a header outside
the three allowed columns or naming a person, and a cell shaped like an email, an
@handle, an IP address or a channel id, with a fixed error that never echoes it.

Limits: at most ``MAX_BYTES`` bytes, ``MAX_ROWS`` rows, ``MAX_CELL`` characters
per cell. No day after today. A whole export is refused for any bad row; nothing
is half read. Errors name the row, never print a cell.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from reach_around.config.cutover import utc_today
from reach_around.config.handkept import (
    HandKeptEntry,
    HandKeptError,
    make_entry,
    read_regular,
)
from reach_around.config.import_guard import (
    ImportGuardError,
    check_names,
    check_value,
)

EXPORT = "youtube-analytics"
MAX_BYTES = 256 * 1024
MAX_ROWS = 1100
MAX_CELL = 32
DATE_COLUMN = "Date"
VIEWS_COLUMN = "Views"
HOURS_COLUMN = "Watch time (hours)"
_COLUMNS = frozenset({DATE_COLUMN, VIEWS_COLUMN, HOURS_COLUMN})

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WHOLE = re.compile(r"^\d{1,12}$")
_HOURS = re.compile(r"^\d{1,9}(\.\d{1,4})?$")


def _text(data: bytes) -> str:
    if b"\x00" in data:
        raise HandKeptError("the export holds a NUL byte")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HandKeptError("the export is not valid UTF-8") from None


def import_youtube(path: Path, *, today: date | None = None) -> list[HandKeptEntry]:
    """The hand-kept entries of one YouTube analytics export file."""
    day = today if today is not None else utc_today()
    text = _text(read_regular(path, MAX_BYTES, "the export"))
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None:
            raise HandKeptError("the export is empty")
        try:
            check_names(header, _COLUMNS)
        except ImportGuardError as refused:
            raise HandKeptError(str(refused)) from None
        if len(header) != len(set(header)) or set(header) != _COLUMNS:
            raise HandKeptError(
                "the header must name exactly Date, Views and Watch time (hours)"
            )
        where = {name: header.index(name) for name in _COLUMNS}
        views = 0
        hours = Decimal(0)
        days: set[date] = set()
        saw_total = False
        for number, row in enumerate(reader, start=2):
            if number - 1 > MAX_ROWS:
                raise HandKeptError(f"the export has more than {MAX_ROWS} rows")
            if len(row) != len(header):
                raise HandKeptError(f"row {number} has the wrong number of cells")
            if any(len(cell) > MAX_CELL for cell in row):
                raise HandKeptError(f"row {number} has a cell that is too long")
            try:
                for cell in row:
                    check_value(cell)
            except ImportGuardError as refused:
                raise HandKeptError(f"row {number}: {refused}") from None
            stamp = row[where[DATE_COLUMN]]
            if stamp == "Total":
                if saw_total:
                    raise HandKeptError("the export has more than one Total row")
                saw_total = True
                continue
            if not _DAY.match(stamp):
                raise HandKeptError(f"row {number}: Date must be written YYYY-MM-DD")
            try:
                when = date.fromisoformat(stamp)
            except ValueError:
                raise HandKeptError(f"row {number}: Date is not a real day") from None
            if when > day:
                raise HandKeptError(f"row {number}: Date is after today (UTC)")
            if when in days:
                raise HandKeptError(f"row {number}: Date repeats an earlier row")
            count, watch = row[where[VIEWS_COLUMN]], row[where[HOURS_COLUMN]]
            if not _WHOLE.match(count):
                raise HandKeptError(f"row {number}: Views must be a whole number")
            if not _HOURS.match(watch):
                raise HandKeptError(
                    f"row {number}: Watch time (hours) must be a plain decimal"
                )
            days.add(when)
            views += int(count)
            hours += Decimal(watch)
    except csv.Error:
        raise HandKeptError("the export is not valid CSV") from None
    if not days:
        raise HandKeptError("the export has no daily rows")
    period = (min(days), max(days))
    whole_hours = int(hours.quantize(Decimal(1), ROUND_HALF_UP))
    return [
        make_entry(
            platform="youtube",
            key=key,
            value=value,
            as_of=period[1],
            period=period,
            export=EXPORT,
            today=day,
        )
        for key, value in (("views", views), ("watch_hours", whole_hours))
    ]
