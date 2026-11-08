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
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reach_around.funnel.export import Export
from reach_around.funnel.report import (
    FLOOR,
    _geo_label,
    _source_label,
    _suppress,
    _utc_day,
)

SCHEMA_VERSION = 1
OTHER = "(below the floor)"
APPLICATION_ID = 0x52410335  # marks a file this job made, so it never overwrites others

TABLES: dict[str, tuple[str, ...]] = {
    "grafana_meta": ("key", "value"),
    "grafana_views_daily": ("day", "bot_class", "views"),
    "grafana_pages_daily": ("day", "page", "views"),
    "grafana_sources_daily": ("day", "source", "views"),
    "grafana_countries_daily": ("day", "country", "views"),
    "grafana_clicks_daily": ("day", "link_id", "clicks"),
}

_DDL = """
CREATE TABLE grafana_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
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
"""


class GrafanaExportError(ValueError):
    """The store could not be written. The message never holds export data."""


@dataclass
class Subset:
    site: str = "links"
    floor: int = FLOOR
    views: list[tuple[str, str, int]] = field(default_factory=list)
    pages: list[tuple[str, str, int]] = field(default_factory=list)
    sources: list[tuple[str, str, int]] = field(default_factory=list)
    countries: list[tuple[str, str, int]] = field(default_factory=list)
    clicks: list[tuple[str, str, int]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "grafana_views_daily": len(self.views),
            "grafana_pages_daily": len(self.pages),
            "grafana_sources_daily": len(self.sources),
            "grafana_countries_daily": len(self.countries),
            "grafana_clicks_daily": len(self.clicks),
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


def build_subset(export: Export, *, site: str = "links", floor: int = FLOOR) -> Subset:
    """Reduce a validated export to the documented, floored daily subset."""
    if floor < FLOOR:
        raise ValueError(f"the floor cannot be below {FLOOR}")
    classes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    pages: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sources: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    countries: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    clicks: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in export.view_daily:
        if r["site"] != site:
            continue
        classes[r["day"]][r["bot_class"]] += r["views"]
        if r["bot_class"] == "h":
            pages[r["day"]][r["page"]] += r["views"]
            sources[r["day"]][_source_label(r["src_kind"], r["src"])] += r["views"]
            countries[r["day"]][_geo_label(r["country"], "", "", "country")] += r[
                "views"
            ]
    for c in export.click_events:
        if c.get("site", "links") == site:
            clicks[_utc_day(c["ts"])][c["link_id"]] += 1
    return Subset(
        site=site,
        floor=floor,
        views=_floor_days(classes, floor, frozenset({"h"})),
        pages=_floor_days(pages, floor),
        sources=_floor_days(sources, floor),
        countries=_floor_days(countries, floor),
        clicks=_floor_days(clicks, floor),
    )


def _is_ours(path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        return bool(
            con.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        )
    except sqlite3.Error:
        return False
    finally:
        con.close()


def _check_target(path: Path) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise GrafanaExportError("the output directory does not exist")
    if path.is_symlink():
        raise GrafanaExportError("the output path is a symbolic link")
    if path.exists():
        if not path.is_file():
            raise GrafanaExportError("the output path is not a regular file")
        if not _is_ours(path):
            raise GrafanaExportError(
                "the output file exists and is not a store made by this job; "
                "refusing to overwrite it"
            )


def write_store(path: Path, subset: Subset) -> None:
    """Build the SQLite file beside ``path`` and move it into place whole."""
    _check_target(path)
    fd, tmp_name = tempfile.mkstemp(prefix=".grafana-", suffix=".tmp", dir=path.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        con = sqlite3.connect(tmp)
        try:
            con.execute("PRAGMA journal_mode = OFF")
            con.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            con.executescript(_DDL)
            meta: list[tuple[str, str]] = [
                ("floor", str(subset.floor)),
                ("schema_version", str(SCHEMA_VERSION)),
                ("site", subset.site),
            ]
            data: dict[str, list[Any]] = {
                "grafana_meta": meta,
                "grafana_views_daily": subset.views,
                "grafana_pages_daily": subset.pages,
                "grafana_sources_daily": subset.sources,
                "grafana_countries_daily": subset.countries,
                "grafana_clicks_daily": subset.clicks,
            }
            for table, rows in data.items():
                marks = ", ".join("?" for _ in TABLES[table])
                con.executemany(
                    f"INSERT INTO {table} VALUES ({marks})",  # noqa: S608 - table names are the fixed TABLES keys, values are bound
                    rows,
                )
            con.commit()
            con.execute("VACUUM")
        finally:
            con.close()
        tmp.chmod(0o640)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
