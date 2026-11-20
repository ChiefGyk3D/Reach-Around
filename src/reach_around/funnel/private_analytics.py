# SPDX-License-Identifier: Apache-2.0
"""Private platform analytics for the owner report (#397).

Views, watch time and demographics that a platform's authorized API returned to
the owner (YouTube Analytics, Instagram insights), shown with the privacy floor.
They are the owner's own authorized data: the display policy (#424) keeps them
out of the site, the kit, both PDFs and every public file, and this block is
printed by ``reach-around report --history`` only (text, JSON and CSV). It is
not in the HTML owner report. It reads no network and writes nothing.

Records are picked by their ``api_provider`` tag in ``PRIVATE_PROVIDERS`` and
nothing else; a metric name never decides. Rules, each tested:

* A count under the floor (5, raise only) is never printed. Count cells (followers
  by age, gender or country; views by country) go through the report's own
  ``floor_cells`` rule, with the figure the collector already folded (``other``)
  treated as a remainder that is never printed alone: any cell under the floor
  and an ``other`` under the floor each pull the largest printed cell into one
  remainder line, so the line bounds nothing. A group that cannot be made safe
  prints nothing.
* An activity figure (views, watch minutes, reach and so on) under the floor
  prints as withheld; zero prints as zero. No total is printed.
* A share (YouTube viewer demographics, in hundredths of a percent) is a
  percentage, not a count of people; the collector already folds shares under
  2.00 percent into ``other``.
* Figures are never added across platforms or windows: definitions differ.
* Every figure carries its platform, provider, UTC date, era mark and the label
  "collected, not hand-kept". A series with a definition tag shows the tag.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from reach_around.config.era import classify_era, era_text
from reach_around.config.zones import format_instant
from reach_around.counts.history import (
    PRIVATE_PROVIDERS,
    History,
    Point,
    Record,
    Series,
    parse_as_of,
)
from reach_around.funnel.floor import FLOOR, floor_cells, share_pins_small_count

LABEL = (
    "Private platform analytics (owner only, never published; collected through "
    "each platform's API, not hand-kept; UTC days)"
)
PROVENANCE = "collected, not hand-kept"
ACTIVITY_POINTS = 7
# The pool label for the collector's own folded figure. Real labels are letters,
# digits and hyphens, so this one can never collide with a cell; it is never printed.
OTHER = "~other"
NOTES = (
    "Private to you. YouTube and Instagram figures stay out of the kit, the "
    "site and every PDF (display policy, #424).",
    "Figures are never added across platforms or windows: definitions differ "
    "(a reach is a unique count over its window).",
    "A count under the floor is never printed. Cells under the floor are merged "
    "into one remainder line, together with the largest printed cell, so the "
    "line pins no single cell.",
)

# metric prefix -> what it counts
ACTIVITY = {
    "views": "views",
    "watch_minutes": "watch minutes",
    "reach": "accounts reached (unique over the window)",
    "interactions": "interactions",
    "engaged": "accounts engaged",
    "link_taps": "profile link taps",
}
_ACTIVITY_NAME = re.compile(r"^([a-z]+(?:_[a-z]+)*)_(\d+)d$")
_FDEMO = re.compile(r"^fdemo_(age|gender|country)_(.+)$")
_GEO = re.compile(r"^geo_(.+)_views$")
_SHARE = re.compile(r"^demo_(.+)$")
_AGE_SEX = re.compile(r"^a(.+)_([a-z])$")
FDEMO_NAMES = {
    "age": "Followers by age",
    "gender": "Followers by gender",
    "country": "Followers by country",
}
GEO_NAME = "Views by country"
SHARE_NAME = "Viewer shares by age and gender (percent)"


def _point_dict(
    point: Point, cutover: date | None, today: date, zone: str | None
) -> dict[str, Any]:
    day = parse_as_of(point.as_of_utc).date()
    out: dict[str, Any] = {"as_of_utc": point.as_of_utc}
    if zone is not None:
        out["as_of_display"] = format_instant(parse_as_of(point.as_of_utc), zone)
    era = classify_era(day, cutover, today)
    if era != "unknown":
        out["era"] = era
        out["era_text"] = era_text(era)
    if point.era is not None:
        out["definition"] = point.era
    out["corrected"] = point.corrected
    return out


def _label(kind: str, raw: str) -> str:
    if kind == "share":
        match = _AGE_SEX.match(raw)
        if match:
            return f"{match.group(1).replace('_', '-')} {match.group(2).upper()}"
    text = raw.replace("_", "-")
    return text.upper() if kind in {"country", "gender"} else text


def _count_group(
    cells: dict[str, int], stored_other: int, floor: int
) -> tuple[str, list[dict[str, Any]], int | None]:
    """(state, printed rows, remainder) for a group of count cells."""
    pool = dict(cells)
    if stored_other > 0:
        pool[OTHER] = stored_other
    result = floor_cells(pool, floor)
    if result.dropped:
        return "withheld", [], None
    rows = [
        {"label": label, "value": value}
        for label, value in result.shown
        if label != OTHER
    ]
    other = result.other
    if any(label == OTHER for label, _ in result.shown):
        other += stored_other
    if not rows and not other:
        return "withheld", [], None
    return "ok", rows, other or None


def _withhold_pinning_shares(section: dict[str, Any], floor: int) -> None:
    """Withhold a share group read against a printed count that would pin a cell.

    A share printed beside a count of the same platform tells a reader how many
    people or views the cell holds. The counts printed in the section (activity
    figures and the totals of the count groups) are the bases; one printed cell
    that could hold 1 to ``floor`` - 1 withholds the whole group, since the
    shares total 100 percent and a hidden one would follow from the rest.
    """
    bases = [p["value"] for m in section["activity"] for p in m["points"] if p["value"]]
    for g in section["groups"]:
        if g["kind"] == "count" and g["state"] == "ok":
            total = sum(r["value"] for r in g["rows"]) + (g["other"] or 0)
            if total:
                bases.append(total)
    for g in section["groups"]:
        if g["kind"] != "share" or g["state"] != "ok":
            continue
        if any(
            share_pins_small_count(row["value"], 10_000, base, floor)
            for row in g["rows"]
            for base in bases
        ):
            g.update(state="withheld", rows=[], other=None)


def build_private(
    history: History,
    *,
    floor: int = FLOOR,
    cutover: date | None,
    today: date,
    zone: str | None = None,
) -> dict[str, Any]:
    """The report's private analytics block from a validated history."""
    if floor < FLOOR:
        raise ValueError(f"the floor cannot be below {FLOOR}")
    by_metric: dict[tuple[str, str, str], list[Record]] = {}
    for record in history.records:
        if record.api_provider in PRIVATE_PROVIDERS:
            key = (record.platform, str(record.api_provider), record.metric)
            by_metric.setdefault(key, []).append(record)
    sections: dict[tuple[str, str], dict[str, Any]] = {}
    pending: dict[tuple[str, str, str], list[tuple[str, str, Series]]] = {}
    unrecognized = 0

    def section(platform: str, provider: str) -> dict[str, Any]:
        return sections.setdefault(
            (platform, provider),
            {
                "platform": platform,
                "provider": provider,
                "provenance": PROVENANCE,
                "activity": [],
                "groups": [],
            },
        )

    for (platform, provider, metric), records in sorted(by_metric.items()):
        series = Series(records)
        activity = _ACTIVITY_NAME.match(metric)
        fdemo = _FDEMO.match(metric)
        geo = _GEO.match(metric)
        share = _SHARE.match(metric)
        if fdemo:
            kind, suffix = fdemo.group(1), fdemo.group(2)
            name = FDEMO_NAMES[kind]
        elif geo:
            name, kind, suffix = GEO_NAME, "country", geo.group(1)
        elif share:
            name, kind, suffix = SHARE_NAME, "share", share.group(1)
        elif activity and activity.group(1) in ACTIVITY:
            by_day = series.by_day()
            points = []
            for day in sorted(by_day)[-ACTIVITY_POINTS:]:
                point = by_day[day]
                row = _point_dict(point, cutover, today, zone)
                shown = point.value == 0 or point.value >= floor
                row["value"] = point.value if shown else None
                row["status"] = "ok" if shown else "below_floor"
                points.append(row)
            section(platform, provider)["activity"].append(
                {
                    "metric": metric,
                    "unit": ACTIVITY[activity.group(1)],
                    "window_days": int(activity.group(2)),
                    "points": points,
                }
            )
            continue
        else:
            unrecognized += 1
            continue
        pending.setdefault((platform, provider, name), []).append(
            (kind, suffix, series)
        )
    for (platform, provider, name), members in sorted(pending.items()):
        latest = max(max(s.by_day()) for _, _, s in members)
        cells: dict[str, int] = {}
        stored_other = 0
        sample: Point | None = None
        for kind, suffix, series in members:
            found = series.by_day().get(latest)
            if found is None:
                continue
            sample = found
            if suffix == "other":
                stored_other += found.value
            else:
                cells[_label(kind, suffix)] = found.value
        if sample is None:  # pragma: no cover - ``latest`` came from a member
            continue
        is_share = members[0][0] == "share"
        group: dict[str, Any] = {
            "name": name,
            "kind": "share" if is_share else "count",
            **_point_dict(sample, cutover, today, zone),
        }
        if is_share:
            rows = [
                {"label": label, "value": value}
                for label, value in sorted(cells.items(), key=lambda i: (-i[1], i[0]))
            ]
            if stored_other:
                rows.append({"label": "other", "value": stored_other})
            group.update(state="ok" if rows else "withheld", rows=rows, other=None)
        else:
            state, rows, other = _count_group(cells, stored_other, floor)
            group.update(state=state, rows=rows, other=other)
        section(platform, provider)["groups"].append(group)
    for entry in sections.values():
        _withhold_pinning_shares(entry, floor)
    return {
        "label": LABEL,
        "timezone": "UTC",
        "floor": floor,
        "sections": [sections[k] for k in sorted(sections)],
        "unrecognized": unrecognized,
        "notes": list(NOTES),
    }


