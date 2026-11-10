# SPDX-License-Identifier: Apache-2.0
"""The Grafana read subset: floored daily aggregates in a local SQLite file.

Grafana never gets a Cloudflare token, the Worker's database or a raw table.
The owner exports the Worker's aggregate tables with a read-only query (the same
export ``reach-around report`` reads), and this job turns that export into one
small SQLite file holding only the tables named in ``TABLES``. Grafana reads
that file and nothing else.

What the subset leaves out on purpose: unique-visitor counts (they do not add
up across cells), region and city, browser, system, language, window size and
network type, bot reasons, the media kit and leads, and every raw row. Click
rows are counted per day and link; no click row is copied.

The privacy floor is the report's: per day and per table, a cell under the floor
is never written alone. The small cells are merged into one ``OTHER`` row only
when the merge reaches the floor, using the same rule as the report (the
smallest shown cell joins the merged row whenever something is hidden). It is
label suppression, not k-anonymity; a reader who holds two days or two tables
can still subtract. The output is deterministic: rows are sorted, nothing
carries a clock, and the file is built in one pass and moved into place whole.

Only the newest 90 days of daily data are kept, counted back from the newest day
in the export (for this site), so a long export cannot carry older data into the
file. The monthly series (``*_monthly``) comes only from the 25 month archive
(``reach_around.funnel.archive``), which is floored before it is written.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from reach_around.config.era import UNKNOWN, classify_window
from reach_around.funnel.export import Export
from reach_around.funnel.gaps import coverage_for_window
from reach_around.funnel.report import (
    FLOOR,
    _geo_label,
    _source_label,
    _suppress,
    _utc_day,
)

SCHEMA_VERSION = 4
RETENTION_DAYS = 90  # the ceiling for local copies (analytics-design, threat B2)
OTHER = "(below the floor)"
APPLICATION_ID = 0x52410335  # marks a file this job made, so it never overwrites others

TABLES: dict[str, tuple[str, ...]] = {
    "grafana_meta": ("key", "value"),
    "grafana_annotations": ("day", "text"),
    "grafana_views_daily": ("day", "bot_class", "views"),
    "grafana_pages_daily": ("day", "page", "views"),
    "grafana_sources_daily": ("day", "source", "views"),
    "grafana_countries_daily": ("day", "country", "views"),
    "grafana_clicks_daily": ("day", "link_id", "clicks"),
    # The monthly series (issue #399): from the 25 month archive, floored before
    # it was archived. Empty unless an archive directory is given. month is UTC
    # YYYY-MM; a label total is a sum of archived (already floored) cells.
    "grafana_clicks_monthly": ("month", "link_id", "clicks"),
    "grafana_sources_monthly": ("month", "source", "clicks"),
    "grafana_countries_monthly": ("month", "country", "clicks"),
    # Days with no or partial collection (issue #402). A day listed here has no row of
    # zeros in the tables above: draw it as a gap. status is gap or partial; reason is
    # outage, quota, kill_switch or site_kill_switch (joined with +).
    "grafana_gaps_daily": ("day", "status", "reason"),
}

_DDL = """
CREATE TABLE grafana_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE grafana_annotations (
    day TEXT NOT NULL PRIMARY KEY, text TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE grafana_views_daily (
    day TEXT NOT NULL, bot_class TEXT NOT NULL, views INTEGER NOT NULL,
    PRIMARY KEY (day, bot_class)
) WITHOUT ROWID;
CREATE TABLE grafana_pages_daily (
    day TEXT NOT NULL, page TEXT NOT NULL, views INTEGER NOT NULL,
    PRIMARY KEY (day, page)
) WITHOUT ROWID;
CREATE TABLE grafana_sources_daily (
    day TEXT NOT NULL, source TEXT NOT NULL, views INTEGER NOT NULL,
    PRIMARY KEY (day, source)
) WITHOUT ROWID;
CREATE TABLE grafana_countries_daily (
    day TEXT NOT NULL, country TEXT NOT NULL, views INTEGER NOT NULL,
    PRIMARY KEY (day, country)
) WITHOUT ROWID;
CREATE TABLE grafana_clicks_daily (
    day TEXT NOT NULL, link_id TEXT NOT NULL, clicks INTEGER NOT NULL,
    PRIMARY KEY (day, link_id)
) WITHOUT ROWID;
CREATE TABLE grafana_clicks_monthly (
    month TEXT NOT NULL, link_id TEXT NOT NULL, clicks INTEGER NOT NULL,
    PRIMARY KEY (month, link_id)
) WITHOUT ROWID;
CREATE TABLE grafana_sources_monthly (
    month TEXT NOT NULL, source TEXT NOT NULL, clicks INTEGER NOT NULL,
    PRIMARY KEY (month, source)
) WITHOUT ROWID;
CREATE TABLE grafana_countries_monthly (
    month TEXT NOT NULL, country TEXT NOT NULL, clicks INTEGER NOT NULL,
    PRIMARY KEY (month, country)
) WITHOUT ROWID;
CREATE TABLE grafana_gaps_daily (
    day TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
    PRIMARY KEY (day)
) WITHOUT ROWID;
"""


class GrafanaExportError(ValueError):
    """The store could not be written. The message never holds export data."""


@dataclass
class Subset:
    site: str = "links"
    floor: int = FLOOR
    cutover_utc: str | None = None
    window_era: str | None = None
    annotations: list[tuple[str, str]] = field(default_factory=list)
    views: list[tuple[str, str, int]] = field(default_factory=list)
    pages: list[tuple[str, str, int]] = field(default_factory=list)
    sources: list[tuple[str, str, int]] = field(default_factory=list)
    countries: list[tuple[str, str, int]] = field(default_factory=list)
    clicks: list[tuple[str, str, int]] = field(default_factory=list)
    gaps: list[tuple[str, str, str]] = field(default_factory=list)
    clicks_monthly: list[tuple[str, str, int]] = field(default_factory=list)
    sources_monthly: list[tuple[str, str, int]] = field(default_factory=list)
    countries_monthly: list[tuple[str, str, int]] = field(default_factory=list)

    def with_archive(self, records: list[dict[str, Any]]) -> None:
        """Add the monthly series from validated archive records (already floored)."""
        for rec in records:
            month = rec["month"]
            per_link: dict[str, int] = defaultdict(int)
            per_source: dict[str, int] = defaultdict(int)
            per_country: dict[str, int] = defaultdict(int)
            for r in rec["sources"]:
                per_link[r["link_id"]] += r["clicks"]
                per_source[r["label"]] += r["clicks"]
            for r in rec["countries"]:
                per_country[r["label"]] += r["clicks"]
            self.clicks_monthly += [(month, k, v) for k, v in sorted(per_link.items())]
            self.sources_monthly += [
                (month, k, v) for k, v in sorted(per_source.items())
            ]
            self.countries_monthly += [
                (month, k, v) for k, v in sorted(per_country.items())
            ]

    def counts(self) -> dict[str, int]:
        return {
            "grafana_annotations": len(self.annotations),
            "grafana_views_daily": len(self.views),
            "grafana_pages_daily": len(self.pages),
            "grafana_sources_daily": len(self.sources),
            "grafana_countries_daily": len(self.countries),
            "grafana_clicks_daily": len(self.clicks),
            "grafana_gaps_daily": len(self.gaps),
            "grafana_clicks_monthly": len(self.clicks_monthly),
            "grafana_sources_monthly": len(self.sources_monthly),
            "grafana_countries_monthly": len(self.countries_monthly),
        }


def _floor_days(
    per_day: dict[str, dict[str, int]],
    floor: int,
    pinned: frozenset[str] = frozenset(),
) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for day in sorted(per_day):
        result = _suppress(per_day[day], floor, pinned)
        out.extend((day, label, views) for label, views in result.shown)
        if result.other:
            out.append((day, OTHER, result.other))
    return sorted(out)


def build_subset(
    export: Export,
    *,
    site: str = "links",
    floor: int = FLOOR,
    cutover: date | None = None,
    today: date | None = None,
) -> Subset:
    """Reduce a validated export to the documented, floored daily subset."""
    if floor < FLOOR:
        raise ValueError(f"the floor cannot be below {FLOOR}")
    classes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    pages: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sources: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    countries: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    clicks: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    view_rows = [r for r in export.view_daily if r["site"] == site]
    click_rows = [
        {**c, "day": _utc_day(c["ts"])}
        for c in export.click_events
        if c.get("site", "links") == site
    ]
    days = [r["day"] for r in view_rows] + [c["day"] for c in click_rows]
    oldest = (
        (date.fromisoformat(max(days)) - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        if days
        else ""
    )
    for r in view_rows:
        if r["day"] < oldest:
            continue
        classes[r["day"]][r["bot_class"]] += r["views"]
        if r["bot_class"] == "h":
            pages[r["day"]][r["page"]] += r["views"]
            sources[r["day"]][_source_label(r["src_kind"], r["src"])] += r["views"]
            countries[r["day"]][_geo_label(r["country"], "", "", "country")] += r[
                "views"
            ]
    for c in click_rows:
        if c["day"] >= oldest:
            clicks[c["day"]][c["link_id"]] += 1
    cov = coverage_for_window(
        export.analytics_coverage,
        "analytics_coverage" in export.present,
        oldest or None,
        max([*days, *(r["day"] for r in export.analytics_coverage)], default=None),
        today or datetime.now(UTC).date(),
    )
    gaps = sorted(
        [(g.day, "gap", g.reasons[0]) for g in cov.gaps]
        + [(p.day, "partial", "+".join(p.reasons)) for p in cov.partial]
    )
    current_day = today or datetime.now(UTC).date()
    retained_days = sorted(day for day in days if day >= oldest)
    since = date.fromisoformat(retained_days[0]) if retained_days else None
    until = date.fromisoformat(retained_days[-1]) if retained_days else None
    window_era = classify_window(since, until, cutover, current_day)
    # an arrived cutover is always marked; only the window era needs data
    active_cutover = cutover if cutover and cutover <= current_day else None
    return Subset(
        site=site,
        floor=floor,
        cutover_utc=active_cutover.isoformat() if active_cutover else None,
        window_era=window_era if active_cutover and window_era != UNKNOWN else None,
        annotations=(
            [(active_cutover.isoformat(), "Reach Around cutover")]
            if active_cutover
            else []
        ),
        gaps=gaps,
        views=_floor_days(classes, floor, frozenset({"h"})),
        pages=_floor_days(pages, floor),
        sources=_floor_days(sources, floor),
        countries=_floor_days(countries, floor),
        clicks=_floor_days(clicks, floor),
    )


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _is_ours(head: bytes) -> bool:
    """The SQLite header: magic string, and the application id at offset 68."""
    return (
        len(head) >= 72
        and head.startswith(_SQLITE_MAGIC)
        and int.from_bytes(head[68:72], "big") == APPLICATION_ID
    )


def _open_parent(path: Path) -> int:
    """Open the output directory without following a link in its last part."""
    try:
        return os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise GrafanaExportError(
            "the output directory does not exist or is a symbolic link"
        ) from None


def _check_target(dir_fd: int, name: str) -> None:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise GrafanaExportError("the output path is not a regular file")
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        raise GrafanaExportError("the output file cannot be read") from None
    try:
        head = os.read(fd, 100)
    finally:
        os.close(fd)
    if not _is_ours(head):
        raise GrafanaExportError(
            "the output file exists and is not a store made by this job; "
            "refusing to overwrite it"
        )


def _build_bytes(subset: Subset) -> bytes:
    con = sqlite3.connect(":memory:")
    try:
        con.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        con.executescript(_DDL)
        data: dict[str, list[Any]] = {
            "grafana_meta": [
                *(
                    [("cutover_utc", subset.cutover_utc)]
                    if subset.cutover_utc is not None
                    else []
                ),
                ("floor", str(subset.floor)),
                ("schema_version", str(SCHEMA_VERSION)),
                ("site", subset.site),
                *(
                    [("window_era", subset.window_era)]
                    if subset.window_era is not None
                    else []
                ),
            ],
            "grafana_annotations": subset.annotations,
            "grafana_views_daily": subset.views,
            "grafana_pages_daily": subset.pages,
            "grafana_sources_daily": subset.sources,
            "grafana_countries_daily": subset.countries,
            "grafana_clicks_daily": subset.clicks,
            "grafana_gaps_daily": subset.gaps,
            "grafana_clicks_monthly": subset.clicks_monthly,
            "grafana_sources_monthly": subset.sources_monthly,
            "grafana_countries_monthly": subset.countries_monthly,
        }
        for table, rows in data.items():
            marks = ", ".join("?" for _ in TABLES[table])
            con.executemany(
                f"INSERT INTO {table} VALUES ({marks})",  # noqa: S608 - table names are the fixed TABLES keys, values are bound
                rows,
            )
        con.commit()
        con.execute("VACUUM")
        return con.serialize()
    finally:
        con.close()


def write_store(path: Path, subset: Subset) -> None:
    """Build the store in memory, write it beside ``path``, move it in whole.

    Every step is relative to a directory descriptor opened with no-follow, so a
    link in the output directory, or one swapped in later, is not followed.
    """
    payload = _build_bytes(subset)
    name = path.name
    if not name or name in (".", ".."):
        raise GrafanaExportError("the output path has no file name")
    dir_fd = _open_parent(path)
    tmp_name = f".grafana-{os.getpid()}-{os.urandom(4).hex()}.tmp"
    try:
        _check_target(dir_fd, name)
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o640,
            dir_fd=dir_fd,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name, dir_fd=dir_fd)
            raise
    finally:
        os.close(dir_fd)
