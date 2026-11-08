# SPDX-License-Identifier: Apache-2.0
"""Batch import of hand-kept numbers from a CSV file.

Columns, in this order, header included: ``platform,metric,value,as_of``.

* ``platform`` is a platform slug or a platform item id from the config.
* ``metric`` is ``followers``, ``subscribers`` or ``members`` and must be the
  label the config gives that platform (a Kick subscriber count is never filed
  as followers).
* ``value`` is a whole number written with digits only.
* ``as_of`` is a UTC day, ``YYYY-MM-DD``, not in the future.

The file is parsed whole before anything is written: one bad row refuses the
file. Errors name the row and the column and never repeat a value, so a cell
that starts with ``=``, ``+``, ``-`` or ``@`` is not echoed anywhere. A number a
configured fetch source writes is refused (refresh it instead). The result goes
to the numbers file through the same writer ``counts refresh`` uses.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, get_args

from reach_around.config.blocks import MAX_COUNT, CountLabel, Platforms
from reach_around.config.numbers import NumbersEntry, source_platforms

MAX_CSV_BYTES = 64 * 1024
MAX_ROWS = 200
MAX_ERRORS = 20
COLUMNS = ("platform", "metric", "value", "as_of")
_EARLIEST = date(2000, 1, 1)
_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_VALUE = re.compile(r"0|[1-9][0-9]{0,10}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_METRICS = frozenset(get_args(CountLabel))


class ImportFailed(Exception):
    """The file is refused; ``problems`` are sentences with no cell values."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ImportedRow:
    key: str
    metric: str
    count: int
    as_of: date


def _labels_by_key(config: Any) -> dict[str, set[str]]:
    """Each key an import may write, with the labels its items use."""
    out: dict[str, set[str]] = {}
    for tab in config.tabs:
        for block in tab.blocks:
            if isinstance(block, Platforms):
                for ident, item in zip(block.item_ids, block.items, strict=True):
                    out.setdefault(ident, set()).add(item.label)
                    out.setdefault(item.platform, set()).add(item.label)
    kit = getattr(config, "kit", None)
    if kit is not None:
        for metric in kit.metrics:
            if metric.source == "counts":
                out.setdefault(metric.platform, set()).update(_METRICS)
    return out


def _decode(data: bytes) -> str:
    if len(data) > MAX_CSV_BYTES:
        raise ImportFailed([f"the file is too large (limit {MAX_CSV_BYTES} bytes)"])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ImportFailed(["the file is not valid UTF-8"]) from None
    for ch in text:
        if ch in "\r\n":
            continue
        if unicodedata.category(ch)[0] in "CZ" and ch != " ":
            raise ImportFailed(["the file holds a control or hidden character"])
    return text


def parse_import(
    data: bytes,
    config: Any,
    previous: Mapping[str, NumbersEntry],
    today: date,
) -> list[ImportedRow]:
    """Validate the CSV bytes against the config; raise ``ImportFailed``."""
    text = _decode(data)
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise ImportFailed(["the file is not well-formed CSV"]) from None
    if not rows or tuple(rows[0]) != COLUMNS:
        raise ImportFailed([f"the first line must be exactly: {','.join(COLUMNS)}"])
    body = rows[1:]
    if not body:
        raise ImportFailed(["the file has no rows"])
    if len(body) > MAX_ROWS:
        raise ImportFailed([f"too many rows (limit {MAX_ROWS})"])
    labels = _labels_by_key(config)
    fetched = source_platforms(config)
    problems: list[str] = []
    found: list[ImportedRow] = []
    seen: set[str] = set()

    def bad(line: int, column: str, why: str) -> None:
        problems.append(f"row {line}, column {column}: {why}")

    for number, row in enumerate(body, start=2):
        if len(row) != len(COLUMNS):
            problems.append(f"row {number}: needs exactly {len(COLUMNS)} columns")
            continue
        platform, metric, value, day = row
        before = len(problems)
        if _KEY.fullmatch(platform) is None or platform not in labels:
            bad(number, "platform", "not a platform or item id in the config")
        elif platform in fetched:
            bad(number, "platform", "a fetch source writes this number; refresh it")
        elif platform in seen:
            bad(number, "platform", "listed more than once")
        if metric not in _METRICS:
            bad(number, "metric", f"must be one of {', '.join(sorted(_METRICS))}")
        elif len(problems) == before and metric not in labels[platform]:
            bad(number, "metric", "does not match the label the config gives it")
        count = 0
        if _VALUE.fullmatch(value) is None or int(value) > MAX_COUNT:
            bad(number, "value", f"must be a whole number from 0 to {MAX_COUNT}")
        else:
            count = int(value)
        stamp = _day(day)
        if stamp is None:
            bad(number, "as_of", "must be a real UTC day written YYYY-MM-DD")
        elif stamp < _EARLIEST:
            bad(number, "as_of", "must be 2000-01-01 or later")
        elif stamp > today:
            bad(number, "as_of", "is in the future")
        elif platform in previous and stamp < previous[platform].as_of_date:
            bad(number, "as_of", "is older than the number already on file")
        if len(problems) == before and stamp is not None:
            seen.add(platform)
            found.append(ImportedRow(platform, metric, count, stamp))
    if problems:
        shown = problems[:MAX_ERRORS]
        if len(problems) > MAX_ERRORS:
            shown.append(f"{len(problems) - MAX_ERRORS} more problems not shown")
        raise ImportFailed(shown)
    return found


def _day(text: str) -> date | None:
    if _DAY.fullmatch(text) is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def merge(
    previous: Mapping[str, NumbersEntry], rows: list[ImportedRow]
) -> dict[str, NumbersEntry]:
    """``previous`` with each imported row replacing its key; dates are UTC
    midnight."""
    merged = dict(previous)
    for row in rows:
        merged[row.key] = NumbersEntry(
            count=row.count, as_of=datetime.combine(row.as_of, time(0), UTC)
        )
    return merged
