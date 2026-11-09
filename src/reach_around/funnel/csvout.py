# SPDX-License-Identifier: Apache-2.0
"""CSV output for ``reach-around report --csv DIR``.

One file per section, written from the already floored report dictionary, so
a suppressed cell stays suppressed and no file can hold more than the text and
JSON output do. There is no per-visitor row: every row is an aggregate label
and a count.

Format: UTF-8 without a byte order mark, RFC 4180 (CRLF line ends, a field
holding a comma, a quote or a line break is quoted, a quote is doubled), a
header row, fixed column order. Metadata (``generated_at_utc``, the window,
the floor) goes into a separate ``metadata.csv`` so each section file stays a
clean table that a spreadsheet or a parser reads without special cases.

Formula injection: a text cell whose first character is ``=``, ``+``, ``-``,
``@``, a tab, a carriage return or a line feed is written with a leading
apostrophe, so a spreadsheet shows it as text and does not run it. Counts are
integers and ratios are non-negative numbers, written as numbers.
"""

from __future__ import annotations

import csv
import io
import os
import stat
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reach_around.config.zones import format_instant

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r", "\n")
BELOW = "(rows below the floor)"


def safe_cell(value: Any) -> Any:
    """Neutralize a text cell that a spreadsheet could read as a formula."""
    if isinstance(value, str) and value.startswith(_DANGEROUS):
        return "'" + value
    if value is None:
        return ""
    return value


def render_csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([safe_cell(h) for h in header])
    for row in rows:
        writer.writerow([safe_cell(cell) for cell in row])
    return buf.getvalue().encode("utf-8")


def _table_rows(section: dict[str, Any]) -> list[list[Any]]:
    state = section["state"]
    if state in ("not_in_export", "withheld"):
        return [["", "", state]]
    rows: list[list[Any]] = [[r["label"], r["views"], "row"] for r in section["rows"]]
    if section["other_below_floor"]:
        rows.append([BELOW, section["other_below_floor"], "below_floor"])
    return rows


def _pair_rows(section: dict[str, Any], key: str) -> list[list[Any]]:
    """Cells of a label by destination section, whole destinations only."""
    state = section["state"]
    if state in ("not_in_export", "withheld", "period_required"):
        return [["", "", "", state]]
    return [[c[key], c["destination"], c["clicks"], "cell"] for c in section["cells"]]


def _flow_rows(section: dict[str, Any]) -> list[list[Any]]:
    state = section["state"]
    if state in ("not_in_export", "withheld", "period_required"):
        return [["", "", "", state]]
    rows: list[list[Any]] = [
        [r["label"], r["clicks"], r["share"], "row"] for r in section["ranked"]
    ]
    if section["ranked_other"]:
        rows.append(["other", section["ranked_other"], "", "other"])
    return rows


def _funnel_rows(report: dict[str, Any]) -> list[list[Any]]:
    funnel = report["funnel"]
    steps = [
        ("page_views", "view_daily"),
        ("clicks", "click_events"),
        ("kit_views", "kit_events"),
        ("kit_unlocks", "kit_events"),
    ]
    rows: list[list[Any]] = []
    for key, table in steps:
        if table in funnel["missing"]:
            rows.append([key, "", "not_in_export"])
        elif key in funnel["not_scoped"]:
            rows.append([key, "", "not_available"])
        elif funnel[key] is None:
            rows.append([key, "", "below_floor"])
        else:
            rows.append([key, funnel[key], "ok"])
    for key in ("clicks_per_100_views", "kit_views_per_100_views"):
        value = funnel[key]
        rows.append(
            [key, "" if value is None else value, "ok" if value is not None else "n/a"]
        )
    return rows


def _bot_rows(report: dict[str, Any]) -> list[list[Any]]:
    bots = report["bots"]
    if "view_daily" in report["funnel"]["missing"]:
        return [["", "", "", "", "not_in_export"]]
    rows: list[list[Any]] = [
        [c["class"], c["name"], c["views"], c["share"], "row"] for c in bots["classes"]
    ]
    if bots["other_below_floor"]:
        rows.append(["", BELOW, bots["other_below_floor"], "", "below_floor"])
    if not rows:
        rows.append(["", "", "", "", "withheld"])
    return rows


def _display_rows(report: dict[str, Any], generated_at: datetime) -> list[list[Any]]:
    """Zone and era rows; present only when a zone or a cutover was given."""
    rows: list[list[Any]] = []
    display = report.get("display")
    if display:
        rows.append(["display_timezone", display["zone"]])
        rows.append(
            ["generated_at_display", format_instant(generated_at, display["zone"])]
        )
        rows.append(["window_start_display", display["window_start"] or ""])
        rows.append(
            ["window_end_exclusive_display", display["window_end_exclusive"] or ""]
        )
    era = report.get("era")
    if era:
        rows.append(["cutover_utc", era["cutover_utc"]])
        rows.append(["window_era", era["window"]])
    return rows