def _stamp(item: dict[str, Any]) -> str:
    stamp = item["as_of_utc"]
    if "as_of_display" in item:
        stamp = f"{item['as_of_display']} ({stamp})"
    extra = f"  [{item['era_text']}]" if "era_text" in item else ""
    mark = f"  definition: {item['definition']}" if "definition" in item else ""
    return f"as of {stamp}{extra}{mark}"


def private_lines(block: dict[str, Any]) -> list[str]:
    """Plain-text lines for the report."""
    lines = ["", block["label"]]
    if not block["sections"]:
        lines.append("  no private analytics in the history file")
    for section in block["sections"]:
        lines.append(
            f"  {section['platform']} ({section['provider']}; {section['provenance']})"
        )
        for metric in section["activity"]:
            window = (
                "one UTC day"
                if metric["window_days"] == 1
                else f"{metric['window_days']} UTC days"
            )
            lines.append(f"    {metric['metric']}: {metric['unit']}, {window}")
            for point in metric["points"]:
                value = (
                    f"{point['value']:,}"
                    if point["status"] == "ok"
                    else "below the floor"
                )
                lines.append(f"      {value:>16}  {_stamp(point)}")
        for group in section["groups"]:
            unit = "percent of viewers" if group["kind"] == "share" else "count"
            lines.append(f"    {group['name']} ({unit}), {_stamp(group)}")
            if group["state"] == "withheld":
                lines.append("      withheld (cells under the floor cannot be merged)")
                continue
            for row in group["rows"]:
                value = (
                    f"{row['value'] / 100:.2f}%"
                    if group["kind"] == "share"
                    else f"{row['value']:,}"
                )
                lines.append(f"      {row['label']:<22} {value:>12}")
            if group["other"]:
                lines.append(
                    f"      {'(other, below the floor)':<22} {group['other']:>12,}"
                )
    if block["unrecognized"]:
        lines.append(
            f"  {block['unrecognized']} private metric(s) not recognized; not shown"
        )
    for note in block["notes"]:
        lines.append(f"  {note}")
    return lines


