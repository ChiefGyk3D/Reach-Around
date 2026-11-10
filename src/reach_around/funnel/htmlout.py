# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501  (inline HTML and SVG string literals)
"""The owner report as one self-contained HTML file (#438).

Rendered from ``owner_view``, which is derived from the floored report and
nothing else, so the page can show no figure the floored JSON does not hold.
The file has inline CSS and inline SVG, no script, no font, no image, no link
that loads anything, and a content security policy that blocks every request
the page could make. It is dark only, has no animation, and puts a border on
nothing (there are no buttons). It is for the owner to open locally; it is not
a public page and the site build never includes it.

Gaps (UTC days with no exported rows) are drawn as gaps; a day under the floor
is drawn as a tick on the baseline, never as zero. Axis labels print only 0 and
the largest printed value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape
from typing import Any

from reach_around.funnel.owner import CAPPED_NOTE, LINK_RATE_LABEL, RATE_LABEL

CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "base-uri 'none'; form-action 'none'"
)
BELOW = "below the privacy floor"

CSS = """
:root{color-scheme:dark;--ra-brand-amber:#FBB03B;--ra-brand-ground:#0F0F0F;
--ra-font-body:Jost,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--bg:#101216;--panel:#181b21;--row:#1e222a;--text:#e8eaed;
--muted:#b4bac4;--views:#8ab4ff;--clicks:#ffb74d;--bar:#5f95f2;--axis:#8b929d;
--gap:#2a2f39}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.5 var(--ra-font-body)}
main{max-width:1840px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1.1rem;margin:0 0 10px}
p{margin:4px 0}
.muted{color:var(--muted)}
.note{color:var(--muted);font-size:.9rem}
.grid{display:grid;gap:16px;margin-top:16px}
.tiles{grid-template-columns:repeat(3,1fr)}
.charts{grid-template-columns:repeat(2,1fr)}
.cols{grid-template-columns:repeat(3,1fr)}
.cols2{grid-template-columns:repeat(2,1fr)}
@media(max-width:1100px){.cols{grid-template-columns:repeat(2,1fr)}}
@media(max-width:800px){.tiles,.charts,.cols,.cols2{grid-template-columns:1fr}}
section,.tile{background:var(--panel);border-radius:6px;padding:14px 16px;min-width:0}
.tile .big{font-size:2.2rem;font-weight:600;line-height:1.2}
.tile .label{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;vertical-align:middle}
th{color:var(--muted);font-weight:600;font-size:.85rem}
td.n,th.n{text-align:right}
tbody tr:nth-child(odd){background:var(--row)}
.bar{display:block;height:8px;background:var(--bar);border-radius:2px}
.barcell{width:30%}
svg{width:100%;height:auto;display:block}
@media(max-width:800px){svg{min-width:640px}}
.legend{display:flex;flex-wrap:wrap;gap:4px 16px;margin:6px 0;font-size:.9rem}
.sw{display:inline-block;width:12px;height:12px;vertical-align:-1px;margin-right:5px}
details{margin-top:8px}
summary{cursor:pointer;color:var(--muted)}
.wrap{overflow-x:auto}
"""


WORDS = {
    "below_floor": BELOW,
    "withheld": "withheld (a small remainder could be recovered)",
    "not_in_export": "not in this export",
    "gap": "no data for this day",
    "unavailable": "n/a",
}


def _cell(value: int | float | None, status: str) -> str:
    """A figure, or the word for why it is not printed."""
    if status == "ok" and value is not None:
        return str(value)
    return WORDS.get(status, "n/a")


def _rate_text(value: float | None, capped: bool) -> str:
    if value is None:
        return "n/a"
    return f"{value}" + (" (capped)" if capped else "")


def _change(percent: float | None, label: str) -> str:
    if percent is None:
        return f"{label}: n/a (needs two printable figures)"
    sign = "+" if percent > 0 else ""
    return f"{label}: {sign}{percent}% against the previous window"


SPANS_GAP_LABEL = "Spans a collection gap: counts cover only the time collected."


def _coverage_note(view: dict[str, Any]) -> str:
    c = view["coverage"]
    if not c["spans_gap"]:
        return ""
    return (
        f'<p class="note">Collection gaps in this window: {c["gap_days"]} full-day '
        f"gaps, {c['partial_days']} partial days. A gap is unknown time, not a zero; "
        "the figures below count only the time collected.</p>"
    )


def _tile(name: str, tile: dict[str, Any], view: dict[str, Any]) -> str:
    prev = view["tiles"]["previous_window"]
    window = (
        f"{escape(prev['since'])} to {escape(prev['until'])} UTC"
        if prev["since"]
        else "no previous window"
    )
    released = view["released"]
    if name == "click_rate":
        big = _cell(tile["value"], tile["state"])
        small = RATE_LABEL
        if tile["capped"]:
            small += f". {CAPPED_NOTE}"
        pts = tile["change_points"]
        if pts is None:
            chg = "Change: n/a (needs two printable rates)"
        else:
            chg = f"Change: {'+' if pts > 0 else ''}{pts} points against the previous window"
        title = "Click rate"
    else:
        big = _cell(tile["value"], tile["state"])
        small = "likely-human page views" if name == "views" else "link clicks"
        chg = _change(tile["change_percent"], "Change")
        title = "Views" if name == "views" else "Clicks"
    if released:
        chg_html = ""
    else:
        chg_html = (
            f'<p class="note">{escape(chg)}</p>'
            f'<p class="note">Previous window: {window}</p>'
        )
    gap_html = (
        f'<p class="note">{escape(SPANS_GAP_LABEL)}</p>'
        if view["coverage"]["spans_gap"]
        else ""
    )
    return (
        f'<div class="tile"><h2>{title}</h2><div class="big">{escape(big)}</div>'
        f'<p class="label">{escape(small)}</p>{gap_html}{chg_html}</div>'
    )


# ---- charts ----------------------------------------------------------------

W, H = 800, 260
X0, X1, Y0, Y1 = 64, 784, 24, 196


def _x(i: int, count: int) -> float:
    if count <= 1:
        return (X0 + X1) / 2
    return X0 + (X1 - X0) * i / (count - 1)


def _chart(
    days: list[dict[str, Any]], series: list[tuple[str, str, str, str]], title: str
) -> str:
    """Inline SVG line chart. ``series`` is (key, css colour var, marker)."""
    count = len(days)
    top = max(
        (d[k] for d in days for k, _, _, _ in series if d.get(k) is not None),
        default=0,
    )

    def y(v: float) -> float:
        return Y1 - (Y1 - Y0) * (v / top) if top else Y1

    out = [
        f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
    ]
    for i, d in enumerate(days):
        if d["views_status"] == "gap":
            out.append(
                f'<rect x="{_x(i, count) - 3:.1f}" y="{Y0}" '
                f'width="6" height="{Y1 - Y0}" fill="var(--gap)"/>'
            )
    out.append(f'<line x1="{X0}" y1="{Y1}" x2="{X1}" y2="{Y1}" stroke="var(--axis)"/>')
    out.append(
        f'<text x="{X0 - 6}" y="{Y1 + 4}" fill="var(--muted)" text-anchor="end" font-size="20">0</text>'
    )
    if top:
        out.append(
            f'<text x="{X0 - 6}" y="{Y0 + 4}" fill="var(--muted)" text-anchor="end" font-size="20">{top}</text>'
        )
    if days:
        out.append(
            f'<text x="{X0}" y="{H - 8}" fill="var(--muted)" font-size="20">{escape(days[0]["day"])}</text>'
        )
        out.append(
            f'<text x="{X1}" y="{H - 8}" fill="var(--muted)" font-size="20" text-anchor="end">{escape(days[-1]["day"])} UTC</text>'
        )
    for key, colour, marker, status_key in series:
        run: list[str] = []
        runs: list[list[str]] = []
        last: str | None = None  # last printed point, for a dotted bridge
        bridged = False  # only under-floor days since it (never a gap)
        for i, d in enumerate(days):
            v = d.get(key)
            if v is None:
                if run:
                    runs.append(run)
                run = []
                if d[status_key] == "gap":
                    last = None  # a gap breaks the line for good
                elif d[status_key] == "below_floor":
                    bridged = True
                    out.append(
                        f'<path d="M{_x(i, count) - 4:.1f} {Y1 + 10} l4 -7 l4 7z" fill="{colour}"/>'
                    )
                else:
                    last = None  # withheld or not exported: nothing to join
                continue
            here = f"{_x(i, count):.1f} {y(v):.1f}"
            if last is not None and bridged:
                out.append(
                    f'<polyline points="{last} {here}" fill="none" stroke="{colour}" '
                    'stroke-width="1" stroke-dasharray="2 4" opacity="0.8"/>'
                )
            run.append(here)
            last, bridged = here, False
            if marker == "circle":
                out.append(
                    f'<circle cx="{_x(i, count):.1f}" cy="{y(v):.1f}" r="3.5" fill="{colour}"/>'
                )
            else:
                out.append(
                    f'<rect x="{_x(i, count) - 3.5:.1f}" y="{y(v) - 3.5:.1f}" width="7" height="7" fill="{colour}"/>'
                )
        if run:
            runs.append(run)
        dash = "" if marker == "circle" else ' stroke-dasharray="6 4"'
        for r in runs:
            if len(r) > 1:
                out.append(
                    f'<polyline points="{" ".join(r)}" fill="none" stroke="{colour}" '
                    f'stroke-width="2"{dash}/>'
                )
    out.append("</svg>")
    return '<div class="wrap">' + "".join(out) + "</div>"


def _daily_table(rows: list[dict[str, Any]]) -> str:
    body = []
    for r in rows:
        if r["views_status"] == "gap":
            cells = '<td colspan="3">no data in the export for this day (a gap, not zero)</td>'
        else:
            cells = (
                f'<td class="n">{escape(_cell(r["views"], r["views_status"]))}</td>'
                f'<td class="n">{escape(_cell(r["clicks"], r["clicks_status"]))}</td>'
                f'<td class="n">{escape(_rate_text(r["rate"], r["rate_capped"]))}</td>'
            )
        mark = {"gap": " (collection gap)", "partial": " (partial collection)"}.get(
            r["collection"], ""
        )
        body.append(f"<tr><td>{escape(r['day'] + mark)}</td>{cells}</tr>")
    return (
        '<div class="wrap"><table><thead><tr><th>Day (UTC)</th><th class="n">Views</th>'
        '<th class="n">Clicks</th><th class="n">Click rate</th></tr></thead><tbody>'
        + "".join(body)
        + "</tbody></table></div>"
    )


def _daily(view: dict[str, Any]) -> str:
    d = view["daily"]
    days = d["days"]
    if d["views_state"] == "not_released":
        return (
            "<section><h2>Views and clicks, daily (UTC days)</h2>"
            "<p>A released period carries no daily series and no comparison "
            "window: day-level figures from two releases could be differenced. "
            "Run an ad-hoc window (--days, --since, --until) for the owner "
            "charts.</p></section>"
        )
    if not days:
        return (
            "<section><h2>Views and clicks, daily (UTC days)</h2>"
            "<p>No days to chart in this window.</p></section>"
        )
    key = (
        '<div class="legend"><span><span class="sw" style="background:var(--views)">'
        '</span>Views (solid line, circles)</span><span><span class="sw" '
        'style="background:var(--clicks)"></span>Clicks (dashed line, squares)</span>'
        '<span><span class="sw" style="background:var(--gap)"></span>Gap: no data for the day</span>'
        "<span>Triangle on the baseline: day under the privacy floor</span></div>"
    )
    views_ok = d["views_state"] == "ok"
    clicks_ok = d["clicks_state"] == "ok"
    notes = ""
    if not views_ok:
        notes += f"<p class='note'>Daily views: {escape(d['views_state'].replace('_', ' '))}.</p>"
    if not clicks_ok:
        notes += f"<p class='note'>Daily clicks: {escape(d['clicks_state'].replace('_', ' '))}.</p>"
    chart1 = _chart(
        days,
        [
            ("views", "var(--views)", "circle", "views_status"),
            ("clicks", "var(--clicks)", "square", "clicks_status"),
        ],
        "Daily page views and clicks, UTC days",
    )
    chart2 = _chart(
        days,
        [("rate", "var(--clicks)", "square", "rate_status")],
        "Daily click rate, clicks per 100 page views, UTC days",
    )
    return (
        '<div class="grid charts"><section><h2>Views and clicks, daily (UTC days)</h2>'
        f"{key}{notes}{chart1}"
        "</section><section><h2>Click rate, daily (UTC days)</h2>"
        f'<p class="note">Click rate is {escape(RATE_LABEL)}. Days under the floor are left out. '
        f"A value above 100 is shown as 100 and marked in the table ({escape(CAPPED_NOTE)}).</p>"
        f"{chart2}</section></div>"
        f'<section style="margin-top:16px"><details open><summary>Daily table (day, views, clicks, rate)</summary>'
        f"{_daily_table(days)}</details></section>"
    )


# ---- tables ------------------------------------------------------------------


def _state_row(state: str, cols: int) -> str:
    text = {
        "not_in_export": "not in this export",
        "withheld": "every row is below the privacy floor; nothing shown",
        "period_required": "released only for one complete week or month (--period with a ledger)",
    }.get(state, state)
    return f'<tr><td colspan="{cols}">{escape(text)}</td></tr>'


def _views_table(title: str, t: dict[str, Any], label: str, note: str = "") -> str:
    if t["state"] != "ok":
        rows = _state_row(t["state"], 2)
    else:
        rows = "".join(
            f"<tr><td>{escape(r['label'])}"
            + (
                f' <span class="note">({escape(r["note"])})</span>'
                if r.get("note")
                else ""
            )
            + f'</td><td class="n">{r["views"]}</td></tr>'
            for r in t["rows"]
        )
        if not t["rows"]:
            rows = '<tr><td colspan="2">no data in this window</td></tr>'
        if t["below_floor"]:
            rows += f'<tr><td>(rows below the floor)</td><td class="n">{t["below_floor"]}</td></tr>'
        if t["more_rows"]:
            rows += f'<tr><td colspan="2" class="note">{t["more_rows"]} more rows (raise --top)</td></tr>'
    extra = f'<p class="note">{escape(note)}</p>' if note else ""
    return (
        f'<section><h2>{escape(title)}</h2>{extra}<div class="wrap"><table>'
        f'<thead><tr><th>{escape(label)}</th><th class="n">Views</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _clicks_table(title: str, t: dict[str, Any], label: str) -> str:
    if t["state"] != "ok":
        rows = _state_row(t["state"], 2)
    else:
        rows = (
            "".join(
                f'<tr><td>{escape(r["label"])}</td><td class="n">{r["clicks"]}</td></tr>'
                for r in t["rows"]
            )
            or '<tr><td colspan="2">no data in this period</td></tr>'
        )
    return (
        f'<section><h2>{escape(title)}</h2><div class="wrap"><table>'
        f'<thead><tr><th>{escape(label)}</th><th class="n">Clicks</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _pairs_table(title: str, t: dict[str, Any], label: str) -> str:
    if t["state"] != "ok":
        rows = _state_row(t["state"], 3)
    else:
        rows = (
            "".join(
                f"<tr><td>{escape(r['label'])}</td><td>{escape(r['destination'])}</td>"
                f'<td class="n">{r["clicks"]}</td></tr>'
                for r in t["rows"]
            )
            or '<tr><td colspan="3">no data in this period</td></tr>'
        )
    return (
        f'<section><h2>{escape(title)}</h2><div class="wrap"><table>'
        f'<thead><tr><th>{escape(label)}</th><th>Link</th><th class="n">Clicks</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></div></section>"
    )


def _links(view: dict[str, Any]) -> str:
    t = view["links"]
    if t["state"] != "ok":
        rows = _state_row(t["state"], 4)
    else:
        rows = (
            "".join(
                f'<tr><td>{escape(r["label"])}</td><td class="n">{r["clicks"]}</td>'
                f'<td class="n">{escape(_rate_text(r["rate"], r["rate_capped"]))}</td>'
                f'<td class="barcell"><span class="bar" style="width:{r["bar_percent"]}%"></span></td></tr>'
                for r in t["rows"]
            )
            or '<tr><td colspan="4">no data in this window</td></tr>'
        )
        if t["below_floor"]:
            rows += (
                f'<tr><td>(rows below the floor)</td><td class="n">{t["below_floor"]}</td>'
                "<td></td><td></td></tr>"
            )
        if t["more_rows"]:
            rows += f'<tr><td colspan="4" class="note">{t["more_rows"]} more rows (raise --top)</td></tr>'
    return (
        "<section><h2>Content clicks per link</h2>"
        f'<p class="note">Rate is {escape(LINK_RATE_LABEL)}; a link has no views of its own. '
        "Link names are the closed destination vocabulary the click ping carries.</p>"
        '<div class="wrap"><table><thead><tr><th>Link</th><th class="n">Clicks</th>'
        f'<th class="n">Rate</th><th></th></tr></thead><tbody>{rows}</tbody></table></div></section>'
    )


def render_html(view: dict[str, Any], *, generated_at: datetime) -> str:
    """The whole page as one string."""
    stamp = generated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    win = view["window"]
    span = (
        f"{win['since']} to {win['until']} (UTC days)"
        if win["since"]
        else "no data in this window"
    )
    if view["days_with_data"] is not None and view["window_days"]:
        # ad-hoc and owner-private only: a released period never says this
        span += f", {view['days_with_data']} of {view['window_days']} days have data"
    kind = f", released period: {view['period']}" if view["period"] else ""
    zone = ""
    if view.get("display"):
        zone = f" Display zone {view['display']['zone']}; counts stay per UTC day."
    era = view.get("era")
    era_verb = "" if era and era["window"] == "spans" else "is "
    era_html = (
        f'<p class="note">Cutover {escape(era["cutover_utc"])} UTC: this window {era_verb}{escape(era["text"])}.</p>'
        if era
        else ""
    )
    warn = (
        '<p class="note">Counting capacity warning: cell tables may be incomplete or biased.</p>'
        if view["capacity_warning"]
        else ""
    )
    nyet = view["not_yet"]
    geo = view["countries"]
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{escape(CSP, quote=False)}">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="color-scheme" content="dark">',
        '<meta name="robots" content="noindex,nofollow">',
        "<title>Reach Around owner report</title>",
        f"<style>{CSS}</style></head><body><main>",
        "<header><h1>Link analytics</h1>",
        f"<p>Site {escape(view['site'])}. Window: {escape(span)}{escape(kind)}. "
        f"All days are UTC.{escape(zone)}</p>",
        f'<p class="note">Generated {escape(stamp)}. Counted classes: '
        f"{escape(', '.join(view['counted_classes']))}. No row under {view['floor']} "
        "views is shown; a figure marked below the privacy floor is withheld. "
        "Private to the owner: do not publish this file. Ranges: run the report with "
        "--days 7, 30 or 90, or --since and --until.</p>",
        (
            ""
            if view["released"]
            else '<p class="note">Owner-private ad-hoc figures for one requested window: '
            "the daily series, the previous window and the gap marks can be "
            "differenced against another window or release. Do not publish, share "
            "or post this page.</p>"
        ),
        era_html + warn + _coverage_note(view) + "</header>",
        '<div class="grid tiles">',
        _tile("views", view["tiles"]["views"], view),
        _tile("clicks", view["tiles"]["clicks"], view),
        _tile("click_rate", view["tiles"]["click_rate"], view),
        "</div>",
        _daily(view),
        '<div class="grid cols" style="margin-top:16px">',
        _links(view),
        _views_table("Sources", view["sources"], "Source", nyet["sources"]),
        _views_table("Devices", view["devices"], "Device class", nyet["devices"]),
        "</div>",
        '<div class="grid cols" style="margin-top:16px">',
        _views_table("Browsers", view["browsers"], "Browser"),
        _views_table("Operating systems", view["systems"], "System"),
        _views_table(
            f"Countries (by {geo['level']})",
            geo,
            geo["level"].capitalize(),
            nyet["countries"],
        ),
        "</div>",
        '<div class="grid cols2" style="margin-top:16px">',
        _clicks_table(
            "Clicks by source (released period)", view["clicks_by_source"], "Source"
        ),
        _clicks_table(
            "Clicks by country (released period)", view["clicks_by_country"], "Country"
        ),
        _pairs_table("Source by link", view["source_by_destination"], "Source"),
        _pairs_table("Country by link", view["country_by_destination"], "Country"),
        "</div>",
        '<p class="note" style="margin-top:16px">Clicks are counts, not people. '
        "Human-likely is an estimate. The world map and the Links and Social icons "
        "tabs are not in this version (see docs/report.md).</p>",
        "</main></body></html>",
    ]
    return "\n".join(parts) + "\n"
