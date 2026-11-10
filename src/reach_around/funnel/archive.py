# SPDX-License-Identifier: Apache-2.0
"""The long-term analytics archive: 25 months of floored monthly rollups (#399).

Owner decision (2026-11-08): keep the last 25 closed UTC months of the click
funnel's monthly rollups on the owner's machine, so a before and after
comparison a year past the cutover is still possible. Day cells stay at 90 days
and raw rows keep their short retention; nothing finer than a month is archived.

What goes in: per site and closed month, the source by link and the country by
link cells, run through the report's own suppression (``report._fold``) BEFORE
they are written, so the archive holds only what the report may print: cells at
the floor, one ``(below the floor)`` remainder per link, a link that cannot be
made safe left out. Counting-capacity trouble is kept as one coarse flag, never
as the day or click numbers behind it. No timestamp, address, user agent or
other visitor field exists in the record.

Storage: one small JSON file per site and month, ``<site>-<YYYY-MM>.json``, in a
directory inside the private instance repo. Append-only in the sense that
matters: a month file is created once, never rewritten. The same data again is a
no-op; different data for an archived month is refused (two versions of one
month could be differenced, and a quiet correction would hide that). The only
deletion is the retention rule: a file for a month older than the 25 closed
months is removed, and nothing that does not match the file name pattern is ever
touched. The git history of the instance repo still holds a removed file; see
docs/report.md for what that means.

The owner is a trusted operator on his own machine. These checks stop accidents
and a corrupted or hand-edited file from reaching Grafana; they do not stop an
owner who edits his own files.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from reach_around.funnel.export import Export
from reach_around.funnel.report import FLOOR, REMAINDER, _fold

ARCHIVE_VERSION = 1
KEEP_MONTHS = 25
MAX_FILE_BYTES = 1024 * 1024
MAX_FILES = 200

_SITE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
_MONTH = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
_NAME = re.compile(r"([a-z0-9][a-z0-9-]{0,19})-(\d{4}-(?:0[1-9]|1[0-2]))\.json")
_LINK = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_LABEL = re.compile(r"[a-z0-9.() -]{1,80}")
_COUNTRY = re.compile(r"[A-Z]{2}|other|\(below the floor\)")
_KEYS = {"version", "site", "month", "floor", "capacity_ok", "sources", "countries"}
_ROW_KEYS = {"link_id", "label", "clicks"}
MAX_CLICKS = 10**12


class ArchiveError(ValueError):
    """The archive cannot be written or read. Never quotes archived content."""


@dataclass
class WriteResult:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def _index(month: str) -> int:
    return int(month[:4]) * 12 + int(month[5:]) - 1


def window(today: date) -> tuple[int, int]:
    """First and last month index kept: the 25 closed UTC months before ``today``."""
    current = today.year * 12 + today.month - 1
    return current - KEEP_MONTHS, current - 1


def _floor_by_link(
    rows: list[dict[str, Any]], label_key: str, floor: int
) -> list[dict[str, Any]]:
    by_link: dict[str, dict[str, int]] = {}
    for r in rows:
        by_link.setdefault(r["link_id"], {})
        cell = by_link[r["link_id"]]
        cell[r[label_key]] = cell.get(r[label_key], 0) + r["clicks"]
    out: list[dict[str, Any]] = []
    for link in sorted(by_link):
        result = _fold(by_link[link], floor)
        if result.dropped or (not result.shown and not result.other):
            continue
        out += [
            {"link_id": link, "label": label, "clicks": n} for label, n in result.shown
        ]
        if result.other:
            out.append({"link_id": link, "label": REMAINDER, "clicks": result.other})
    return sorted(out, key=lambda r: (r["link_id"], r["label"]))


def build_records(
    export: Export, *, site: str, floor: int = FLOOR, today: date
) -> list[dict[str, Any]]:
    """One floored record per closed month in the window that is marked rolled up."""
    if floor < FLOOR:
        raise ValueError(f"the floor cannot be below {FLOOR}")
    if not _SITE.fullmatch(site):
        raise ValueError("the site must be a short lowercase slug")
    first, last = window(today)
    logged = {r["month"]: r for r in export.click_month_log if r["site"] == site}
    records: list[dict[str, Any]] = []
    for month in sorted(logged):
        if not first <= _index(month) <= last:
            continue
        src = [
            r
            for r in export.click_src_monthly
            if r["site"] == site and r["month"] == month
        ]
        cty = [
            r
            for r in export.click_country_monthly
            if r["site"] == site and r["month"] == month
        ]
        records.append(
            {
                "version": ARCHIVE_VERSION,
                "site": site,
                "month": month,
                "floor": floor,
                "capacity_ok": logged[month]["exhausted_days"] == 0,
                "sources": _floor_by_link(src, "src", floor),
                "countries": _floor_by_link(cty, "country", floor),
            }
        )
    return records


def _canonical(record: dict[str, Any]) -> bytes:
    return (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _open_dir(path: Path) -> int:
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ArchiveError(
            "the archive directory does not exist or is a symbolic link"
        ) from None


def _read_file(dir_fd: int, name: str) -> bytes:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
            raise ArchiveError("an archive file is not a regular file of sane size")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        raise ArchiveError("an archive file cannot be read safely") from None
    try:
        return os.read(fd, MAX_FILE_BYTES + 1)
    finally:
        os.close(fd)


def _month_files(dir_fd: int) -> list[tuple[str, str, str]]:
    found = []
    for name in sorted(os.listdir(dir_fd)):
        m = _NAME.fullmatch(name)
        if m:
            found.append((name, m.group(1), m.group(2)))
    if len(found) > MAX_FILES:
        raise ArchiveError("the archive directory holds too many month files")
    return found


def write_records(
    directory: Path, records: list[dict[str, Any]], *, today: date
) -> WriteResult:
    """Create each month file once, then drop month files past the retention."""
    result = WriteResult()
    first, last = window(today)
    dir_fd = _open_dir(directory)
    try:
        for rec in sorted(records, key=lambda r: r["month"]):
            if not first <= _index(rec["month"]) <= last:
                continue
            name = f"{rec['site']}-{rec['month']}.json"
            payload = _canonical(rec)
            try:
                os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                _create(dir_fd, name, payload)
                result.written.append(rec["month"])
                continue
            if _read_file(dir_fd, name) != payload:
                raise ArchiveError(
                    f"the month {rec['month']} is already archived with different "
                    "data; an archived month is never changed"
                )
            result.unchanged.append(rec["month"])
        for name, _site, month in _month_files(dir_fd):
            if _index(month) < first:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                if stat.S_ISREG(st.st_mode):
                    os.unlink(name, dir_fd=dir_fd)
                    result.dropped.append(month)
    finally:
        os.close(dir_fd)
    return result


def _create(dir_fd: int, name: str, payload: bytes) -> None:
    """Write a temp file, then link it into place; fails if the name appeared."""
    tmp = f".archive-{os.getpid()}-{os.urandom(4).hex()}.tmp"
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=dir_fd
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(
                tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd, follow_symlinks=False
            )
        except FileExistsError:
            raise ArchiveError(
                "a month file appeared while writing; run again"
            ) from None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dir_fd)


def _no_dupes(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ArchiveError("an archive file repeats a key")
        out[key] = value
    return out


def _no_const(_: str) -> Any:
    raise ArchiveError("an archive file holds a non-finite number")


def _check_rows(rows: Any, floor: int, label_re: re.Pattern[str]) -> None:
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ArchiveError("an archive file has a bad table")
    for r in rows:
        if (
            not isinstance(r, dict)
            or set(r) != _ROW_KEYS
            or not isinstance(r["link_id"], str)
            or not _LINK.fullmatch(r["link_id"])
            or not isinstance(r["label"], str)
            or not label_re.fullmatch(r["label"])
            or type(r["clicks"]) is not int
            or not floor <= r["clicks"] <= MAX_CLICKS
        ):
            raise ArchiveError("an archive file has a row that is not allowed")


def load_archive(directory: Path, *, site: str, today: date) -> list[dict[str, Any]]:
    """Read and validate the site's month files inside the window, oldest first."""
    first, last = window(today)
    dir_fd = _open_dir(directory)
    try:
        out: list[dict[str, Any]] = []
        for name, file_site, month in _month_files(dir_fd):
            if file_site != site or not first <= _index(month) <= last:
                continue
            try:
                rec = json.loads(
                    _read_file(dir_fd, name),
                    object_pairs_hook=_no_dupes,
                    parse_constant=_no_const,
                )
            except (ValueError, RecursionError) as exc:
                if isinstance(exc, ArchiveError):
                    raise
                raise ArchiveError("an archive file is not valid JSON") from None
            if (
                not isinstance(rec, dict)
                or set(rec) != _KEYS
                or rec["version"] != ARCHIVE_VERSION
                or rec["site"] != site
                or rec["month"] != month
                or type(rec["floor"]) is not int
                or rec["floor"] < FLOOR
                or type(rec["capacity_ok"]) is not bool
            ):
                raise ArchiveError("an archive file does not match its name or schema")
            _check_rows(rec["sources"], rec["floor"], _LABEL)
            _check_rows(rec["countries"], rec["floor"], _COUNTRY)
            out.append(rec)
        return out
    finally:
        os.close(dir_fd)
