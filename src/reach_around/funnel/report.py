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


DIRECT = "direct"
FOLDED = "other"
UNKNOWN_COUNTRY = "ZZ"  # the Worker's label for an unknown or folded country
HIGH_DIRECT_SHARE = 50.0  # percent of shown clicks that carry no source label


DAILY_KEEP_DAYS = 90  # the Worker keeps day cells this many whole UTC days


def period_bounds(period: str, offset: int, as_of: date) -> tuple[date, date]:
    """First and last UTC day of a releasable calendar week (Monday start) or month.

    One kind of period per stretch of time, so a week and a month never cover
    the same day. Let B be the oldest day that still has day cells
    (``as_of`` minus 89 days).

    * weeks: only complete weeks that lie wholly on or after B (day cells
      exist for all seven days). Offset 0 is the latest complete week.
    * months: only months that lie wholly before B (their day cells are gone,
      the monthly rollup row is the record). Offset 0 is the latest such month.

    A week that crosses B and a month that crosses B are never released, and a
    request that would reach into the other kind's range is refused.
    """
    if offset < 0:
        raise ValueError("--offset cannot be negative")
    oldest = as_of - timedelta(days=DAILY_KEEP_DAYS - 1)
    if period == "week":
        monday = as_of - timedelta(days=as_of.weekday())
        start = monday - timedelta(days=7 * (offset + 1))
        if start < oldest:
            raise ValueError(
                "no complete week that far back: day cells exist for the last "
                f"{DAILY_KEEP_DAYS} days only, so weeks are released inside that "
                "range and a week that crosses its edge is withheld; use "
                "--period month for older time"
            )
        return start, start + timedelta(days=6)
    if period == "month":
        newest_end = oldest.replace(day=1) - timedelta(days=1)
        index = newest_end.year * 12 + newest_end.month - 1 - offset
        if index < 0:
            raise ValueError("--offset is too large")
        start = date(index // 12, index % 12 + 1, 1)
        return start, _next_month(start) - timedelta(days=1)
    raise ValueError("--period must be week or month")


def _next_month(first: date) -> date:
    return date(first.year + (first.month == 12), first.month % 12 + 1, 1)


def _month_span(month: str) -> tuple[str, str]:
    year, mon = int(month[:4]), int(month[5:])
    first = date(year, mon, 1)
    nxt = date(year + (mon == 12), mon % 12 + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def _funnel_cells(
    daily: Iterable[dict[str, Any]],
    monthly: Iterable[dict[str, Any]],
    label_key: str,
    site: str,
    since: str | None,
    until: str | None,
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Link -> label -> clicks from the funnel counters in the window.

    A month that lies wholly inside the window and has a monthly row is read
    from the monthly table (the day cells of a month go 90 days after it
    closes, so the monthly rows are the complete record). Every other day is
    read from the day cells. A month is never read both ways, so nothing is
    counted twice. The floor is applied by the caller, after this sum.
    """
    monthly_rows = [r for r in monthly if r["site"] == site]
    use: set[str] = set()
    for month in {r["month"] for r in monthly_rows}:
        first, last = _month_span(month)
        if (since is None or first >= since) and (until is None or last <= until):
            use.add(month)
    cells: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in monthly_rows:
        if r["month"] in use:
            cells[r["link_id"]][r[label_key]] += r["clicks"]
    for r in daily:
        if r["site"] != site or r["day"][:7] in use:
            continue
        if _in_window(r["day"], since, until):
            cells[r["link_id"]][r[label_key]] += r["clicks"]
    return cells, sorted(use)


REMAINDER = "(below the floor)"
OVERFLOW_LINK = "overflow-all"  # the Worker's one overflow destination per day


def _fold(cells: Mapping[str, int], floor: int) -> Floored:
    """Cells at the floor print; the rest merge into one remainder that bounds nothing.

    Cells under the floor go into the remainder. Printed cells are listed in
    order, and the remainder is R = (hidden cells) + (moved cells). A reader
    knows every moved cell is at least the largest printed cell (and at least
    the floor), so the hidden sum is at most R minus that number. At least one
    printed cell always moves (the reader cannot rule a move out); then the
    rule is to move more of the largest printed cells until

        R - max(largest printed cell left, floor) >= floor - 1

    i.e. until that bound is no tighter than "each hidden cell is under the
    floor", which a reader knows anyway. With nothing left to move and the
    condition still false, nothing of the destination prints (``dropped``).
    """
    items = sorted(
        ((label, n) for label, n in cells.items() if n > 0),
        key=lambda item: (-item[1], item[0]),
    )
    shown = [item for item in items if item[1] >= floor]
    hidden = sum(n for _, n in items if n < floor)
    if not hidden:
        return Floored(shown, 0, 0)
    remainder = hidden
    hidden_rows = sum(1 for _, n in items if n < floor)
    if shown:  # a printed cell always joins, so a reader cannot rule a move out
        remainder += shown.pop(0)[1]
        hidden_rows += 1
    while True:
        biggest = max([n for _, n in shown] + [floor])
        if remainder - biggest >= floor - 1:
            break
        if not shown:
            return Floored([], 0, hidden_rows, dropped=True)
        _, n = shown.pop(0)
        remainder += n
        hidden_rows += 1
    return Floored(shown, remainder, hidden_rows)


def _link_breakdown(
    by_link: Mapping[str, Mapping[str, int]],
    printed: list[str] | None,
    floor: int,
    top: int,
    present: bool,
    months: list[str],
    released: bool = True,
) -> dict[str, Any]:
    """Per-destination breakdowns for a marginal pair (label x destination).

    Within a destination, every cell under the floor goes into one protected
    remainder line, and a visitor who creates one tiny cell cannot hide the
    rest. The remainder is printed only when it reaches the floor; when any cell
    is under the floor the smallest printed cell joins it, so the remainder
    always reaches the floor or nothing of it is printed (``_suppress``). A
    destination with only small cells prints nothing. If a per-link table is
    printed beside it (``printed``), a destination prints only when it is one of
    its printed rows (the overflow destination, a sum over links, is exempt).
    Totals are sums of printed cells and the remainder only, so no withheld cell
    can be recovered from a printed number. Truncation by ``top`` drops whole
    destinations, never single cells.
    """
    empty: dict[str, Any] = {
        "months_used": months,
        "destinations": [],
        "cells": [],
        "label_totals": [],
        "links_withheld": 0,
        "more_links": 0,
    }
    if not present:
        return {"state": "not_in_export", **empty}
    if not released:
        return {"state": "period_required", **empty}
    results: dict[str, Floored] = {}
    for link, cells in by_link.items():
        if not cells or (
            printed is not None and link not in printed and link != OVERFLOW_LINK
        ):
            continue
        result = _fold(cells, floor)
        if result.dropped or (not result.shown and not result.other):
            continue
        results[link] = result

    def total(link: str) -> int:
        return sum(n for _, n in results[link].shown) + results[link].other

    order = sorted(results, key=lambda link: (-total(link), link))
    shown = order[:top]
    label_totals: dict[str, int] = defaultdict(int)
    cell_rows: list[dict[str, Any]] = []
    for link in shown:
        for label, n in results[link].shown:
            cell_rows.append({"label": label, "destination": link, "clicks": n})
            label_totals[label] += n
        if results[link].other:
            cell_rows.append(
                {
                    "label": REMAINDER,
                    "destination": link,
                    "clicks": results[link].other,
                }
            )
    withheld = sum(1 for cells in by_link.values() if cells) - len(results)
    state = "ok"
    if any(by_link.values()) and not cell_rows and not len(order) > top:
        state = "withheld"
    return {
        "state": state,
        "months_used": months,
        "destinations": [
            {"destination": link, "clicks": total(link)} for link in shown
        ],
        "cells": cell_rows,
        "label_totals": [
            {"label": label, "clicks": n}
            for label, n in sorted(label_totals.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "links_withheld": withheld,
        "more_links": max(0, len(order) - top),
    }


def _shown_total(section: Mapping[str, Any]) -> int:
    return sum(cell["clicks"] for cell in section["cells"])


def _matrix_section(section: dict[str, Any], top: int) -> dict[str, Any]:
    """The source by destination matrix plus the ranked flow list."""
    shown = _shown_total(section)
    cells = [
        {"source": c["label"], "destination": c["destination"], "clicks": c["clicks"]}
        for c in section["cells"]
    ]
    flows = [c for c in cells if c["source"] != REMAINDER]
    ranked = sorted(flows, key=lambda c: (-c["clicks"], c["source"], c["destination"]))
    ranked_rows = [
        {
            "label": f"{c['source']} -> {c['destination']}",
            "clicks": c["clicks"],
            "share": round(c["clicks"] * 100 / shown, 1),
        }
        for c in ranked[:top]
    ]
    rest = sum(c["clicks"] for c in ranked[top:])
    direct = sum(c["clicks"] for c in flows if c["source"] == DIRECT)
    direct_share = round(direct * 100 / shown, 1) if shown else None
    out = {k: v for k, v in section.items() if k not in ("cells", "label_totals")}
    out.update(
        sources=[
            {"source": t["label"], "clicks": t["clicks"]}
            for t in section["label_totals"]
        ]
        + (
            [{"source": REMAINDER, "clicks": rem}]
            if (rem := sum(c["clicks"] for c in cells if c["source"] == REMAINDER))
            else []
        ),
        cells=cells,
        ranked=ranked_rows,
        ranked_other=rest,
        ranked_more_rows=max(0, len(ranked) - top),
        shown_clicks=shown,
        direct_share_percent=direct_share,
        direct_share_high=bool(
            direct_share is not None and direct_share >= HIGH_DIRECT_SHARE
        ),
    )
    return out


def _country_section(section: dict[str, Any], top: int) -> dict[str, Any]:
    """Country by destination: the top countries, the rest folded per destination."""
    totals = section["label_totals"]
    keep = [t["label"] for t in totals if t["label"] != UNKNOWN_COUNTRY][:top]
    folded: dict[str, int] = defaultdict(int)
    cells: list[dict[str, Any]] = []
    remainder = 0
    for c in section["cells"]:
        if c["label"] == REMAINDER:
            cells.append(
                {
                    "country": REMAINDER,
                    "destination": c["destination"],
                    "clicks": c["clicks"],
                }
            )
            remainder += c["clicks"]
        elif c["label"] in keep:
            cells.append(
                {
                    "country": c["label"],
                    "destination": c["destination"],
                    "clicks": c["clicks"],
                }
            )
        else:
            folded[c["destination"]] += c["clicks"]
    for link in section["destinations"]:
        if folded.get(link["destination"]):
            cells.append(
                {
                    "country": FOLDED,
                    "destination": link["destination"],
                    "clicks": folded[link["destination"]],
                }
            )
    out = {k: v for k, v in section.items() if k not in ("cells", "label_totals")}
    out.update(
        countries=[
            {
                "country": t["label"] if t["label"] != UNKNOWN_COUNTRY else FOLDED,
                "clicks": t["clicks"],
            }
            for t in totals
            if t["label"] in keep
        ]
        + ([{"country": FOLDED, "clicks": sum(folded.values())}] if folded else [])
        + ([{"country": REMAINDER, "clicks": remainder}] if remainder else []),
        cells=cells,
        more_countries=max(
            0,
            len(totals)
            - len(keep)
            - sum(1 for t in totals if t["label"] == UNKNOWN_COUNTRY),
        ),
        shown_clicks=_shown_total(section),
    )
    return out


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
    period: str | None = None,
    offset: int = 0,
    as_of: date | None = None,
) -> dict[str, Any]:
    if floor < FLOOR:
        raise ValueError(f"the floor cannot be below {FLOOR}")
    release: dict[str, Any] = {
        "period": None,
        "offset": offset,
        "start": None,
        "end": None,
    }
    if period is not None:
        if since is not None or until is not None or days is not None:
            raise ValueError(
                "--period cannot be combined with --since, --until or --days: "
                "the source and country tables are released for one fixed "
                "calendar week or month only"
            )
        start, end = period_bounds(period, offset, as_of or datetime.now(UTC).date())
        since, until = start.isoformat(), end.isoformat()
        release = {
            "period": period,
            "offset": offset,
            "start": since,
            "end": until,
        }
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

    counter_days = [
        r["day"]
        for r in (*export.click_src_daily, *export.click_country_daily)
        if r["site"] == site
    ]
    if days is not None:
        every = [
            r["day"] for r in view_rows + dim_rows + geo_rows + click_rows + kit_rows
        ] + counter_days
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
        | {d for d in counter_days if _in_window(d, since, until)}
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
    printed_links = (
        [row["label"] for row in links_section["rows"]]
        if "click_events" in export.present
        else None
    )
    src_cells, src_months = _funnel_cells(
        export.click_src_daily, export.click_src_monthly, "src", site, since, until
    )
    country_cells, country_months = _funnel_cells(
        export.click_country_daily,
        export.click_country_monthly,
        "country",
        site,
        since,
        until,
    )
    matrix = _matrix_section(
        _link_breakdown(
            src_cells,
            printed_links,
            floor,
            top,
            bool({"click_src_daily", "click_src_monthly"} & export.present),
            src_months,
            released=period is not None,
        ),
        top,
    )
    countries = _country_section(
        _link_breakdown(
            country_cells,
            printed_links,
            floor,
            top,
            bool({"click_country_daily", "click_country_monthly"} & export.present),
            country_months,
            released=period is not None,
        ),
        top,
    )
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
        "release": release,
        "click_matrix": matrix,
        "click_countries": countries,
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


def _grid(header: list[str], rows: list[list[str]], lines: list[str]) -> None:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    for n, row in enumerate([header, *rows]):
        cells = [row[0].ljust(widths[0])] + [
            row[i].rjust(widths[i]) for i in range(1, len(row))
        ]
        lines.append("  " + "  ".join(cells))
        if n == 0:
            lines.append("  " + "  ".join("-" * w for w in widths))


def _funnel_notes(section: dict[str, Any], lines: list[str]) -> None:
    if section["months_used"]:
        lines.append(
            "  Whole months read from the monthly rollup: "
            + ", ".join(section["months_used"])
            + "."
        )
    if section["links_withheld"]:
        lines.append(
            "  A destination with nothing at or above the floor is not shown "
            f"({section['links_withheld']}). Cells under the floor are merged "
            "into one line per destination only when together they reach it."
        )
    if section["more_links"]:
        lines.append(f"  ... {section['more_links']} more destinations (raise --top)")


def _funnel_sections(report: dict[str, Any], lines: list[str]) -> None:
    """The marginal-pair sections: source -> destination and country -> destination."""
    matrix = report["click_matrix"]
    lines.append("")
    rel = report["release"]
    if rel["period"] is not None:
        lines.append(
            f"Released period for the source and country tables: {rel['period']} "
            f"{rel['start']} to {rel['end']} UTC"
        )
    lines.append("Clicks by source and destination")
    if matrix["state"] == "period_required":
        lines.append(
            "  not released for a free window; use --period week|month "
            "(whole UTC calendar periods only)"
        )
    elif matrix["state"] == "not_in_export":
        lines.append("  not in this export")
    elif matrix["state"] == "withheld" or not matrix["cells"]:
        lines.append(
            "  every cell is below the privacy floor; nothing shown"
            if matrix["state"] == "withheld"
            else "  no data in this period"
        )
    else:
        dests = [d["destination"] for d in matrix["destinations"]]
        by = {(c["source"], c["destination"]): c["clicks"] for c in matrix["cells"]}
        rows = [
            [t["source"]]
            + [str(by.get((t["source"], d), 0)) for d in dests]
            + [str(t["clicks"])]
            for t in matrix["sources"]
        ]
        rows.append(
            ["total (shown)"]
            + [str(d["clicks"]) for d in matrix["destinations"]]
            + [str(matrix["shown_clicks"])]
        )
        _grid(["source", *dests, "total"], rows, lines)
        _funnel_notes(matrix, lines)
        if matrix["direct_share_high"]:
            lines.append(
                f"  Note: {matrix['direct_share_percent']}% of the clicks shown carry "
                "no source label (direct); label the links you share to see more."
            )
        lines.append("")
        lines.append("Top flows (share of the clicks shown above, never a conversion)")
        for row in matrix["ranked"]:
            lines.append(f"  {row['label']:<40} {row['clicks']:>8}  ({row['share']}%)")
        if matrix["ranked_other"]:
            lines.append(f"  {'other':<40} {matrix['ranked_other']:>8}")
    lines.append(
        "  Counts clicks, not people. A source is the label on the link the "
        "visitor came in on; no click is tied to a view or a person."
    )
    countries = report["click_countries"]
    lines.append("")
    lines.append("Clicks by country and destination")
    if countries["state"] == "period_required":
        lines.append(
            "  not released for a free window; use --period week|month "
            "(whole UTC calendar periods only)"
        )
    elif countries["state"] == "not_in_export":
        lines.append("  not in this export")
    elif countries["state"] == "withheld" or not countries["cells"]:
        lines.append(
            "  every cell is below the privacy floor; nothing shown"
            if countries["state"] == "withheld"
            else "  no data in this period"
        )
    else:
        dests = [d["destination"] for d in countries["destinations"]]
        by = {(c["country"], c["destination"]): c["clicks"] for c in countries["cells"]}
        rows = [
            [t["country"]]
            + [str(by.get((t["country"], d), 0)) for d in dests]
            + [str(t["clicks"])]
            for t in countries["countries"]
        ]
        rows.append(
            ["total (shown)"]
            + [str(d["clicks"]) for d in countries["destinations"]]
            + [str(countries["shown_clicks"])]
        )
        _grid(["country", *dests, "total"], rows, lines)
        _funnel_notes(countries, lines)
        if countries["more_countries"]:
            lines.append(
                f"  ... {countries['more_countries']} more countries folded into other "
                "(raise --top)"
            )
    lines.append(
        "  Counts clicks, not people. Country codes only, from the click request, "
        "for every click whether or not it carried a source; other is unknown "
        "or folded."
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
    _funnel_sections(report, lines)
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
