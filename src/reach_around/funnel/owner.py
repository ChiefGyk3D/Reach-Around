# SPDX-License-Identifier: Apache-2.0
"""The owner report view (#438): one derived model for the HTML page and CSV.

``owner_view`` takes the finished, floored report dictionary (the output of
``reach_around.funnel.release.release_report`` or ``build_report``) and nothing
else. It reads no export and no raw table, so it can print no figure that the
floored report does not already hold. Every number it adds is arithmetic on two
printed numbers (a click rate, a change against the previous window, a bar
width), never a new cut of the data.

Click rate is defined once, here: clicks divided by page views, same window and
same bot class filter (likely human page views), computed only when both counts
are printable under the floor. A value above 100 is never printed as such: the
shown value is capped at 100 and flagged, and the raw counts stay beside it.
The per-link figure is labeled "clicks per 100 views of the page" because a
link has no views of its own.
"""

from __future__ import annotations

from typing import Any

RATE_CAP = 100.0
CAPPED_NOTE = "capped at 100: clicks exceed views in this cut"
RATE_LABEL = "clicks per 100 page views"
LINK_RATE_LABEL = "clicks per 100 views of the page"
FOLDED_COUNTRY = "other"
UNKNOWN_NOTE = "unknown or folded (no country recorded)"
NOT_YET = {
    "sources": "Clicks and click rate by source need source-attributed clicks "
    "for the same population as the views; the released clicks by "
    "source are in the table beside it.",
    "devices": "Clicks and click rate by device, browser and system are not "
    "collected for the same cut as views yet.",
    "countries": "Click rate by country needs clicks and views for the same "
    "cut; the released clicks by country are in the table beside it.",
}


def rate(clicks: int | None, views: int | None) -> dict[str, Any]:
    """Click rate for two printable counts, or an empty rate."""
    if clicks is None or views is None or views <= 0:
        return {"value": None, "capped": False}
    raw = clicks * 100 / views
    if raw > RATE_CAP:
        return {"value": RATE_CAP, "capped": True}
    return {"value": round(raw, 1), "capped": False}


def _change(now: int | float | None, before: int | float | None) -> float | None:
    """Percent change between two printable figures; None unless both exist."""
    if now is None or before is None or before <= 0:
        return None
    return round((now - before) * 100 / before, 1)


def _state(report: dict[str, Any], table: str, key: str) -> str:
    funnel = report["funnel"]
    if table in funnel["missing"]:
        return "not_in_export"
    if funnel[key] is None:
        return "below_floor"
    return "ok"


def _tiles(report: dict[str, Any]) -> dict[str, Any]:
    funnel = report["funnel"]
    prev = report["previous"]
    views, clicks = funnel["page_views"], funnel["clicks"]
    now_rate = rate(clicks, views)
    prev_rate = rate(prev["clicks"], prev["page_views"])
    points = None
    if now_rate["value"] is not None and prev_rate["value"] is not None:
        points = round(now_rate["value"] - prev_rate["value"], 1)
    return {
        "views": {
            "state": _state(report, "view_daily", "page_views"),
            "value": views,
            "previous": prev["page_views"],
            "change_percent": _change(views, prev["page_views"]),
        },
        "clicks": {
            "state": _state(report, "click_events", "clicks"),
            "value": clicks,
            "previous": prev["clicks"],
            "change_percent": _change(clicks, prev["clicks"]),
        },
        "click_rate": {
            "state": "ok" if now_rate["value"] is not None else "unavailable",
            "value": now_rate["value"],
            "capped": now_rate["capped"],
            "previous": prev_rate["value"],
            "change_points": points,
            "label": RATE_LABEL,
        },
        "previous_window": {"since": prev["since"], "until": prev["until"]},
    }


def _daily(report: dict[str, Any]) -> dict[str, Any]:
    daily = report["daily"]
    rows = []
    for d in daily["days"]:
        r = rate(d["clicks"], d["views"])
        if d["gap"]:
            status = "gap"
        elif d["views"] is None and d["clicks"] is None:
            status = "below_floor"
        else:
            status = "ok"
        rows.append(
            {
                "day": d["day"],
                "status": status,
                "views": d["views"],
                "clicks": d["clicks"],
                "rate": r["value"],
                "rate_capped": r["capped"],
            }
        )
    return {
        "timezone": "UTC",
        "views_state": daily["views_state"],
        "clicks_state": daily["clicks_state"],
        "days": rows,
    }


def _table(section: dict[str, Any]) -> dict[str, Any]:
    rows = [{"label": r["label"], "views": r["views"]} for r in section["rows"]]
    top = max((r["views"] for r in rows), default=0)
    for r in rows:
        r["bar_percent"] = round(r["views"] * 100 / top) if top else 0
    return {
        "state": section["state"],
        "rows": rows,
        "below_floor": section["other_below_floor"],
        "more_rows": section["more_rows"],
    }


def _links(report: dict[str, Any]) -> dict[str, Any]:
    section = report["links"]
    views = report["funnel"]["page_views"]
    rows = []
    for r in section["rows"]:
        rt = rate(r["views"], views)
        rows.append(
            {
                "label": r["label"],
                "clicks": r["views"],
                "rate": rt["value"],
                "rate_capped": rt["capped"],
            }
        )
    top = max((r["clicks"] for r in rows), default=0)
    for r in rows:
        r["bar_percent"] = round(r["clicks"] * 100 / top) if top else 0
    return {
        "state": section["state"],
        "rate_label": LINK_RATE_LABEL,
        "rows": rows,
        "below_floor": section["other_below_floor"],
        "more_rows": section["more_rows"],
    }


def _released(section: dict[str, Any], key: str) -> dict[str, Any]:
    """Clicks per source or per country from a released period, or its state."""
    if section["state"] != "ok":
        return {"state": section["state"], "rows": []}
    totals = section["sources"] if key == "source" else section["countries"]
    rows = [{"label": r[key], "clicks": r["clicks"]} for r in totals]
    return {"state": "ok", "rows": rows}


def _pairs(section: dict[str, Any], key: str) -> dict[str, Any]:
    if section["state"] != "ok":
        return {"state": section["state"], "rows": []}
    return {
        "state": "ok",
        "rows": [
            {"label": c[key], "destination": c["destination"], "clicks": c["clicks"]}
            for c in section["cells"]
        ],
    }


def owner_view(report: dict[str, Any]) -> dict[str, Any]:
    period = report["period"]
    geo = report["geography"]
    countries = _table(geo)
    if geo["level"] == "country":
        for r in countries["rows"]:
            if r["label"] == FOLDED_COUNTRY:
                r["note"] = UNKNOWN_NOTE
    release = report["release"]
    return {
        "site": report["site"],
        "timezone": "UTC",
        "floor": report["floor"],
        "window": {"since": period["since"], "until": period["until"]},
        "period": release["period"],
        "counted_classes": report["counted_classes"],
        "tiles": _tiles(report),
        "daily": _daily(report),
        "links": _links(report),
        "sources": _table(report["sources"]),
        "devices": _table(report["devices"]),
        "browsers": _table(report["browsers"]),
        "systems": _table(report["systems"]),
        "countries": {"level": geo["level"], **countries},
        "clicks_by_source": _released(report["click_matrix"], "source"),
        "clicks_by_country": _released(report["click_countries"], "country"),
        "source_by_destination": _pairs(report["click_matrix"], "source"),
        "country_by_destination": _pairs(report["click_countries"], "country"),
        "capacity_warning": report["capacity"]["warning"],
        "era": report.get("era"),
        "display": report.get("display"),
        "not_yet": NOT_YET,
    }
