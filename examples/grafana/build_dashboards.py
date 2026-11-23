# SPDX-License-Identifier: Apache-2.0
"""Write the example dashboards (dashboards/*.json) from one place.

The JSON files are the product; this script is how they stay consistent. In
particular the Twitch and Kick panels come from one function called twice, so
the two sides cannot drift apart. A test fails when the committed files differ
from what this script writes.

    python3 build_dashboards.py            # rewrite dashboards/*.json
    python3 build_dashboards.py --check    # exit 1 if a file is out of date

Every query reads only the `grafana_*` tables documented in docs/grafana.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "dashboards"
DS_TYPE = "frser-sqlite-datasource"
DS_UID = "reach-around-sqlite"
DS = {"type": DS_TYPE, "uid": DS_UID}

# The two watch links, in display order. Both sides get the same panels, sizes,
# colors and queries; only the link id and the name differ.
WATCH = (("twitch", "Twitch"), ("kick", "Kick"))

# Time filter: Grafana puts the range in as epoch milliseconds before the query
# runs. `day` is a UTC YYYY-MM-DD text column.
IN_RANGE = (
    "day BETWEEN date($__from / 1000, 'unixepoch') AND date($__to / 1000, 'unixepoch')"
)
EPOCH = "CAST(strftime('%s', day) AS INTEGER)"

# A count printed by this dashboard is a sum of printed rows, each at or above
# the floor. This mapping is a second guard: anything from 1 to 4 reads Under 5.
UNDER_5 = [
    {
        "type": "range",
        "options": {
            "from": 0.0001,
            "to": 4.9999,
            "result": {"text": "Under 5", "index": 0},
        },
    }
]
FLAT_COLOR = {"mode": "fixed", "fixedColor": "#8ab4f8"}  # one color: no side favored


def target(sql: str, *, time: bool = False, ref: str = "A") -> dict[str, Any]:
    return {
        "refId": ref,
        "datasource": DS,
        "queryText": sql,
        "rawQueryText": sql,
        "timeColumns": ["time"] if time else [],
    }


def _panel(
    pid: int,
    kind: str,
    title: str,
    pos: tuple[int, int, int, int],
    targets: list[dict[str, Any]],
    *,
    description: str = "",
    defaults: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    transformations: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    x, y, w, h = pos
    return {
        "id": pid,
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "fieldConfig": {"defaults": defaults or {}, "overrides": overrides or []},
        "options": options or {},
        "transformations": transformations or [],
    }


def stat(
    pid: int, title: str, pos: tuple[int, int, int, int], sql: str, **kw: Any
) -> dict[str, Any]:
    count = kw.pop("count", True)
    defaults = {
        "color": kw.pop("color", FLAT_COLOR),
        # only a count can be Under 5; a ratio or a percent is not a count
        "mappings": UNDER_5 if count else [],
        "decimals": kw.pop("decimals", 0),
        "unit": kw.pop("unit", "none"),
        "noValue": "No data",
    }
    options = {
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        "colorMode": "value",
        "graphMode": "none",
        "textMode": "value",
    }
    return _panel(
        pid, "stat", title, pos, [target(sql)], defaults=defaults, options=options, **kw
    )


def series(
    pid: int,
    title: str,
    pos: tuple[int, int, int, int],
    sql: str,
    *,
    bars: bool = False,
    stack: bool = False,
    color: dict[str, Any] | None = None,
    transformations: list[dict[str, Any]] | None = None,
    **kw: Any,
) -> dict[str, Any]:
    custom: dict[str, Any] = {
        "drawStyle": "bars" if bars else "line",
        "lineWidth": 2,
        "fillOpacity": 40 if bars or stack else 10,
        "showPoints": "never",
        "spanNulls": False,  # a gap stays a gap; it is not drawn as zero
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
    }
    defaults: dict[str, Any] = {
        "custom": custom,
        "min": 0,
        "decimals": 0,
        "noValue": "No data",
        "color": color or {"mode": "palette-classic"},
    }
    options = {
        "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
        "tooltip": {"mode": "multi", "sort": "desc"},
    }
    return _panel(
        pid,
        "timeseries",
        title,
        pos,
        [target(sql, time=True)],
        defaults=defaults,
        options=options,
        transformations=transformations,
        **kw,
    )


def table(
    pid: int,
    title: str,
    pos: tuple[int, int, int, int],
    sql: str,
    *,
    count: bool = False,
    **kw: Any,
) -> dict[str, Any]:
    defaults = {"mappings": UNDER_5 if count else [], "custom": {"align": "auto"}}
    return _panel(
        pid,
        "table",
        title,
        pos,
        [target(sql)],
        defaults=defaults,
        options={"showHeader": True, "cellHeight": "sm"},
        **kw,
    )


def text(
    pid: int, title: str, pos: tuple[int, int, int, int], body: str
) -> dict[str, Any]:
    p = _panel(
        pid, "text", title, pos, [], options={"mode": "markdown", "content": body}
    )
    p.pop("targets")
    p.pop("datasource")
    return p


def dashboard(
    uid: str,
    title: str,
    panels: list[dict[str, Any]],
    *,
    annotations: bool = True,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    marks: list[dict[str, Any]] = [
        {
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": False,
            "hide": True,
            "name": "Annotations & Alerts",
            "type": "dashboard",
        }
    ]
    if annotations:
        marks += [
            {
                "name": "Cutover",
                "datasource": DS,
                "enable": True,
                "iconColor": "#b39ddb",
                "target": target(
                    sql(
                        """SELECT @EPOCH@ AS time, text FROM grafana_annotations
                           WHERE @RANGE@"""
                    ),
                    time=True,
                ),
            },
            {
                "name": "Collection gaps",
                "datasource": DS,
                "enable": True,
                "iconColor": "#ef9a9a",
                "target": target(
                    sql(
                        """SELECT @EPOCH@ AS time,
                           status || ' (' || reason || '): no data is not zero'
                           AS text FROM grafana_gaps_daily WHERE @RANGE@"""
                    ),
                    time=True,
                ),
            },
        ]
    return {
        "uid": uid,
        "title": title,
        "tags": ["reach-around"],
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "graphTooltip": 1,
        "timezone": "utc",
        "refresh": "",
        "time": {"from": "now-30d", "to": "now"},
        "timepicker": {"hidden": False, "refresh_intervals": []},
        "annotations": {"list": marks},
        "links": links or [],
        "templating": {"list": []},
        "panels": panels,
    }


NAV = [
    {
        "title": "Reach Around dashboards",
        "type": "dashboards",
        "tags": ["reach-around"],
        "asDropdown": False,
        "includeVars": False,
        "keepTime": True,
    }
]

NOTE_FLOOR = (
    "Every count comes from a Reach Around export that already applied the "
    "privacy floor of 5. A row under the floor is never stored alone: small rows "
    "are merged into one line named `(below the floor)`. On a day with any small "
    "row, the largest row joins that line too, so a link can be missing on such a "
    "day. Any count between 1 and 4 shows as **Under 5**. A day marked red had no "
    "or partial data collection, and a missing point is not a zero: it is a gap or "
    "a count under the floor. Sample data is synthetic."
)


def sql(text: str, **names: str) -> str:
    """One line of SQL: whitespace collapsed, @NAME@ filled from fixed constants."""
    out = " ".join(text.split())
    for key, value in {"EPOCH": EPOCH, "RANGE": IN_RANGE, **names}.items():
        out = out.replace(f"@{key}@", value)
    return out


# `link` for a merged row: the floor's own label, said plainly.
LINK_LABEL = (
    "CASE link_id WHEN '(below the floor)' THEN 'Small links, merged' ELSE link_id END"
)
RANGE_C = IN_RANGE.replace("day", "c.day")


def overview() -> dict[str, Any]:
    views_h = sql(
        """SELECT @EPOCH@ AS time, views FROM grafana_views_daily
           WHERE bot_class = 'h' AND @RANGE@ ORDER BY day"""
    )
    clicks = sql(
        """SELECT @EPOCH@ AS time, SUM(clicks) AS clicks FROM grafana_clicks_daily
           WHERE @RANGE@ GROUP BY day ORDER BY day"""
    )
    classes = sql(
        """SELECT @EPOCH@ AS time,
           CASE bot_class WHEN 'h' THEN 'Likely human'
             WHEN 'v' THEN 'Verified crawler' WHEN 'a' THEN 'Automated'
             WHEN 's' THEN 'Suspicious' ELSE 'Other (below the floor)' END AS class,
           views FROM grafana_views_daily WHERE @RANGE@ ORDER BY day"""
    )
    ratio = sql(
        """SELECT CAST(strftime('%s', c.day) AS INTEGER) AS time,
           c.clicks * 100.0 / v.views AS clicks_per_100_views
           FROM (SELECT day, SUM(clicks) AS clicks FROM grafana_clicks_daily
                 GROUP BY day) AS c
           JOIN grafana_views_daily AS v ON v.day = c.day AND v.bot_class = 'h'
           WHERE @RANGE_C@ ORDER BY c.day""",
        RANGE_C=RANGE_C,
    )
    gaps = sql(
        """SELECT day, status, reason FROM grafana_gaps_daily
           WHERE @RANGE@ ORDER BY day"""
    )
    stat_views = sql(
        """SELECT SUM(views) FROM grafana_views_daily
           WHERE bot_class = 'h' AND @RANGE@"""
    )
    stat_clicks = sql("SELECT SUM(clicks) FROM grafana_clicks_daily WHERE @RANGE@")
    stat_ratio = sql(
        """SELECT (SELECT SUM(clicks) FROM grafana_clicks_daily WHERE @RANGE@)
           * 100.0 / NULLIF((SELECT SUM(views) FROM grafana_views_daily
                             WHERE bot_class = 'h' AND @RANGE@), 0)"""
    )
    floor = "SELECT CAST(value AS INTEGER) FROM grafana_meta WHERE key = 'floor'"
    by_class = [
        {
            "id": "partitionByValues",
            "options": {"fields": ["class"], "naming": {"asLabels": False}},
        }
    ]
    return dashboard(
        "ra-overview",
        "Reach Around: counts over time",
        [
            stat(1, "Likely-human views", (0, 0, 6, 4), stat_views),
            stat(2, "Link clicks", (6, 0, 6, 4), stat_clicks),
            stat(
                3,
                "Clicks per 100 views",
                (12, 0, 6, 4),
                stat_ratio,
                decimals=1,
                count=False,
                description="Clicks over likely-human views, not a conversion rate.",
            ),
            stat(4, "Privacy floor", (18, 0, 6, 4), floor, count=False),
            series(5, "Likely-human views per day", (0, 4, 12, 8), views_h),
            series(6, "Link clicks per day", (12, 4, 12, 8), clicks),
            series(
                7,
                "Views per day by traffic class",
                (0, 12, 12, 8),
                classes,
                stack=True,
                transformations=by_class,
            ),
            series(
                8,
                "Clicks per 100 likely-human views",
                (12, 12, 12, 8),
                ratio,
                color=FLAT_COLOR,
            ),
            table(
                9,
                "Collection gaps in this range",
                (0, 20, 12, 7),
                gaps,
                description="A gap is a day with no collection; a partial day "
                "has some hours missing.",
            ),
            text(10, "About these numbers", (12, 20, 12, 7), NOTE_FLOOR),
        ],
        links=NAV,
    )


def clicks_dash() -> dict[str, Any]:
    by_link = sql(
        """SELECT @LINK@ AS link, SUM(clicks) AS clicks FROM grafana_clicks_daily
           WHERE @RANGE@ GROUP BY link_id ORDER BY clicks DESC""",
        LINK=LINK_LABEL,
    )
    per_day = sql(
        """SELECT @EPOCH@ AS time, @LINK@ AS link, clicks FROM grafana_clicks_daily
           WHERE @RANGE@ ORDER BY day""",
        LINK=LINK_LABEL,
    )
    share = sql(
        """SELECT @LINK@ AS link,
           ROUND(SUM(clicks) * 100.0 / (SELECT SUM(clicks)
                 FROM grafana_clicks_daily WHERE @RANGE@), 1) AS share_percent,
           SUM(clicks) AS clicks FROM grafana_clicks_daily
           WHERE @RANGE@ GROUP BY link_id ORDER BY clicks DESC""",
        LINK=LINK_LABEL,
    )
    split = [
        {
            "id": "partitionByValues",
            "options": {"fields": ["link"], "naming": {"asLabels": False}},
        }
    ]
    group_note = (
        "Clicks **by group** are not in the export yet. `grafana_clicks_daily` is "
        "per link id, and the group rollup with its Under 5 rule (#678) is not "
        "built. When it ships as a table, add a panel here that reads only its "
        "printed rows. Until then this page shows printed rows per link."
    )
    return dashboard(
        "ra-clicks",
        "Reach Around: link clicks",
        [
            _panel(
                1,
                "barchart",
                "Link clicks by link",
                (0, 0, 12, 10),
                [target(by_link)],
                defaults={"color": {"mode": "palette-classic"}},
                options={
                    "orientation": "horizontal",
                    "legend": {"showLegend": False},
                    "xField": "link",
                },
            ),
            table(2, "Share of link clicks", (12, 0, 12, 10), share),
            series(
                3,
                "Link clicks per day, by link",
                (0, 10, 18, 9),
                per_day,
                stack=True,
                bars=True,
                transformations=split,
            ),
            text(4, "Groups", (18, 10, 6, 9), group_note),
        ],
        links=NAV,
    )


def watch_panels(link_id: str, name: str, x: int, base: int) -> list[dict[str, Any]]:
    """The same four panels for one watch link. Called once per side."""
    clicks_range = sql(
        """SELECT SUM(clicks) FROM grafana_clicks_daily
           WHERE link_id = '@ID@' AND @RANGE@""",
        ID=link_id,
    )
    share = sql(
        """SELECT SUM(CASE WHEN link_id = '@ID@' THEN clicks ELSE 0 END) * 100.0
           / NULLIF(SUM(clicks), 0) FROM grafana_clicks_daily WHERE @RANGE@""",
        ID=link_id,
    )
    per_day = sql(
        """SELECT @EPOCH@ AS time, clicks FROM grafana_clicks_daily
           WHERE link_id = '@ID@' AND @RANGE@ ORDER BY day""",
        ID=link_id,
    )
    per_100 = sql(
        """SELECT CAST(strftime('%s', c.day) AS INTEGER) AS time,
           c.clicks * 100.0 / v.views AS clicks_per_100_views
           FROM grafana_clicks_daily AS c
           JOIN grafana_views_daily AS v ON v.day = c.day AND v.bot_class = 'h'
           WHERE c.link_id = '@ID@' AND @RANGE_C@ ORDER BY c.day""",
        ID=link_id,
        RANGE_C=RANGE_C,
    )
    return [
        stat(base, f"{name}: clicks", (x, 0, 6, 4), clicks_range),
        stat(
            base + 1,
            f"{name}: share of link clicks",
            (x + 6, 0, 6, 4),
            share,
            unit="percent",
            decimals=1,
            count=False,
        ),
        series(
            base + 2,
            f"{name}: clicks per day",
            (x, 4, 12, 9),
            per_day,
            bars=True,
            color=FLAT_COLOR,
        ),
        series(
            base + 3,
            f"{name}: clicks per 100 likely-human views",
            (x, 13, 12, 8),
            per_100,
            color=FLAT_COLOR,
        ),
    ]


def watch() -> dict[str, Any]:
    panels: list[dict[str, Any]] = []
    for i, (link_id, name) in enumerate(WATCH):
        panels += watch_panels(link_id, name, 12 * i, 1 + 10 * i)
    note = (
        "Both sides use the same panels, sizes, color and queries; only the link "
        "id differs (`twitch`, `kick`). Edit the `WATCH` pair in "
        "`build_dashboards.py` if your watch links use other ids. " + NOTE_FLOOR
    )
    panels.append(text(99, "About this page", (0, 21, 24, 5), note))
    return dashboard("ra-watch", "Reach Around: Twitch and Kick", panels, links=NAV)


FILES = {
    "overview.json": overview,
    "clicks.json": clicks_dash,
    "watch.json": watch,
}


def render(name: str) -> str:
    return json.dumps(FILES[name](), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="fail if a file is out of date"
    )
    args = parser.parse_args(argv)
    stale = []
    for name in FILES:
        path = OUT / name
        want = render(name)
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != want:
                stale.append(name)
        else:
            OUT.mkdir(exist_ok=True)
            path.write_text(want, encoding="utf-8")
    if stale:
        print("out of date: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
