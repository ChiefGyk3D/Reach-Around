# SPDX-License-Identifier: Apache-2.0
"""Social numbers for the owner report (#398).

Reads the social-stats history (``reach_around.counts.history``) and builds one
block for the report: the latest number per platform and metric, the change over
30, 90 and 365 UTC days where the history has the record to compare against, the
days missing from the last 90, and the cutover label. Everything is UTC.

Rules, each tested:

* A change needs a record on exactly the day 30, 90 or 365 days before the latest
  one. No record that day means ``no_record``; a number is never guessed, carried
  forward or set to zero, and a missing day is listed as a gap.
* Imported history (source ``imported``, the Beacons import) is a separate block
  with its own label and is never added to, or compared with, collected numbers.
* A change that crosses a recorded definition break (``era`` tag) is withheld.
* A change window that crosses the cutover day is labeled.
* Collected YouTube Analytics figures are refused, as in the kit (#424): only
  an explicitly identified Data API ``subscribers`` count may come from the API
  for YouTube. Missing or unknown provenance is refused. A value the owner typed
  (``hand_kept``) is accepted.

The block is OWNER ONLY. The report is private; the history stays out of every
public file. It reads no network and writes nothing.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from reach_around.config.era import classify_era, classify_window
from reach_around.config.zones import format_instant
from reach_around.counts.history import History, Point, Record, Series, parse_as_of

WINDOWS = (30, 90, 365)
GAP_WINDOW_DAYS = 90
COLLECTED_LABEL = "Social numbers (fetched or typed; UTC days)"
IMPORTED_LABEL = (
    "Imported history (Beacons), shown on its own and never added to or compared "
    "with the numbers above"
)
REFUSED_REASON = (
    "collected YouTube Analytics figures are refused (display policy); "
    "type the figure by hand to include it"
)
ERA_TEXT = {
    "before": "before Reach Around",
    "after": "after",
    "spans": "spans the cutover (this change mixes before and after Reach Around)",
}
NOTES = (
    "Every date is a UTC day. A missing day is a gap, never a zero.",
    "A change is shown only when a record exists on the exact day 30, 90 or 365 "
    "days before the latest one.",
    "Private to you: the history file and this block are not for publication.",
)


def _refused(record: Record) -> bool:
    if record.source != "api" or not record.platform.startswith("youtube"):
        return False
    return not (
        record.platform == "youtube"
        and record.metric == "subscribers"
        and record.api_provider == "youtube_data_api"
    )


def _day(point: Point) -> date:
    return parse_as_of(point.as_of_utc).date()


def _latest(
    point: Point, cutover: date | None, today: date, zone: str | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "value": point.value,
        "as_of_utc": point.as_of_utc,
        "source": point.source,
        "corrected": point.corrected,
    }
    if zone is not None:
        out["as_of_display"] = format_instant(parse_as_of(point.as_of_utc), zone)
    if point.era is not None:
        out["definition"] = point.era
    if point.note is not None:
        out["note"] = point.note
    era = classify_era(_day(point), cutover, today)
    if era != "unknown":
        out["era"] = era
    return out


def _change(
    days: int,
    latest: Point,
    by_day: dict[date, Point],
    cutover: date | None,
    today: date,
) -> dict[str, Any]:
    last = _day(latest)
    base_day = last - timedelta(days=days)
    row: dict[str, Any] = {
        "days": days,
        "baseline_day": base_day.isoformat(),
        "baseline_value": None,
        "change": None,
        "percent": None,
        "status": "no_record",
    }
    base = by_day.get(base_day)
    if base is not None:
        row["baseline_value"] = base.value
        window = (
            point
            for point_day, point in by_day.items()
            if base_day <= point_day <= last
        )
        if any(point.era != base.era for point in window):
            row["status"] = "definition_changed"
        else:
            row["status"] = "ok"
            row["change"] = latest.value - base.value
            if base.value > 0:
                row["percent"] = round(
                    (latest.value - base.value) * 100 / base.value, 1
                )
    kind = classify_window(base_day, last, cutover, today)
    if kind != "unknown":
        row["era"] = kind
        row["era_text"] = ERA_TEXT[kind]
    return row


def _gaps(series: Series, end: date) -> dict[str, Any]:
    by_day = series.by_day()
    first = max(min(by_day), end - timedelta(days=GAP_WINDOW_DAYS - 1))
    missing = series.gaps(first, end)
    return {
        "from": first.isoformat(),
        "to": end.isoformat(),
        "count": len(missing),
        "days": [d.isoformat() for d in missing],
    }


def _one(
    platform: str,
    metric: str,
    records: list[Record],
    *,
    collected: bool,
    cutover: date | None,
    today: date,
    zone: str | None,
) -> dict[str, Any]:
    series = Series(records)
    by_day = series.by_day()
    latest_day = max(by_day)
    latest = by_day[latest_day]
    # Collected series are checked up to today (a stale series shows its missing
    # tail); an imported series ends where its import ends.
    end = max(latest_day, today) if collected else latest_day
    return {
        "platform": platform,
        "metric": metric,
        "latest": _latest(latest, cutover, today, zone),
        "stale_days": (today - latest_day).days,
        "changes": [_change(n, latest, by_day, cutover, today) for n in WINDOWS],
        "gaps": _gaps(series, end),
    }


def build_social(
    history: History,
    *,
    cutover: date | None,
    today: date,
    zone: str | None = None,
) -> dict[str, Any]:
    """The report's social block from a validated history."""
    refused: dict[tuple[str, str], None] = {}
    groups: dict[tuple[bool, str, str], list[Record]] = {}
    for record in history.records:
        if _refused(record):
            refused[(record.platform, record.metric)] = None
            continue
        collected = record.source != "imported"
        groups.setdefault((collected, record.platform, record.metric), []).append(
            record
        )
    rows: dict[bool, list[dict[str, Any]]] = {True: [], False: []}
    for (collected, platform, metric), records in sorted(groups.items()):
        rows[collected].append(
            _one(
                platform,
                metric,
                records,
                collected=collected,
                cutover=cutover,
                today=today,
                zone=zone,
            )
        )
    block: dict[str, Any] = {
        "label": COLLECTED_LABEL,
        "timezone": "UTC",
        "windows": list(WINDOWS),
        "platforms": rows[True],
        "imported": {"label": IMPORTED_LABEL, "series": rows[False]},
        "refused": [
            {"platform": p, "metric": m, "reason": REFUSED_REASON}
            for p, m in sorted(refused)
        ],
        "notes": list(NOTES),
    }
    if classify_era(today, cutover, today) != "unknown" and cutover is not None:
        block["cutover_utc"] = cutover.isoformat()
    return block


