# SPDX-License-Identifier: Apache-2.0
"""Build and render the owner report from a validated export.

The privacy floor is label suppression, like the Worker's small-cell fold: a
row under ``floor`` views is never printed (default and minimum 5), and the
rows under it are merged into one "below the floor" line only when together
they reach the floor. It is not k-anonymity. A reader who has two tables can
still subtract one total from another, so share the report, not the export.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from reach_around.funnel.export import CLASSES, Export

FLOOR = 5
# Report section -> the ``dim`` value in view_dim_daily it is built from.
DEVICE_SECTIONS = {"devices": "device", "browsers": "browser", "systems": "os"}
REPORT_VERSION = 1
CLASS_NAMES = {
    "h": "likely human",
    "v": "verified crawler (claimed)",
    "a": "automated",
    "s": "suspicious",
}


@dataclass(frozen=True)
class Floored:
    shown: list[tuple[str, int]] = field(default_factory=list)
    other: int = 0  # merged views of hidden rows; 0 unless they reach the floor
    hidden_rows: int = 0
    dropped: bool = False  # a remainder could not be made safe

    @property
    def withheld(self) -> bool:
        return not self.shown and self.other == 0


def _suppress(
    cells: Mapping[str, int], floor: int, pinned: frozenset[str] = frozenset()
) -> Floored:
    """Keep rows of at least ``floor`` views, largest first; merge the rest.

    The rows plus the merged line add up to a total that is printed elsewhere,
    so the merged line must not reveal a cell. Whenever any row is under the
    floor, the smallest shown row joins the merged line, always. That makes the
    remainder at least the floor (subtracting the shown rows from the total
    cannot isolate a small cell) and it removes the upper bound that a group of
    only small cells has (a remainder of members times floor-minus-one would
    pin every member). The rule never depends on the values, so the shape of
    the output tells a reader nothing about them. With nothing to join, the
    remainder is dropped and ``dropped`` is set.

    Labels in ``pinned`` are printed exactly somewhere else (the funnel prints
    the human class), so they never join the merged line.
    """
    items = sorted(
        ((label, views) for label, views in cells.items() if views > 0),
        key=lambda item: (-item[1], item[0]),
    )
    shown = [item for item in items if item[1] >= floor]
    hidden = [item for item in items if item[1] < floor]
    if not hidden:
        return Floored(shown, 0, 0)
    movable = [item for item in shown if item[0] not in pinned]
    if not movable:
        return Floored(shown, 0, len(hidden), dropped=True)
    pick = movable[0]
    shown.remove(pick)
    hidden.append(pick)
    return Floored(shown, sum(views for _, views in hidden), len(hidden))


def floor_cells(cells: Mapping[str, int], floor: int) -> Floored:
    return _suppress(cells, floor)


def _floor_classes(cells: Mapping[str, int], floor: int) -> Floored:
    """``floor_cells`` for the four bot classes. The human class is printed on
    its own in the funnel, so it never joins the merged line (or the funnel
    figure would undo the merge). When the remainder cannot be made safe without
    it, the class total and shares are withheld."""
    return _suppress(cells, floor, frozenset({"h"}))


def _count(n: int, floor: int) -> int | None:
    """A count that may be printed: zero, or at least the floor."""
    return n if n == 0 or n >= floor else None


def _utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, UTC).date().isoformat()


def _in_window(day: str, since: str | None, until: str | None) -> bool:
    return (since is None or day >= since) and (until is None or day <= until)


def _withhold_views(report: dict[str, Any]) -> None:
    """Withhold every figure derived from the page-view population.

    Used when a table over those views had a small remainder that no merge could
    make safe. The views are printed as a total in the funnel, the class table
    and every other cut, so a remainder left unsafe in one cut can be recovered
    from another. Clicks, the kit and the bot reasons stay.
    """
    funnel = report["funnel"]
    funnel["page_views"] = None
    funnel["clicks_per_100_views"] = None
    funnel["kit_views_per_100_views"] = None
    if "view_daily" not in funnel["missing"] and "page_views" not in funnel["withheld"]:
        funnel["withheld"].append("page_views")
    for key in ("pages", "sources", "geography", *DEVICE_SECTIONS):
        if report[key]["state"] != "not_in_export":
            report[key].update(
                state="withheld", rows=[], other_below_floor=0, more_rows=0
            )
    bots = report["bots"]
    bots.update(classes=[], other_below_floor=0, total_views=None, views_withheld=True)


def _section(
    cells: Mapping[str, int], floor: int, top: int, present: bool
) -> dict[str, Any]:
    if not present:
        return {
            "state": "not_in_export",
            "rows": [],
            "other_below_floor": 0,
            "more_rows": 0,
        }
    result = floor_cells(cells, floor)
    return {
        "state": "withheld" if result.withheld and cells else "ok",
        "rows": [
            {"label": label, "views": views} for label, views in result.shown[:top]
        ],
        "other_below_floor": result.other,
        "more_rows": max(0, len(result.shown) - top),
    }


def _joint_click_sources(
    by_link: Mapping[str, Mapping[str, int]],
    links_section: Mapping[str, Any],
    floor: int,
    top: int,
    present: bool,
) -> dict[str, Any]:
    """Source -> link cells, suppressed jointly with the per-link section.

    The two sections share a population (the clicks), so a cell hidden in one
    must not be recoverable from the other. A link's whole breakdown is printed
    or none of it: it is printed only when every one of its cells is at least
    the floor and the link's own total is a printed row of the links section.
    A link with any small cell prints its total (or its place in the merged
    line) there and no breakdown here. Truncation by ``top`` also drops whole
    links, never single cells.
    """
    if not present:
        return {
            "state": "not_in_export",
            "rows": [],
            "other_below_floor": 0,
            "more_rows": 0,
            "links_withheld": 0,
        }
    printed = [row["label"] for row in links_section["rows"]]
    rows: list[dict[str, Any]] = []
    shown_links = 0
    more = 0
    for link in printed:
        cells = by_link.get(link, {})
        if not cells or any(n < floor for n in cells.values()):
            continue
        if shown_links >= top:
            more += len(cells)
            continue
        shown_links += 1
        rows += [
            {"label": f"{src} -> {link}", "views": n}
            for src, n in sorted(cells.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
    printable = {
        link
        for link in printed
        if by_link.get(link) and all(n >= floor for n in by_link[link].values())
    }
    withheld = sum(1 for link, cells in by_link.items() if cells) - len(printable)
    return {
        "state": "withheld" if by_link and not rows and not more else "ok",
        "rows": rows,
        "other_below_floor": 0,
        "more_rows": more,
        "links_withheld": withheld,
    }


def _source_label(src_kind: str, src: str) -> str:
    if src_kind == "d":
        return "(direct)"
    if src_kind == "i":
        return "(internal)"
    if src_kind == "u":
        return f"utm:{src}"
    if src_kind == "o":
        return "(other)"
    return src or "(unknown)"


def _geo_label(country: str, region: str, city: str, level: str) -> str:
    parts = [country]
    if level in ("region", "city"):
        parts.append(region)
    if level == "city":
        parts.append(city)
    out: list[str] = []
    for part in parts:
        if part:
            out.append(part)
        else:
            out.append("other")
            break
    return " / ".join(out)


def _ratio(num: int | None, den: int | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return round(num * 100 / den, 1)


def build_report(
    export: Export,
    *,
    site: str = "links",
    since: str | None = None,
    until: str | None = None,
    days: int | None = None,
    geo: str = "country",
    include_bots: bool = False,
    top: int = 10,
    floor: int = FLOOR,
) -> dict[str, Any]:
    if floor < FLOOR:
        raise ValueError(f"the floor cannot be below {FLOOR}")
    view_rows = [r for r in export.view_daily if r["site"] == site]
    dim_rows = [r for r in export.view_dim_daily if r["site"] == site]
    geo_rows = [r for r in export.view_geo_daily if r["site"] == site]
    click_rows = [
        {**r, "day": _utc_day(r["ts"])}
        for r in export.click_events
        if r.get("site", "links") == site
    ]
    # Kit rows with a site belong to it. Rows without one (the database has no
    # kit site) are counted for the default site only; see ``not_scoped``.
    kit_rows = [
        {**r, "day": _utc_day(r["ts"])}
        for r in export.kit_events
        if r.get("site") == site or ("site" not in r and site == "links")
    ]
    kit_unscoped = site != "links" and any("site" not in r for r in export.kit_events)

    if days is not None:
        every = [
            r["day"] for r in view_rows + dim_rows + geo_rows + click_rows + kit_rows
        ]
        if every:
            end = date.fromisoformat(until) if until else date.fromisoformat(max(every))
            since = (end - timedelta(days=days - 1)).isoformat()
            until = end.isoformat()

    def window(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return [r for r in rows if _in_window(r["day"], since, until)]

    view_rows, dim_rows, geo_rows = (
        window(view_rows),
        window(dim_rows),
        window(geo_rows),
    )
    click_rows, kit_rows = window(click_rows), window(kit_rows)
    seen_days = sorted(
        {r["day"] for r in view_rows + dim_rows + geo_rows + click_rows + kit_rows}
    )
    wanted = set(CLASSES) if include_bots else {"h"}

    # bot share: every class, regardless of the page tables' class filter
    by_class: dict[str, int] = defaultdict(int)
    for r in view_rows:
        by_class[r["bot_class"]] += r["views"]
    total_views = sum(by_class.values())
    classes = _floor_classes({c: by_class[c] for c in CLASSES if by_class[c]}, floor)
    total_printable = not classes.hidden_rows or classes.other > 0
    reasons: dict[str, int] = defaultdict(int)
    for r in dim_rows:
        if r["dim"] == "reason" and r["bot_class"] != "h":
            reasons[r["value"]] += r["views"]

    cuts: dict[str, dict[str, int]] = {
        name: defaultdict(int) for name in DEVICE_SECTIONS
    }
    for r in dim_rows:
        for name, dim_name in DEVICE_SECTIONS.items():
            if r["dim"] == dim_name and r["bot_class"] in wanted:
                cuts[name][r["value"]] += r["views"]

    pages: dict[str, int] = defaultdict(int)
    sources: dict[str, int] = defaultdict(int)
    for r in view_rows:
        if r["bot_class"] in wanted:
            pages[r["page"]] += r["views"]
            sources[_source_label(r["src_kind"], r["src"])] += r["views"]
    places: dict[str, int] = defaultdict(int)
    for r in geo_rows:
        if r["bot_class"] in wanted:
            places[_geo_label(r["country"], r["region"], r["city"], geo)] += r["views"]
    links: dict[str, int] = defaultdict(int)
    click_sources: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in click_rows:
        links[r["link_id"]] += 1
        click_sources[r["link_id"]][r.get("src") or "none"] += 1
    kit: dict[str, int] = defaultdict(int)
    for r in kit_rows:
        kit[r["kind"]] += 1

    human = by_class["h"]
    missing = [
        t
        for t in (
            "view_daily",
            "view_geo_daily",
            "view_dim_daily",
            "click_events",
            "kit_events",
        )
        if t not in export.present
    ]
    page_views = _count(human, floor) if "view_daily" in export.present else None
    click_total = (
        _count(len(click_rows), floor) if "click_events" in export.present else None
    )
    kit_views = _count(kit["view"], floor) if "kit_events" in export.present else None
    kit_unlocks = (
        _count(kit["unlock"], floor) if "kit_events" in export.present else None
    )
    not_scoped: list[str] = []
    if kit_unscoped and "kit_events" in export.present:
        kit_views = kit_unlocks = None
        not_scoped = ["kit_views", "kit_unlocks"]
    withheld = []
    if "view_daily" in export.present and page_views is None and human:
        withheld.append("page_views")
    if "click_events" in export.present and click_total is None:
        withheld.append("clicks")
    if "kit_events" in export.present and not not_scoped:
        if kit_views is None:
            withheld.append("kit_views")
        if kit_unlocks is None:
            withheld.append("kit_unlocks")

    links_section = _section(links, floor, top, "click_events" in export.present)
    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "site": site,
        "floor": floor,
        "period": {
            "since": seen_days[0] if seen_days else None,
            "until": seen_days[-1] if seen_days else None,
            "days_with_data": len(seen_days),
        },
        "counted_classes": sorted(wanted),
        "funnel": {
            "page_views": page_views,
            "clicks": click_total,
            "kit_views": kit_views,
            "kit_unlocks": kit_unlocks,
            "clicks_per_100_views": _ratio(click_total, page_views),
            "kit_views_per_100_views": _ratio(kit_views, page_views),
            "withheld": withheld,
            "not_scoped": not_scoped,
            "missing": missing,
        },
        "pages": _section(pages, floor, top, "view_daily" in export.present),
        "sources": _section(sources, floor, top, "view_daily" in export.present),
        "geography": {
            "level": geo,
            **_section(places, floor, top, "view_geo_daily" in export.present),
        },
        "links": links_section,
        "click_sources": {},  # joint suppression with "links", filled below
        **{
            name: _section(cuts[name], floor, top, "view_dim_daily" in export.present)
            for name in DEVICE_SECTIONS
        },
        "bots": {
            "total_views": (_count(total_views, floor) if total_printable else None),
            "classes": [
                {
                    "class": label,
                    "name": CLASS_NAMES[label],
                    "views": views,
                    "share": (
                        round(views * 100 / total_views, 1) if total_printable else None
                    ),
                }
                for label, views in classes.shown
            ],
            "other_below_floor": classes.other,
            "reasons": _section(
                reasons, floor, top, "view_dim_daily" in export.present
            ),
            "human_views_seen": bool(human),
        },
    }
    report["click_sources"] = _joint_click_sources(
        click_sources, links_section, floor, top, "click_events" in export.present
    )
    unsafe = any(
        _suppress(cells, floor).dropped
        for cells in (pages, sources, places, *cuts.values())
    ) or (include_bots and classes.dropped)
    if unsafe:
        _withhold_views(report)
    return report


# ---- text -----------------------------------------------------------------


def _fmt(value: int | float | None, state: str = "below the privacy floor") -> str:
    return state if value is None else str(value)


def _table(title: str, section: dict[str, Any], lines: list[str]) -> None:
    lines.append("")
    lines.append(title)
    state = section["state"]
    if state == "not_in_export":
        lines.append("  not in this export")
        return
    if state == "withheld":
        lines.append("  every row is below the privacy floor; nothing shown")
        return
    if not section["rows"] and not section["other_below_floor"]:
        lines.append("  no data in this period")
        return
    width = max([len(r["label"]) for r in section["rows"]] + [8])
    for row in section["rows"]:
        lines.append(f"  {row['label']:<{width}}  {row['views']:>8}")
    if section["more_rows"]:
        lines.append(f"  ... {section['more_rows']} more rows (raise --top)")
    if section["other_below_floor"]:
        lines.append(
            f"  {'(rows below the floor)':<{width}}  {section['other_below_floor']:>8}"
        )


def render_text(report: dict[str, Any]) -> str:
    period = report["period"]
    funnel = report["funnel"]
    floor = report["floor"]
    lines: list[str] = []
    if period["since"] is None:
        span = "no data in this period"
    else:
        span = (
            f"{period['since']} to {period['until']} UTC, "
            f"{period['days_with_data']} days with data"
        )
    lines.append(f"Reach Around report: site {report['site']}, {span}")
    era = report.get("era")
    if era:
        lines.append(
            f"Cutover: {era['cutover_utc']} UTC; this window is {era['text']}."
        )
    display = report.get("display")
    if display and display["window_start"]:
        lines.append(
            f"Display zone {display['zone']}: window runs from "
            f"{display['window_start']} "
            f"to {display['window_end_exclusive']} (end excluded). {display['note']}"
        )
    lines.append(
        f"Privacy floor: no row under {floor} views is shown (see docs/report.md)."
    )
    lines.append("")
    lines.append("Funnel")
    steps = [
        ("Page views (likely human)", "page_views", "view_daily"),
        ("Link clicks", "clicks", "click_events"),
        ("Kit views", "kit_views", "kit_events"),
        ("Kit unlocks", "kit_unlocks", "kit_events"),
    ]
    for label, key, table in steps:
        if table in funnel["missing"]:
            text = "not in this export"
        else:
            text = _fmt(funnel[key])
        if key in funnel["not_scoped"]:
            text = "not available for this site"
        lines.append(f"  {label:<28} {text}")
    per = funnel["clicks_per_100_views"]
    if per is not None:
        lines.append(f"  Clicks per 100 views: {per} (a ratio, not a conversion rate)")
        if per > 100:
            lines.append(
                "  Note: more than 100 per 100 views; some visitors click without "
                "the page-view script running."
            )
    kper = funnel["kit_views_per_100_views"]
    if kper is not None:
        lines.append(f"  Kit views per 100 page views: {kper}")
    if report["bots"].get("views_withheld"):
        lines.append(
            "  Page-view figures are withheld: a small remainder could be "
            "recovered from them."
        )
    if funnel["not_scoped"]:
        lines.append(
            "  Kit figures cannot be scoped by site: the kit has no site column "
            "in the export. Add a site column to kit_events to see them."
        )
    if funnel["withheld"]:
        lines.append("  A figure marked below the privacy floor is not printed.")
    lines.append(
        "  Clicks are counts, not people; a click's source is the label on the "
        "link it came in on."
    )

    classes = ", ".join(report["counted_classes"])
    _table(f"Pages (classes: {classes})", report["pages"], lines)
    _table(f"Sources (classes: {classes})", report["sources"], lines)
    geo = report["geography"]
    _table(f"Geography by {geo['level']} (classes: {classes})", geo, lines)
    _table("Link clicks by link", report["links"], lines)
    _table("Link clicks by source -> link", report["click_sources"], lines)
    if report["click_sources"].get("links_withheld"):
        lines.append(
            "  A link's breakdown is shown only when every part is at or above the "
            "floor; other links show their total above."
        )
    _table(f"Device classes (classes: {classes})", report["devices"], lines)
    _table(f"Browsers (classes: {classes})", report["browsers"], lines)
    _table(f"Operating systems (classes: {classes})", report["systems"], lines)

    bots = report["bots"]
    lines.append("")
    lines.append("Bots (share of all recorded page views)")
    if "view_daily" in funnel["missing"]:
        lines.append("  not in this export")
    elif not bots["classes"] and not bots["other_below_floor"]:
        lines.append("  no data or every class is below the privacy floor")
    else:
        for row in bots["classes"]:
            share = "" if row["share"] is None else f"  {row['share']:>5}%"
            lines.append(f"  {row['name']:<28} {row['views']:>8}{share}")
        if bots["other_below_floor"]:
            lines.append(
                f"  {'(classes below the floor)':<28} {bots['other_below_floor']:>8}"
            )
    _table("Bot reasons (not human-likely)", bots["reasons"], lines)
    lines.append("")
    lines.append(
        "Human-likely is an estimate; crawlers that never run the script are "
        "invisible to page views."
    )
    return "\n".join(lines) + "\n"
