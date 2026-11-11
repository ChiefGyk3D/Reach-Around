# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
"""Owner report layout (#438): HTML and CSV from the floored report only.

Synthetic data only. No network, no Cloudflare, no secret."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import stat
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel.csvout import csv_files
from reach_around.funnel.export import load_exports
from reach_around.funnel.htmlout import render_html
from reach_around.funnel.owner import CAPPED_NOTE, owner_view, rate
from reach_around.funnel.report import _daily_series, build_report

LAST = date(2026, 11, 8)
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def _ts(day: date) -> int:
    return int(
        datetime(day.year, day.month, day.day, 12, tzinfo=UTC).timestamp() * 1000
    )


def fixture_tables(
    gap: str = "2026-11-04", small: str = "2026-11-06"
) -> dict[str, list[dict[str, Any]]]:
    """Fourteen UTC days ending 2026-11-08, one gap day and one small day."""
    views: list[dict[str, Any]] = []
    clicks: list[dict[str, Any]] = []
    geo: list[dict[str, Any]] = []
    for n in range(14):
        day = LAST - timedelta(days=13 - n)
        iso = day.isoformat()
        if iso == gap:
            continue
        v = 3 if iso == small else 40 + 3 * n
        views.append(_v(iso, "news.example", "US", "h", v))
        de = 1 if iso == small else 12 + n
        views.append(_v(iso, "mastodon.example", "DE", "h", de))
        if iso in ("2026-11-05", "2026-11-07"):
            views.append(
                _v(iso, "tiktok.example", "FR", "h", 1)
            )  # 2 in all: under the floor
        views.append(_v(iso, "crawler.example", "US", "a", 9))
        geo.append(_g(iso, "US", v))
        geo.append(_g(iso, "DE", de))
        for _ in range(2 if iso == small else 9 + n // 3):
            clicks.append({"site": "links", "ts": _ts(day), "link_id": "twitch"})
        for _ in range(0 if iso == small else 6):
            clicks.append({"site": "links", "ts": _ts(day), "link_id": "kick"})
    dims = [_d(iso, "device", "mobile", 60) for iso in ("2026-11-05", "2026-11-07")] + [
        _d("2026-11-07", "device", "desktop", 30),
        _d("2026-11-07", "device", "tablet", 2),
    ]
    return {
        "view_daily": views,
        "view_geo_daily": geo,
        "view_dim_daily": dims,
        "click_events": clicks,
    }


def _v(day: str, src: str, cc: str, cls: str, n: int) -> dict[str, Any]:
    return {
        "site": "links", "day": day, "day_tz": "UTC", "page": "home",
        "src_kind": "r", "src": src, "country": cc, "bot_class": cls, "views": n,
    }  # fmt: skip


def _g(day: str, cc: str, n: int) -> dict[str, Any]:
    return {
        "site": "links", "day": day, "country": cc, "region": "", "city": "",
        "bot_class": "h", "views": n,
    }  # fmt: skip


def _d(day: str, dim: str, value: str, n: int) -> dict[str, Any]:
    return {
        "site": "links", "day": day, "dim": dim, "value": value,
        "bot_class": "h", "views": n,
    }  # fmt: skip


def export_file(tmp_path: Path, tables: dict[str, Any]) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": tables}), encoding="utf-8")
    return path


def report_of(tmp_path: Path, tables: dict[str, Any], **kw: Any) -> dict[str, Any]:
    kw.setdefault("days", 7)
    return build_report(load_exports([export_file(tmp_path, tables)]), **kw)


def page_of(report: dict[str, Any]) -> str:
    return render_html(owner_view(report), generated_at=NOW)


def text_of(page: str) -> str:
    body = re.sub(r"<style>.*?</style>", " ", page, flags=re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body)


def table_rows(page: str, heading: str) -> list[list[str]]:
    """Cell texts of the first table after the heading."""
    idx = page.index(heading)
    inside = page.rfind("<table", 0, idx)
    start = (
        inside
        if inside != -1 and page.find("</table>", inside) > idx
        else page.index("<table", idx)
    )
    chunk = page[start : page.index("</table>", start)]
    rows = re.findall(r"<tr>(.*?)</tr>", chunk, flags=re.S)
    return [
        [
            re.sub(r"<[^>]+>", "", c).strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)
        ]
        for r in rows
    ]


def csv_rows(files: dict[str, bytes], name: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(files[name].decode("utf-8"), newline="")))


@pytest.fixture
def report(tmp_path: Path) -> dict[str, Any]:
    return report_of(tmp_path, fixture_tables())


# ---- layout ------------------------------------------------------------------


def test_every_section_of_the_layout_is_present(report: dict[str, Any]) -> None:
    page = page_of(report)
    for heading in (
        "Views",
        "Clicks",
        "Click rate",
        "Views and clicks, daily (UTC days)",
        "Click rate, daily (UTC days)",
        "Content clicks per link",
        "Sources",
        "Devices",
        "Browsers",
        "Operating systems",
        "Countries (by country)",
        "Clicks by source (released period)",
        "Clicks by country (released period)",
    ):
        assert f"<h2>{heading}</h2>" in page, heading
    assert "<svg" in page and "(UTC days)" in page
    assert "All days are UTC" in page
    assert report["period"]["since"] == "2026-11-02"


def test_layout_a_puts_the_shared_component_overview_before_details(
    report: dict[str, Any],
) -> None:
    page = page_of(report)
    overview = page.index('<section class="report-overview')
    details = page.index('<div class="report-details')
    assert overview < details
    overview_html = page[overview:details]
    assert len(re.findall(r'class="[^"]*\bra-c-plate\b', overview_html)) == 3
    assert "ra-c-ticks" in overview_html
    assert "--ra-brand-ground" in page
    assert "--ra-space-4" in page


@pytest.fixture(scope="module")
def owner_report_browser() -> Iterator[Any]:
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(
            args=["--no-sandbox"],
            executable_path=os.environ.get("RA_CHROMIUM") or None,
        )
        yield browser
        browser.close()


@pytest.mark.visual
@pytest.mark.parametrize(
    ("width", "columns"),
    [
        (390, {"tiles": 1, "charts": 1, "cols": 1, "cols2": 1}),
        (820, {"tiles": 3, "charts": 2, "cols": 2, "cols2": 2}),
        (1440, {"tiles": 3, "charts": 2, "cols": 3, "cols2": 2}),
    ],
)
def test_layout_a_computes_the_supported_viewport_layout_without_page_overflow(
    report: dict[str, Any],
    owner_report_browser: Any,
    width: int,
    columns: dict[str, int],
) -> None:
    context = owner_report_browser.new_context(viewport={"width": width, "height": 900})
    browser_page = context.new_page()
    browser_page.set_content(page_of(report), wait_until="load")

    assert browser_page.evaluate("document.documentElement.scrollWidth") <= width
    for class_name, expected in columns.items():
        tracks = browser_page.locator(f".{class_name}").first.evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
        )
        assert tracks == expected, (width, class_name, tracks)

    if width == 390:
        chart_wraps = browser_page.locator(".charts .wrap")
        assert chart_wraps.count() == 2
        for index in range(chart_wraps.count()):
            dimensions = chart_wraps.nth(index).evaluate(
                "element => [element.clientWidth, element.scrollWidth]"
            )
            assert dimensions[1] > dimensions[0], dimensions

    context.close()


def test_window_is_the_last_seven_days_with_previous_window(
    report: dict[str, Any],
) -> None:
    prev = report["previous"]
    assert (prev["since"], prev["until"]) == ("2026-10-26", "2026-11-01")
    tiles = owner_view(report)["tiles"]
    assert tiles["views"]["value"] == report["funnel"]["page_views"]
    assert tiles["views"]["previous"] == prev["page_views"] is not None
    assert tiles["views"]["change_percent"] is not None
    page = page_of(report)
    assert "against the previous window" in page
    assert "2026-10-26 to 2026-11-01 UTC" in page


def test_page_is_self_contained_dark_static_and_uses_tokenized_borders(
    report: dict[str, Any],
) -> None:
    page = page_of(report)
    assert page.startswith("<!doctype html>")
    assert "default-src 'none'" in page
    for forbidden in (
        "<script",
        "src=",
        "href=",
        "url(",
        "@import",
        "http:",
        "https:",
        "<link",
        "<img",
        "<iframe",
    ):
        assert forbidden not in page, forbidden
    low = page.lower()
    assert "border:var(--ra-rule-width) solid var(--ra-brand-rule)" in low
    assert "border-radius:var(--ra-radius-square)" in low
    assert "<button" not in low
    for forbidden in ("animation", "transition", "@keyframes", "outline"):
        assert forbidden not in low, forbidden
    for forbidden in ("#fff", "white", "cream", "background:#f", "background:#e"):
        assert forbidden not in low.replace("#ffb74d", ""), forbidden
    assert "color-scheme:dark" in page


def _lum(hexcolor: str) -> float:
    rgb = [int(hexcolor[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_text_and_chart_colors_meet_aa_on_every_surface(report: dict[str, Any]) -> None:
    page = page_of(report)
    css = dict(re.findall(r"--([a-z]+):(#[0-9a-f]{6})", page))
    for surface in ("bg", "panel", "row"):
        for fg in ("text", "muted"):
            assert _contrast(css[fg], css[surface]) >= 4.5, (fg, surface)
        for fg in ("views", "clicks", "bar", "axis"):
            assert _contrast(css[fg], css[surface]) >= 3.0, (fg, surface)
    assert _contrast("#2a2f39", css["panel"]) < 3  # the gap band is shading only


# ---- floors, gaps, empties ------------------------------------------------


def test_small_cells_are_not_printed_anywhere(
    report: dict[str, Any], tmp_path: Path
) -> None:
    page = page_of(report)
    assert "tiktok.example" not in page  # 1 view a day
    files = csv_files(report, generated_at=NOW)
    assert b"tiktok.example" not in b"".join(files.values())
    daily = {d["day"]: d for d in report["daily"]["days"]}
    small = daily["2026-11-06"]  # 3 views, 2 clicks
    assert small["views"] is None and small["clicks"] is None
    assert not any(
        "2026-11-06" in r and re.search(r"\b3\b", " ".join(r))
        for r in table_rows(page, "Daily table")
    )
    row = next(r for r in table_rows(page, "Day (UTC)") if r[0] == "2026-11-06")
    assert row[1:] == ["below the privacy floor"] * 2 + ["n/a"]


def test_gap_day_is_a_gap_not_zero(report: dict[str, Any]) -> None:
    day = next(d for d in report["daily"]["days"] if d["day"] == "2026-11-04")
    assert day["gap"] and day["views"] is None and day["clicks"] is None
    page = page_of(report)
    row = next(r for r in table_rows(page, "Day (UTC)") if r[0] == "2026-11-04")
    assert "a gap, not zero" in row[1]
    files = csv_files(report, generated_at=NOW)
    csv_row = next(
        r for r in csv_rows(files, "owner_daily.csv") if r[0] == "2026-11-04"
    )
    assert csv_row == ["2026-11-04", "", "gap", "", "gap", "", "", ""]


def test_one_small_day_also_hides_the_smallest_printed_day() -> None:
    state, cells = _daily_series({"a": 40, "b": 9, "c": 2, "d": 30}, True, 5)
    assert state == "ok"
    # c is under the floor, so the largest printed day (a) joins it: the printed
    # total minus the printed days is at least the floor and pins nothing
    assert cells == {"a": None, "b": 9, "c": None, "d": 30}
    assert 81 - sum(v for v in cells.values() if v) >= 5


def test_all_days_small_withholds_the_series() -> None:
    state, cells = _daily_series({"a": 2, "b": 3}, True, 5)
    assert state == "withheld" and set(cells.values()) == {None}


def test_empty_export_still_renders(tmp_path: Path) -> None:
    rep = build_report(
        load_exports([export_file(tmp_path, {"kit_events": []})]), days=7
    )
    page = page_of(rep)
    assert "no data in this window" in page or "No days to chart" in page
    assert "not in this export" in page
    files = csv_files(rep, generated_at=NOW)
    assert csv_rows(files, "owner_daily.csv") == [
        [
            "day_utc",
            "views",
            "views_status",
            "clicks",
            "clicks_status",
            "click_rate",
            "note",
            "collection",
        ]
    ]


def test_tables_present_but_no_rows_in_window(tmp_path: Path) -> None:
    rep = build_report(
        load_exports([export_file(tmp_path, {"view_daily": [], "click_events": []})]),
        days=7,
    )
    tiles = owner_view(rep)["tiles"]
    assert tiles["views"]["value"] == 0 and tiles["views"]["previous"] is None
    assert tiles["views"]["change_percent"] is None
    assert tiles["click_rate"]["value"] is None
    assert "n/a" in page_of(rep)


def test_withheld_views_blank_the_daily_views_and_previous(tmp_path: Path) -> None:
    # a source remainder that no merge can make safe withholds every view figure
    tables = fixture_tables()
    tables["view_daily"] = [
        _v("2026-11-08", "a.example", "US", "h", 2),
        _v("2026-11-07", "b.example", "US", "h", 2),
    ]
    rep = report_of(tmp_path, tables)
    daily = owner_view(rep)["daily"]
    assert all(d["views"] is None for d in daily["days"])
    assert owner_view(rep)["tiles"]["views"]["value"] is None


# ---- click rate ---------------------------------------------------------------


def test_rate_is_capped_and_labeled() -> None:
    assert rate(30, 20) == {"value": 100.0, "capped": True}
    assert rate(10, 40) == {"value": 25.0, "capped": False}
    assert rate(None, 40)["value"] is None and rate(10, None)["value"] is None
    assert rate(10, 0)["value"] is None


def test_capped_rate_is_labeled_in_html_and_csv(tmp_path: Path) -> None:
    tables = fixture_tables()
    tables["click_events"] = tables["click_events"] * 8  # clicks exceed views
    rep = report_of(tmp_path, tables)
    view = owner_view(rep)
    assert view["tiles"]["click_rate"]["capped"] is True
    assert view["tiles"]["click_rate"]["value"] == 100.0
    page = page_of(rep)
    assert CAPPED_NOTE in page
    assert "(capped)" in page
    files = csv_files(rep, generated_at=NOW)
    assert ["click_rate", "ok"] == csv_rows(files, "owner_tiles.csv")[3][:2]
    assert csv_rows(files, "owner_tiles.csv")[3][5] == "capped"


def test_link_rate_uses_the_page_level_denominator(report: dict[str, Any]) -> None:
    view = owner_view(report)
    views = report["funnel"]["page_views"]
    assert view["links"]["rate_label"] == "clicks per 100 views of the page"
    for row in view["links"]["rows"]:
        assert row["rate"] == round(row["clicks"] * 100 / views, 1)
    assert "clicks per 100 views of the page" in page_of(report)


# ---- one data path ----------------------------------------------------------


def test_csv_matches_the_html_numbers(report: dict[str, Any]) -> None:
    page = page_of(report)
    files = csv_files(report, generated_at=NOW)
    html_daily = table_rows(page, "Day (UTC)")[1:]
    csv_daily = csv_rows(files, "owner_daily.csv")[1:]
    assert len(html_daily) == len(csv_daily) == 7
    for h, c in zip(html_daily, csv_daily, strict=True):
        assert h[0] == c[0]
        if c[2] == "gap":
            assert "gap" in h[1]
            continue
        for shown, raw, status in ((h[1], c[1], c[2]), (h[2], c[3], c[4])):
            assert shown == (raw if status == "ok" else "below the privacy floor")
        assert h[3] == (c[5] + (" (capped)" if c[6] else "") if c[5] else "n/a")
    html_links = table_rows(page, "Content clicks per link")[1:]
    csv_links = csv_rows(files, "owner_links.csv")[1:]
    for h, c in zip(html_links, csv_links, strict=False):
        if c[4] == "row":
            assert h[0] == c[0] and h[1] == c[1]
            assert h[2] == c[2] + (" (capped)" if c[3] else "")
    html_sources = table_rows(page, "<h2>Sources</h2>")[1:]
    csv_sources = csv_rows(files, "owner_sources.csv")[1:]
    assert [r[:2] for r in html_sources if r[0] != "(rows below the floor)"] == [
        r[:2] for r in csv_sources if r[2] == "row"
    ]
    tiles = {r[0]: r for r in csv_rows(files, "owner_tiles.csv")[1:]}
    assert str(report["funnel"]["page_views"]) == tiles["views"][2]
    assert str(report["funnel"]["clicks"]) == tiles["clicks"][2]


NUM = re.compile(r"(?<![\w.-])-?\d+(?:\.\d+)?(?![\w-])")


def _numbers(obj: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, int | float):
        out.add(str(obj))
        if isinstance(obj, float) and obj == int(obj):
            out.add(str(int(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _numbers(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= _numbers(v)
    return out


def test_html_has_no_figure_the_floored_report_does_not_hold(
    report: dict[str, Any],
) -> None:
    page = page_of(report)
    body = re.sub(
        r"<(style|svg)\b.*?</\1>", " ", page, flags=re.S
    )  # chart is in the tables
    text = text_of(body)
    text = re.sub(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2})?", " ", text)  # dates, stamp
    allowed = _numbers(json.loads(json.dumps(report)))
    funnel, prev = report["funnel"], report["previous"]
    views, clicks = funnel["page_views"], funnel["clicks"]
    # arithmetic on two printed figures, recomputed here by hand
    allowed.add(str(round(clicks * 100 / views, 1)))
    for key in ("page_views", "clicks"):
        allowed.add(str(round((funnel[key] - prev[key]) * 100 / prev[key], 1)))
        allowed.add(f"+{round((funnel[key] - prev[key]) * 100 / prev[key], 1)}")
    for row in report["links"]["rows"]:
        allowed.add(str(round(row["views"] * 100 / views, 1)))
    for d in report["daily"]["days"]:
        if d["views"] and d["clicks"] is not None:
            allowed.add(str(round(d["clicks"] * 100 / d["views"], 1)))
    rate_now = round(clicks * 100 / views, 1)
    rate_prev = round(prev["clicks"] * 100 / prev["page_views"], 1)
    diff = round(rate_now - rate_prev, 1)
    allowed |= {str(diff), f"+{diff}", "7", "30", "90", "100"}  # --days 7, 30, 90
    stray = [n for n in NUM.findall(text) if n not in allowed]
    assert stray == []
    # the chart labels print only 0 and the largest printed value
    labels = re.findall(r"<text[^>]*>(.*?)</text>", page)
    nums = [x for x in labels if NUM.fullmatch(x)]
    assert set(nums) <= (allowed | {"0"}), nums


def test_every_json_figure_for_the_tiles_is_the_floored_figure(tmp_path: Path) -> None:
    # 4 clicks in the window: under the floor, so no tile figure and no rate
    tables = fixture_tables()
    tables["click_events"] = tables["click_events"][-4:]
    rep = report_of(tmp_path, tables)
    view = owner_view(rep)
    assert view["tiles"]["clicks"]["value"] is None
    assert view["tiles"]["click_rate"]["value"] is None
    page = page_of(rep)
    assert "below the privacy floor" in page
    assert '<div class="big">4</div>' not in page


def test_formula_cells_are_neutralized_in_owner_csv(report: dict[str, Any]) -> None:
    view = owner_view(report)
    view["sources"]["rows"].append(
        {"label": "=HYPERLINK(1)", "views": 9, "bar_percent": 1}
    )
    from reach_around.funnel.csvout import owner_csv_files

    raw = owner_csv_files(view)["owner_sources.csv"].decode()
    assert "'=HYPERLINK(1)" in raw and "\n=HYPERLINK" not in raw


def test_hostile_labels_are_escaped_in_html(report: dict[str, Any]) -> None:
    view = owner_view(report)
    view["links"]["rows"].append(
        {
            "label": "<img src=x onerror=1>",
            "clicks": 9,
            "rate": 1.0,
            "rate_capped": False,
            "bar_percent": 5,
        }
    )
    page = render_html(view, generated_at=NOW)
    assert "<img" not in page and "&lt;img" in page


def test_html_labels_a_window_that_spans_the_cutover(tmp_path: Path) -> None:
    report = report_of(tmp_path, fixture_tables(), as_of=LAST)
    report["era"] = {
        "cutover_utc": "2026-11-05",
        "window": "spans",
        "text": "spans the cutover (numbers from before and after are added together)",
    }
    page = page_of(report)
    assert (
        "Cutover 2026-11-05 UTC: this window spans the cutover "
        "(numbers from before and after are added together)." in page
    )
    assert "window is spans" not in page
    report["era"] = {"cutover_utc": "2026-11-05", "window": "after", "text": "after"}
    assert "this window is after." in page_of(report)


# ---- CLI --------------------------------------------------------------------


def test_cli_writes_the_page_owner_only_and_defaults_to_seven_days(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export_file(tmp_path, fixture_tables())
    out = tmp_path / "owner.html"
    code = main(["report", "--input", str(path), "--html", str(out)])
    assert code == 0, capsys.readouterr().err
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    page = out.read_text(encoding="utf-8")
    assert "2026-11-02 to 2026-11-08 (UTC days)" in page
    assert not list(tmp_path.glob(".*.tmp"))
    # rewrite replaces the file, a symlink at the target is not followed
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    link = tmp_path / "link.html"
    link.symlink_to(victim)
    assert main(["report", "--input", str(path), "--html", str(link)]) == 0
    assert victim.read_text() == "keep" and not link.is_symlink()


def test_cli_html_agrees_with_json_and_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export_file(tmp_path, fixture_tables())
    out = tmp_path / "owner.html"
    csv_dir = tmp_path / "csv"
    assert (
        main(
            [
                "report",
                "--input",
                str(path),
                "--days",
                "7",
                "--html",
                str(out),
                "--csv",
                str(csv_dir),
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["report", "--input", str(path), "--days", "7", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    tiles = {
        r[0]: r
        for r in csv.reader((csv_dir / "owner_tiles.csv").read_text().splitlines())
    }
    assert tiles["views"][2] == str(rep["funnel"]["page_views"])
    assert f'<div class="big">{rep["funnel"]["page_views"]}</div>' in out.read_text()


def test_cli_html_write_failure_is_exit_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export_file(tmp_path, fixture_tables())
    code = main(
        ["report", "--input", str(path), "--html", str(tmp_path / "no" / "x.html")]
    )
    assert code == 2
    assert "cannot write HTML" in capsys.readouterr().err


def test_text_report_has_the_daily_table_with_gaps(report: dict[str, Any]) -> None:
    from reach_around.funnel.report import render_text

    text = render_text(report)
    assert "Daily (UTC days; gap = no rows exported, never zero)" in text
    row = next(x for x in text.splitlines() if x.strip().startswith("2026-11-04"))
    assert row.split() == ["2026-11-04", "gap", "gap", "gap"]
    row = next(x for x in text.splitlines() if x.strip().startswith("2026-11-06"))
    assert "<floor" in row and "n/a" in row


# ---- released path (review findings, #438) ------------------------------------

from test_report_funnel import AS_OF, released  # noqa: E402


def _week_tables(days: dict[str, int], clicks: int = 12) -> dict[str, Any]:
    views = [_v(day, "news.example", "US", "h", n) for day, n in days.items()]
    events = [
        {"site": "links", "ts": _ts(date.fromisoformat(day)), "link_id": "twitch"}
        for day in days
        for _ in range(clicks)
    ]
    return {"view_daily": views, "click_events": events}


def _release(tmp_path: Path, tables: dict[str, Any]) -> dict[str, Any]:
    sub = tmp_path / "rel"
    sub.mkdir(exist_ok=True)
    export = load_exports([export_file(sub, tables)])
    return released(sub, export, period="week", as_of=AS_OF)


def test_released_period_has_no_daily_series_so_windows_cannot_be_differenced(
    tmp_path: Path,
) -> None:
    # 2, 10 and 20 views on three days of the released week (the review's numbers)
    rep = _release(
        tmp_path, _week_tables({"2026-11-02": 2, "2026-11-03": 10, "2026-11-04": 20})
    )
    assert rep["funnel"]["page_views"] == 32
    assert rep["daily"]["days"] == []
    assert rep["daily"]["views_state"] == rep["daily"]["clicks_state"] == "not_released"
    assert json.dumps(rep).count("2026-11-03") == 0  # no per-day line at all
    view = owner_view(rep)
    assert view["daily"]["days"] == []
    page = page_of(rep)
    assert "2026-11-02</td>" not in page
    files = csv_files(rep, generated_at=NOW)
    assert [r[0] for r in csv_rows(files, "owner_daily.csv")] == ["day_utc"]


def test_released_period_has_no_previous_window_block(tmp_path: Path) -> None:
    rep = _release(tmp_path, _week_tables({"2026-11-02": 30, "2026-10-27": 40}))
    assert rep["previous"] == {
        "since": None, "until": None, "page_views": None, "clicks": None,
    }  # fmt: skip
    tiles = owner_view(rep)["tiles"]
    assert (
        tiles["views"]["previous"] is None and tiles["views"]["change_percent"] is None
    )
    assert tiles["previous_window"] == {"since": None, "until": None}
    page = page_of(rep)
    assert "2026-10-26" not in page
    assert "Previous window:" not in page and "against the previous window" not in page
    assert b"2026-10-2" not in b"".join(csv_files(rep, generated_at=NOW).values())


def test_released_gap_and_small_day_are_indistinguishable(tmp_path: Path) -> None:
    a = _release(
        tmp_path, _week_tables({"2026-11-02": 30, "2026-11-03": 2, "2026-11-05": 40})
    )
    b = _release(
        tmp_path, _week_tables({"2026-11-02": 30, "2026-11-05": 40})
    )  # 11-03 a gap
    assert a["daily"] == b["daily"]
    assert csv_files(a, generated_at=NOW)["owner_daily.csv"] == csv_files(b, generated_at=NOW)["owner_daily.csv"]  # fmt: skip
    assert owner_view(a)["daily"] == owner_view(b)["daily"]


def test_adhoc_report_is_marked_owner_private_and_differenceable(
    report: dict[str, Any],
) -> None:
    page = page_of(report)
    assert "owner-private" in page.lower() and "can be differenced" in page
    assert "do not publish" in page.lower()
    from reach_around.funnel.report import render_text

    assert "owner-private" in render_text(report)


def test_adhoc_build_cannot_write_a_ledger(tmp_path: Path) -> None:
    import inspect

    from reach_around.funnel import report as mod

    assert "ledger" not in inspect.signature(mod.build_report).parameters
    before = {p.name for p in tmp_path.iterdir()}
    report_of(tmp_path, fixture_tables())
    assert {p.name for p in tmp_path.iterdir()} - before == {"export.json"}


def test_chart_text_is_readable_and_gap_shading_is_narrow(
    report: dict[str, Any],
) -> None:
    page = page_of(report)
    sizes = {int(x) for x in re.findall(r'font-size="(\d+)"', page)}
    assert min(sizes) >= 20
    widths = [
        float(w) for w in re.findall(r'<rect x="[\d.]+" y="24" width="([\d.]+)"', page)
    ]
    assert widths and max(widths) <= 8
    assert "min-width:640px" in page


# ---- round 2: repeat releases and released metadata ------------------------


def _round2_tables(monday: int | None) -> dict[str, Any]:
    """Floor 10: Tuesday 30 and Sunday 40 views, an optional 2-view Monday.

    The click rows are the same either way, so the click cells are identical.
    """
    days = {"2026-11-03": 30, "2026-11-08": 40}
    if monday is not None:
        days = {"2026-11-02": monday, **days}
    views = [_v(day, "news.example", "US", "h", n) for day, n in days.items()]
    events = [
        {"site": "links", "ts": _ts(date.fromisoformat(d)), "link_id": "twitch"}
        for d in ("2026-11-03", "2026-11-08")
        for _ in range(15)
    ]
    return {"view_daily": views, "click_events": events}


def _release_on(ledger: Path, tmp_path: Path, tables: dict[str, Any]) -> dict[str, Any]:
    from reach_around.funnel.release import release_report
    from test_report_funnel import add_evidence

    export = load_exports([export_file(tmp_path, tables)])
    add_evidence(export, "week", 0, AS_OF)
    report, _status = release_report(
        export, ledger, period="week", as_of=AS_OF, floor=10
    )
    return report


def test_a_repeat_of_one_period_with_different_page_views_is_refused(
    tmp_path: Path,
) -> None:
    from reach_around.funnel.ledger import LedgerError, init_ledger

    ledger = tmp_path / "ledger.jsonl"
    init_ledger(ledger)
    first = _release_on(ledger, tmp_path, _round2_tables(monday=2))
    assert first["funnel"]["page_views"] == 72
    with pytest.raises(LedgerError):  # 70 would reveal the 2-view Monday
        _release_on(ledger, tmp_path, _round2_tables(monday=None))
    # the same projection again is still a plain repeat
    again = _release_on(ledger, tmp_path, _round2_tables(monday=2))
    assert again == first


def test_released_period_metadata_is_the_fixed_period_not_the_seen_days(
    tmp_path: Path,
) -> None:
    from reach_around.funnel.ledger import init_ledger
    from reach_around.funnel.report import render_text

    outs = []
    for monday in (2, None):
        sub = tmp_path / f"m{monday}"
        sub.mkdir()
        ledger = sub / "ledger.jsonl"
        init_ledger(ledger)
        outs.append(_release_on(ledger, sub, _round2_tables(monday)))
    with_monday, without = outs
    for rep in outs:
        assert rep["period"] == {"since": "2026-11-02", "until": "2026-11-08"}
        assert "days_with_data" not in json.dumps(rep)
    # everything but the page-view total (72 against 70) is identical, so no
    # field tells a boundary gap from a day under the floor
    meta = [csv_files(r, generated_at=NOW)["metadata.csv"] for r in outs]
    assert meta[0] == meta[1] and b"days_with_data" not in meta[0]
    texts = [render_text(r).splitlines()[0] for r in outs]
    assert texts[0] == texts[1] and "days with data" not in texts[0]
    assert "2026-11-02 to 2026-11-08" in texts[0]
    win = [re.search(r"Window: [^.]*\.", page_of(r)).group(0) for r in outs]  # type: ignore[union-attr]
    assert win[0] == win[1] and "2026-11-02 to 2026-11-08" in win[0]
    assert with_monday["funnel"]["page_views"] - without["funnel"]["page_views"] == 2


# ---- review threads: states, per-series ticks, requested window, range cap ----


def _unsafe_views_tables() -> dict[str, Any]:
    """24 views, each source 4: no row reaches the floor, so no merge is safe."""
    tables = fixture_tables()
    tables["view_daily"] = [
        _v(day, f"s{day[-1]}{n}.example", "US", "h", 4)
        for day in ("2026-11-07", "2026-11-08")
        for n in range(3)
    ] + [_v("2026-11-02", "s9.example", "US", "h", 1)]
    return tables


def test_withheld_views_are_withheld_not_below_the_floor(tmp_path: Path) -> None:
    rep = report_of(tmp_path, _unsafe_views_tables())
    assert rep["bots"]["views_withheld"] and rep["funnel"]["page_views"] is None
    view = owner_view(rep)
    assert view["tiles"]["views"]["state"] == "withheld"
    assert view["tiles"]["click_rate"]["state"] == "withheld"
    rows = {d["day"]: d for d in view["daily"]["days"]}
    assert rows["2026-11-07"]["views_status"] == "withheld"
    assert rows["2026-11-04"]["views_status"] == "gap"
    page = page_of(rep)
    tile = page[page.index("<h2>Views</h2>") : page.index("<h2>Clicks</h2>")]
    assert "withheld" in tile and "below the privacy floor" not in tile
    row = next(r for r in table_rows(page, "Day (UTC)") if r[0] == "2026-11-07")
    assert row[1].startswith("withheld") and "below the privacy floor" not in row[1]
    files = csv_files(rep, generated_at=NOW)
    tiles = {r[0]: r for r in csv_rows(files, "owner_tiles.csv")[1:]}
    assert tiles["views"][1] == "withheld" and tiles["click_rate"][1] == "withheld"
    daily = {r[0]: r for r in csv_rows(files, "owner_daily.csv")[1:]}
    assert daily["2026-11-07"][2] == "withheld"
    assert daily["2026-11-04"][2] == "gap" and daily["2026-11-04"][4] == "gap"


def test_below_floor_and_missing_have_their_own_words(tmp_path: Path) -> None:
    tables = fixture_tables()
    tables["click_events"] = tables["click_events"][-4:]  # 4 clicks: under the floor
    page = page_of(report_of(tmp_path, tables))
    tile = page[page.index("<h2>Clicks</h2>") : page.index("<h2>Click rate</h2>")]
    assert "below the privacy floor" in tile
    only_kit = build_report(
        load_exports([export_file(tmp_path, {"kit_events": []})]), days=7
    )
    assert owner_view(only_kit)["tiles"]["views"]["state"] == "not_in_export"
    assert "not in this export" in page_of(only_kit)


def test_a_tick_is_drawn_per_series_for_its_own_under_floor_day(
    tmp_path: Path,
) -> None:
    tables = fixture_tables()  # 11-06: 4 views, 2 clicks
    tables["click_events"] += [
        {"site": "links", "ts": _ts(date(2026, 11, 6)), "link_id": "twitch"}
        for _ in range(8)
    ]  # now 10 clicks that day: only the views are under the floor
    rep = report_of(tmp_path, tables)
    rows = {d["day"]: d for d in owner_view(rep)["daily"]["days"]}
    assert rows["2026-11-06"]["views_status"] == "below_floor"
    assert rows["2026-11-06"]["clicks_status"] == "ok"
    page = page_of(rep)
    first = page[page.index("<svg") : page.index("</svg>")]
    assert 'l4 -7 l4 7z" fill="var(--views)"' in first  # a views tick
    assert 'l4 -7 l4 7z" fill="var(--clicks)"' not in first  # no clicks tick


def test_header_shows_the_requested_window_even_with_a_gap_at_the_edge(
    tmp_path: Path,
) -> None:
    rep = report_of(tmp_path, fixture_tables(gap="2026-11-02"))
    assert rep["period"]["since"] == "2026-11-03"  # span of days with rows
    page = page_of(rep)
    assert "Window: 2026-11-02 to 2026-11-08 (UTC days)" in page
    assert "6 of 7 days have data" in page


def test_released_header_still_uses_the_fixed_bounds(tmp_path: Path) -> None:
    rep = _release(tmp_path, _week_tables({"2026-11-03": 30}))
    page = page_of(rep)
    assert "Window: 2026-11-02 to 2026-11-08 (UTC days)" in page
    assert "days have data" not in page


def test_a_range_over_366_days_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = load_exports([export_file(tmp_path, fixture_tables())])
    with pytest.raises(ValueError, match="366 days"):
        build_report(export, since="2025-01-01", until="2026-11-08")
    build_report(export, since="2025-11-08", until="2026-11-08")  # exactly 366: fine
    path = tmp_path / "export.json"
    code = main(
        [
            "report",
            "--input",
            str(path),
            "--since",
            "2025-01-01",
            "--until",
            "2026-11-08",
        ]
    )
    assert code == 2 and "366 days" in capsys.readouterr().err