def _meta_rows(
    report: dict[str, Any], generated_at: datetime, requested: dict[str, Any]
) -> list[list[Any]]:
    period = report["period"]
    stamp = generated_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [
        ["generated_at_utc", stamp],
        ["window_since_utc", period["since"] or ""],
        ["window_until_utc", period["until"] or ""],
        ["days_with_data", period["days_with_data"]],
        ["requested_since_utc", requested.get("since") or ""],
        ["requested_until_utc", requested.get("until") or ""],
        ["requested_days", requested.get("days") or ""],
        ["period", report["release"]["period"] or ""],
        ["period_start_utc", report["release"]["start"] or ""],
        ["period_end_utc", report["release"]["end"] or ""],
        ["counting_capacity", report["capacity"]["status"]],
        ["counting_capacity_warning", report["capacity"]["warning"]],
        ["timezone", "UTC"],
        *_display_rows(report, generated_at),
        ["site", report["site"]],
        ["privacy_floor_views", report["floor"]],
        ["counted_classes", " ".join(report["counted_classes"])],
        ["geography_level", report["geography"]["level"]],
        ["report_version", report["version"]],
    ]


_TABLE_HEADER = ["label", "views", "row_type"]
_SECTIONS: list[tuple[str, str]] = [
    ("pages", "pages.csv"),
    ("sources", "sources.csv"),
    ("geography", "geography.csv"),
    ("links", "links.csv"),
    ("devices", "devices.csv"),
    ("browsers", "browsers.csv"),
    ("systems", "systems.csv"),
]


def csv_files(
    report: dict[str, Any],
    *,
    generated_at: datetime,
    requested: dict[str, Any] | None = None,
) -> dict[str, bytes]:
    """File name to bytes, in a fixed order."""
    files: dict[str, bytes] = {
        "metadata.csv": render_csv(
            ["key", "value"], _meta_rows(report, generated_at, requested or {})
        ),
        "funnel.csv": render_csv(["step", "value", "status"], _funnel_rows(report)),
    }
    for key, name in _SECTIONS:
        files[name] = render_csv(_TABLE_HEADER, _table_rows(report[key]))
    files["click_matrix.csv"] = render_csv(
        ["source", "destination", "clicks", "row_type"],
        _pair_rows(report["click_matrix"], "source"),
    )
    files["click_flows.csv"] = render_csv(
        ["flow", "clicks", "share_of_shown_clicks_percent", "row_type"],
        _flow_rows(report["click_matrix"]),
    )
    files["click_countries.csv"] = render_csv(
        ["country", "destination", "clicks", "row_type"],
        _pair_rows(report["click_countries"], "country"),
    )
    files["bot_classes.csv"] = render_csv(
        ["class", "name", "views", "share_percent", "row_type"], _bot_rows(report)
    )
    files["bot_reasons.csv"] = render_csv(
        _TABLE_HEADER, _table_rows(report["bots"]["reasons"])
    )
    return files


def _open_dir(out_dir: Path) -> int:
    """Open ``out_dir`` one component at a time, never following a link.

    Every step is an ``openat`` with ``O_DIRECTORY | O_NOFOLLOW`` relative to
    the descriptor of the step before, and a missing component is created with
    ``mkdirat`` (mode 0700) and then opened the same way, so there is no gap
    between a check and the open. The result must be a directory owned by the
    current user. Messages never repeat a path.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    absolute = Path(os.path.abspath(out_dir))
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            try:
                try:
                    nxt = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    nxt = os.open(part, flags, dir_fd=fd)
            except OSError:
                raise ValueError(
                    "--csv path must be a real directory (no symlinks)"
                ) from None
            os.close(fd)
            fd = nxt
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise ValueError("--csv directory must be a directory you own")
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_csv_dir(out_dir: Path, files: dict[str, bytes]) -> list[str]:
    """Write the files into ``out_dir`` (created owner-only if missing).

    The directory is walked with no-follow opens (see ``_open_dir``) and every
    file is created relative to its descriptor with ``O_NOFOLLOW``: a link at
    any component or at a file name is refused. An existing regular file of
    the same name is replaced and set to mode 0600 (failing closed).
    """
    dir_fd = _open_dir(out_dir)
    written: list[str] = []
    try:
        for name, data in files.items():
            fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=dir_fd,
            )
            with os.fdopen(fd, "wb") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
            written.append(name)
    finally:
        os.close(dir_fd)
    return written


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