def _change_text(row: dict[str, Any]) -> str:
    head = f"{row['days']}d "
    if row["status"] == "no_record":
        text = head + f"no record on {row['baseline_day']}"
    elif row["status"] == "definition_changed":
        text = head + "withheld (the definition changed)"
    else:
        text = head + f"{row['change']:+d}"
        if row["percent"] is not None:
            text += f" ({row['percent']:+.1f}%)"
    if "era" in row:
        text += f" [{row['era_text']}]"
    return text


def _series_lines(series: dict[str, Any], lines: list[str]) -> None:
    latest = series["latest"]
    name = f"{series['platform']} {series['metric']}"
    stamp = latest["as_of_utc"]
    if "as_of_display" in latest:
        stamp = f"{latest['as_of_display']} ({stamp})"
    extra = f", {latest['era']}" if "era" in latest else ""
    mark = f"; definition: {latest['definition']}" if "definition" in latest else ""
    lines.append(
        f"  {name:<28} {latest['value']:>12}  as of {stamp} "
        f"({latest['source']}{extra}){mark}"
    )
    lines.append("    " + "; ".join(_change_text(c) for c in series["changes"]))
    gaps = series["gaps"]
    if gaps["count"]:
        shown = ", ".join(gaps["days"])
        lines.append(
            f"    {gaps['count']} gap day(s) {gaps['from']} to {gaps['to']} UTC "
            f"(not zeros): {shown}"
        )


def social_lines(block: dict[str, Any]) -> list[str]:
    """Plain-text lines for the report: collected, then imported, then notes."""
    lines = ["", block["label"]]
    if "cutover_utc" in block:
        lines.append(f"  Cutover: {block['cutover_utc']} UTC")
    if not block["platforms"]:
        lines.append("  no collected or typed numbers in the history file")
    for series in block["platforms"]:
        _series_lines(series, lines)
    for item in block["refused"]:
        lines.append(f"  refused {item['platform']} {item['metric']}: {item['reason']}")
    lines.append("")
    lines.append(block["imported"]["label"])
    if not block["imported"]["series"]:
        lines.append("  no imported history")
    for series in block["imported"]["series"]:
        _series_lines(series, lines)
    for note in block["notes"]:
        lines.append(f"  {note}")
    return lines


CSV_HEADER = (
    "block,platform,metric,latest_as_of_utc,latest_value,source,window_days,"
    "baseline_day,baseline_value,change,percent,status,window_era,gap_days"
).split(",")


def social_csv_rows(block: dict[str, Any]) -> list[list[Any]]:
    """One row per series and window; an unknown figure is an empty cell."""
    rows: list[list[Any]] = []
    for name, series_list in (
        ("collected", block["platforms"]),
        ("imported", block["imported"]["series"]),
    ):
        for s in series_list:
            for c in s["changes"]:
                rows.append(
                    [
                        name,
                        s["platform"],
                        s["metric"],
                        s["latest"]["as_of_utc"],
                        s["latest"]["value"],
                        s["latest"]["source"],
                        c["days"],
                        c["baseline_day"],
                        "" if c["baseline_value"] is None else c["baseline_value"],
                        "" if c["change"] is None else c["change"],
                        "" if c["percent"] is None else c["percent"],
                        c["status"],
                        c.get("era", ""),
                        ";".join(s["gaps"]["days"]),
                    ]
                )
    for item in block["refused"]:
        rows.append(
            [
                "refused",
                item["platform"],
                item["metric"],
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "refused",
                "",
                "",
            ]
        )
    return rows