CSV_HEADER = (
    "platform,provider,kind,name,label,as_of_utc,value,status,era,provenance"
).split(",")


def private_csv_rows(block: dict[str, Any]) -> list[list[Any]]:
    """One row per printed figure. A withheld figure has an empty value."""
    rows: list[list[Any]] = []
    for s in block["sections"]:
        base = [s["platform"], s["provider"]]
        for m in s["activity"]:
            for p in m["points"]:
                rows.append(
                    [
                        *base,
                        "activity",
                        m["metric"],
                        m["unit"],
                        p["as_of_utc"],
                        "" if p["value"] is None else p["value"],
                        p["status"],
                        p.get("era", ""),
                        s["provenance"],
                    ]
                )
        for g in s["groups"]:
            if g["state"] == "withheld":
                rows.append(
                    [
                        *base,
                        g["kind"],
                        g["name"],
                        "",
                        g["as_of_utc"],
                        "",
                        "withheld",
                        g.get("era", ""),
                        s["provenance"],
                    ]
                )
                continue
            items = [(r["label"], r["value"]) for r in g["rows"]]
            if g["other"]:
                items.append(("other (below the floor)", g["other"]))
            for label, value in items:
                rows.append(
                    [
                        *base,
                        g["kind"],
                        g["name"],
                        label,
                        g["as_of_utc"],
                        value,
                        "ok",
                        g.get("era", ""),
                        s["provenance"],
                    ]
                )
    return rows
