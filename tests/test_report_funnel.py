# SPDX-License-Identifier: Apache-2.0
"""Funnel sections of ``reach-around report``: source by destination matrix,
ranked flows and country by destination (issues #331 and #409).

Synthetic exports only. The privacy property under test: a cell under the floor
cannot be recovered from any number the report prints."""

from __future__ import annotations

import csv
import io
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel.csvout import csv_files
from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.report import (
    REMAINDER,
    build_report,
    period_bounds,
    render_text,
)

DAY = "2026-11-01"
# the latest releasable month is 2026-11 (its day cells are gone by then)
AS_OF = date(2027, 3, 10)
TS = 1_793_491_200_000  # 2026-11-01 00:00 UTC


def src_rows(spec: dict[str, dict[str, int]], day: str = DAY) -> list[dict[str, Any]]:
    return [
        {"site": "links", "day": day, "link_id": link, "src": src, "clicks": n}
        for link, cells in spec.items()
        for src, n in cells.items()
    ]


def cc_rows(spec: dict[str, dict[str, int]], day: str = DAY) -> list[dict[str, Any]]:
    return [
        {"site": "links", "day": day, "link_id": link, "country": cc, "clicks": n}
        for link, cells in spec.items()
        for cc, n in cells.items()
    ]


def write(tmp_path: Path, tables: dict[str, Any]) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": tables}), encoding="utf-8")
    return path


def report_of(
    tmp_path: Path,
    tables: dict[str, Any],
    period: str | None = "month",
    offset: int = 0,
    as_of: date = AS_OF,
    **kw: Any,
) -> dict[str, Any]:
    return build_report(
        load_exports([write(tmp_path, tables)]),
        period=period,
        offset=offset,
        as_of=as_of,
        **kw,
    )


def raw_clicks(spec: dict[str, int]) -> list[dict[str, Any]]:
    return [{"ts": TS, "link_id": link} for link, n in spec.items() for _ in range(n)]


def totals(spec: dict[str, dict[str, int]]) -> dict[str, int]:
    return {link: sum(cells.values()) for link, cells in spec.items()}


def test_matrix_and_country_table_print_whole_clean_destinations(
    tmp_path: Path,
) -> None:
    src = {
        "twitch": {"instagram": 12, "direct": 8},
        "kick": {"instagram": 9, "bluesky": 6},
    }
    cc = {"twitch": {"US": 14, "DE": 6}, "kick": {"US": 10, "ZZ": 5}}
    r = report_of(
        tmp_path,
        {
            "click_src_daily": src_rows(src),
            "click_country_daily": cc_rows(cc),
        },
    )
    m = r["click_matrix"]
    assert m["state"] == "ok"
    cells = {(c["source"], c["destination"]): c["clicks"] for c in m["cells"]}
    assert cells == {
        ("instagram", "twitch"): 12,
        ("direct", "twitch"): 8,
        ("instagram", "kick"): 9,
        ("bluesky", "kick"): 6,
    }
    assert m["ranked"][0] == {
        "label": "instagram -> twitch",
        "clicks": 12,
        "share": round(12 * 100 / 35, 1),
    }
    c = r["click_countries"]
    got = {(x["country"], x["destination"]): x["clicks"] for x in c["cells"]}
    # ZZ is printed as "other"
    assert got[("other", "kick")] == 5 and got[("US", "twitch")] == 14
    text = render_text(r)
    assert "Clicks by source and destination" in text
    assert "instagram -> twitch" in text
    assert "Clicks by country and destination" in text
    assert "not people" in text


def test_a_small_cell_is_merged_into_one_remainder_not_a_reason_to_hide_the_rest(
    tmp_path: Path,
) -> None:
    # twitch: 20 and 9 are printable, 3 is not. The largest printed cell (20)
    # joins the 3: the remainder (23) reaches the floor and, having no upper
    # bound from the printed cells, pins nothing under the floor.
    src = {"twitch": {"instagram": 20, "bluesky": 9, "direct": 3}, "kick": {"a": 9}}
    m = report_of(tmp_path, {"click_src_daily": src_rows(src)})["click_matrix"]
    cells = {(c["source"], c["destination"]): c["clicks"] for c in m["cells"]}
    assert cells == {
        ("bluesky", "twitch"): 9,
        (REMAINDER, "twitch"): 23,
        ("a", "kick"): 9,
    }
    assert "instagram" not in json.dumps(m) and '"source": "direct"' not in json.dumps(
        m
    )
    assert {d["destination"]: d["clicks"] for d in m["destinations"]} == {
        "twitch": 32,
        "kick": 9,
    }


def test_everything_small_is_withheld(tmp_path: Path) -> None:
    r = report_of(tmp_path, {"click_src_daily": src_rows({"twitch": {"a": 2, "b": 4}})})
    assert r["click_matrix"]["state"] == "withheld"
    assert "nothing shown" in render_text(r)


def test_tables_missing_from_the_export_say_so(tmp_path: Path) -> None:
    r = report_of(tmp_path, {"kit_events": [{"ts": TS, "kind": "view"}] * 6})
    assert r["click_matrix"]["state"] == "not_in_export"
    assert r["click_countries"]["state"] == "not_in_export"
    assert "not in this export" in render_text(r)


def test_without_a_period_the_cell_sections_are_not_released(tmp_path: Path) -> None:
    r = report_of(
        tmp_path,
        {"click_src_daily": src_rows({"twitch": {"a": 9}})},
        period=None,
    )
    assert r["click_matrix"]["state"] == "period_required"
    assert r["click_matrix"]["cells"] == []
    assert "--period" in render_text(r)


def test_a_free_window_cannot_be_combined_with_a_period(tmp_path: Path) -> None:
    path = write(tmp_path, {"click_src_daily": src_rows({"twitch": {"a": 9}})})
    for extra in ({"since": DAY}, {"until": DAY}, {"days": 3}):
        with pytest.raises(ValueError, match="fixed calendar week or month"):
            build_report(load_exports([path]), period="month", as_of=AS_OF, **extra)


def test_a_free_window_still_gives_the_aggregate_sections(tmp_path: Path) -> None:
    r = build_report(
        load_exports(
            [
                write(
                    tmp_path,
                    {
                        "click_events": raw_clicks({"twitch": 12}),
                        "click_src_daily": src_rows({"twitch": {"a": 12}}),
                    },
                )
            ]
        ),
        since=DAY,
        until=DAY,
    )
    assert r["funnel"]["clicks"] == 12 and r["links"]["rows"]
    assert r["click_matrix"]["state"] == "period_required"


def test_country_by_destination_counts_clicks_the_raw_rows_do_not(
    tmp_path: Path,
) -> None:
    cc = {"twitch": {"US": 11, "DE": 9}}
    r = report_of(
        tmp_path,
        {
            "click_events": [{"ts": TS, "link_id": "twitch", "src": "instagram"}] * 12
            + [{"ts": TS, "link_id": "twitch"}] * 8,
            "click_country_daily": cc_rows(cc),
        },
    )
    assert sum(x["clicks"] for x in r["click_countries"]["cells"]) == 20


def test_destinations_follow_the_links_section_when_raw_clicks_are_present(
    tmp_path: Path,
) -> None:
    src = {"twitch": {"a": 20, "b": 10}, "kick": {"a": 6}}
    r = report_of(
        tmp_path,
        {
            "click_events": raw_clicks({"twitch": 30, "kick": 6}),
            "click_src_daily": src_rows(src),
        },
    )
    shown_links = {row["label"] for row in r["links"]["rows"]}
    assert {c["destination"] for c in r["click_matrix"]["cells"]} <= shown_links


# ---- recoverability --------------------------------------------------------


def expected_release(cells: dict[str, int], floor: int) -> tuple[dict[str, int], int]:
    """What may be printed for one destination: exact cells and one remainder.

    Written from the rule, not from the code: the largest printed cells join
    the remainder until the bound a reader can derive from it (R minus the
    larger of the floor and the largest cell still printed) is no tighter than
    "each hidden cell is under the floor"."""
    shown = sorted((v, k) for k, v in cells.items() if v >= floor)[::-1]
    rem = sum(v for v in cells.values() if v < floor)
    if not rem:
        return {k: v for v, k in shown}, 0
    if shown:
        rem += shown.pop(0)[0]  # a printed cell always joins
    while True:
        if rem - max([v for v, _ in shown] + [floor]) >= floor - 1:
            return {k: v for v, k in shown}, rem
        if not shown:
            return {}, 0
        rem += shown.pop(0)[0]


def _check_nothing_recoverable(
    report: dict[str, Any], key: str, label_key: str, spec: dict[str, dict[str, int]]
) -> None:
    floor = report["floor"]
    section = report[key]
    printed: dict[str, dict[str, int]] = {}
    for c in section["cells"]:
        printed.setdefault(c["destination"], {})[c[label_key]] = c["clicks"]
        # No printed cell, remainder included, is under the floor.
        assert c["clicks"] >= floor
    dest_totals = {d["destination"]: d["clicks"] for d in section["destinations"]}
    for link, cells in spec.items():
        big, remainder = expected_release(cells, floor)
        if not big and not remainder:
            assert link not in printed  # nothing safe to print: nothing printed
            continue
        if link not in printed:
            continue  # cut by the per-link table or --top: whole destination
        got = dict(printed[link])
        rem = got.pop(REMAINDER, 0)
        # (the country table prints ZZ as "other"; compare by value)
        assert sorted(got.values()) == sorted(big.values())
        assert rem == remainder
        assert sum(printed[link].values()) == dest_totals[link]
    # Margins are sums of printed cells only.
    assert sum(c["clicks"] for c in section["cells"]) == sum(dest_totals.values())
    if key == "click_matrix":
        by_label: dict[str, int] = {}
        for c in section["cells"]:
            by_label[c["source"]] = by_label.get(c["source"], 0) + c["clicks"]
        assert {s["source"]: s["clicks"] for s in section["sources"]} == by_label


@pytest.mark.parametrize(
    "spec",
    [
        {"twitch": {"instagram": 9, "bluesky": 3}},
        {
            "twitch": {"instagram": 9, "bluesky": 2, "tiktok": 3},
            "kick": {"instagram": 8, "direct": 7},
        },
        {"twitch": {"direct": 4}, "kick": {"direct": 8}},
        {"twitch": {"a": 5, "b": 5}, "kick": {"a": 4, "b": 4, "c": 4}},
        {"twitch": {"a": 7, "b": 6, "c": 1}, "kick": {"other": 5, "direct": 5}},
        {"twitch": {"a": 40, "b": 1}},
    ],
)
def test_matrix_never_lets_a_small_cell_be_recovered(
    tmp_path: Path, spec: dict[str, dict[str, int]]
) -> None:
    r = report_of(tmp_path, {"click_src_daily": src_rows(spec)})
    _check_nothing_recoverable(r, "click_matrix", "source", spec)


def test_the_largest_printed_cell_always_joins_the_remainder(tmp_path: Path) -> None:
    """A lone sub-floor cell is never isolated: the remainder is that cell plus
    the largest printed one (which no printed cell bounds from above), so
    printed numbers pin nothing under the floor."""
    for small in range(1, 5):
        spec = {"twitch": {"a": 30, "b": 9, "c": small}}
        m = report_of(tmp_path, {"click_src_daily": src_rows(spec)})["click_matrix"]
        got = {c["source"]: c["clicks"] for c in m["cells"]}
        assert got == {"b": 9, REMAINDER: 30 + small}
        assert got[REMAINDER] >= 5


def test_random_data_with_every_table_present(tmp_path: Path) -> None:
    rng = random.Random(409)  # noqa: S311 - test data
    labels = ["direct", "instagram", "bluesky", "other", "tiktok"]
    codes = ["US", "DE", "FR", "ZZ", "BR"]
    for _ in range(60):
        links = [f"l{i}" for i in range(rng.randint(1, 4))]
        src = {
            link: {s: rng.randint(1, 12) for s in rng.sample(labels, rng.randint(1, 4))}
            for link in links
        }
        cc = {
            link: {c: rng.randint(1, 12) for c in rng.sample(codes, rng.randint(1, 4))}
            for link in links
        }
        raw = {link: rng.randint(1, 40) for link in links}
        tables: dict[str, Any] = {
            "click_src_daily": src_rows(src),
            "click_country_daily": cc_rows(cc),
        }
        if rng.random() < 0.6:
            tables["click_events"] = raw_clicks(raw)
        r = report_of(tmp_path, tables)
        _check_nothing_recoverable(r, "click_matrix", "source", src)
        section = r["click_countries"]
        for c in section["cells"]:
            assert c["clicks"] >= r["floor"]
        dest = {d["destination"]: d["clicks"] for d in section["destinations"]}
        by_dest: dict[str, int] = {}
        for c in section["cells"]:
            by_dest[c["destination"]] = by_dest.get(c["destination"], 0) + c["clicks"]
        assert by_dest == dest
        if "click_events" in tables:
            rows = {row["label"] for row in r["links"]["rows"]}
            assert set(dest) <= rows
            assert {d["destination"] for d in r["click_matrix"]["destinations"]} <= rows


def test_the_folded_other_country_is_a_sum_of_shown_cells_only(tmp_path: Path) -> None:
    cc = {"twitch": {"US": 30, "DE": 8, "FR": 7, "BR": 6, "ZZ": 5}}
    r = report_of(tmp_path, {"click_country_daily": cc_rows(cc)}, top=2)
    got = {
        (c["country"], c["destination"]): c["clicks"]
        for c in r["click_countries"]["cells"]
    }
    assert got == {("US", "twitch"): 30, ("DE", "twitch"): 8, ("other", "twitch"): 18}
    assert r["click_countries"]["more_countries"] == 2


# ---- abuse: a visitor cannot hide a destination or pin a cell ---------------


@pytest.mark.parametrize(
    "poison",
    [
        {"unused-listed-source": 1},
        {"other": 1},  # an unlisted s value
        {"direct": 1},  # a repeated s value
        {"unused-listed-source": 1, "other": 1, "direct": 1},
    ],
)
def test_one_poisoned_source_cell_does_not_suppress_a_destination(
    tmp_path: Path, poison: dict[str, int]
) -> None:
    spec = {
        "twitch": {
            "instagram": 40,
            "bluesky": 25,
            "tiktok": 15,
            **{"x" + k: v for k, v in poison.items()},
        }
    }
    m = report_of(tmp_path, {"click_src_daily": src_rows(spec)})["click_matrix"]
    got = {c["source"]: c["clicks"] for c in m["cells"]}
    assert got["bluesky"] == 25 and got["tiktok"] == 15
    assert m["state"] == "ok"
    # The poison is inside a remainder that reaches the floor; nothing pins it.
    for k in poison:
        assert "x" + k not in got
    assert got[REMAINDER] >= 5


def test_one_new_country_does_not_suppress_a_destination(tmp_path: Path) -> None:
    cc = {"twitch": {"US": 30, "DE": 12, "QQ": 1}}
    c = report_of(tmp_path, {"click_country_daily": cc_rows(cc)})["click_countries"]
    got = {x["country"]: x["clicks"] for x in c["cells"]}
    assert got["DE"] == 12 and got[REMAINDER] == 31
    assert "QQ" not in json.dumps(c)


# ---- fixed periods: no differencing across windows -------------------------


def day_src(day: str, n: int, label: str = "a") -> list[dict[str, Any]]:
    return src_rows({"twitch": {label: n}}, day)


def test_overlapping_windows_cannot_be_asked_for(tmp_path: Path) -> None:
    """daily 3, 3, 3 with floor 6: free windows over days 1-2 and 2-3 would print
    6 and 6 and give every day away. The tool refuses a free window for these
    tables, and a fixed period prints one sum for the whole period."""
    rows = (
        day_src("2026-11-02", 3) + day_src("2026-11-03", 3) + day_src("2026-11-04", 3)
    )
    path = write(tmp_path, {"click_src_daily": rows})
    export = load_exports([path])
    for since, until in (("2026-11-02", "2026-11-03"), ("2026-11-03", "2026-11-04")):
        r = build_report(export, since=since, until=until, floor=6)
        assert r["click_matrix"]["state"] == "period_required"
        assert r["click_matrix"]["cells"] == []
    month = build_report(export, period="month", as_of=AS_OF, floor=6)
    assert [c["clicks"] for c in month["click_matrix"]["cells"]] == [9]


def test_a_period_counts_only_its_own_days(tmp_path: Path) -> None:
    rows = (
        day_src("2026-11-02", 7)  # Monday of week 2026-11-02
        + day_src("2026-11-08", 5)  # Sunday, same week
        + day_src("2026-11-09", 100)  # next week
        + day_src("2026-10-31", 200)  # previous month
    )
    export = load_exports([write(tmp_path, {"click_src_daily": rows})])
    week = build_report(export, period="week", offset=1, as_of=date(2026, 11, 16))
    assert week["release"] == {
        "period": "week",
        "offset": 1,
        "start": "2026-11-02",
        "end": "2026-11-08",
    }
    assert [c["clicks"] for c in week["click_matrix"]["cells"]] == [12]
    month = build_report(export, period="month", as_of=AS_OF)
    assert [c["clicks"] for c in month["click_matrix"]["cells"]] == [112]
    assert month["release"]["start"] == "2026-11-01"
    assert month["release"]["end"] == "2026-11-30"


def test_the_current_week_is_never_released(tmp_path: Path) -> None:
    rows = day_src("2026-12-01", 50)
    export = load_exports([write(tmp_path, {"click_src_daily": rows})])
    r = build_report(export, period="week", as_of=date(2026, 12, 2))
    assert r["release"]["end"] == "2026-11-29"
    assert r["click_matrix"]["cells"] == []


# ---- one kind of period per stretch of time --------------------------------


def _releasable(as_of: date) -> tuple[list[tuple[date, date]], list[tuple[date, date]]]:
    weeks: list[tuple[date, date]] = []
    months: list[tuple[date, date]] = []
    for kind, out in (("week", weeks), ("month", months)):
        for offset in range(0, 60):
            try:
                out.append(period_bounds(kind, offset, as_of))
            except ValueError:
                break
    return weeks, months


def test_a_week_and_a_month_never_cover_the_same_day() -> None:
    for n in range(0, 400, 3):
        as_of = date(2026, 1, 1) + timedelta(days=n)
        weeks, months = _releasable(as_of)
        assert weeks and months
        oldest = as_of - timedelta(days=89)
        for start, end in weeks:  # weeks lie inside the day-cell range, complete
            assert start >= oldest and end < as_of and start.weekday() == 0
        for start, end in months:  # months lie wholly before it
            assert end < oldest and start.day == 1
        assert max(m[1] for m in months) < min(w[0] for w in weeks)


def test_week_requests_past_the_day_cell_range_are_refused(tmp_path: Path) -> None:
    as_of = date(2027, 3, 10)  # day cells exist from 2026-12-11 (a Friday)
    weeks, months = _releasable(as_of)
    oldest = weeks[-1][0]
    assert oldest == date(2026, 12, 14)  # the week of Dec 7-13 crosses the edge
    with pytest.raises(ValueError, match="crosses its edge"):
        period_bounds("week", len(weeks), as_of)
    with pytest.raises(ValueError, match="use --period month"):
        period_bounds("week", 40, as_of)
    # the latest month ends before the edge; the month that crosses it is withheld
    assert months[0] == (date(2026, 11, 1), date(2026, 11, 30))
    assert not any(s <= date(2026, 12, 11) <= e for s, e in months)


def test_the_month_that_crosses_the_edge_is_not_released(tmp_path: Path) -> None:
    export = load_exports(
        [write(tmp_path, {"click_src_daily": day_src("2026-12-05", 50)})]
    )
    r = build_report(export, period="month", as_of=date(2027, 3, 10))
    assert r["release"]["end"] == "2026-11-30"
    assert r["click_matrix"]["cells"] == []


def test_consecutive_days_give_the_same_cells_for_the_same_period(
    tmp_path: Path,
) -> None:
    rows = (
        day_src("2026-11-03", 4) + day_src("2026-11-04", 3) + day_src("2026-11-05", 9)
    )
    export = load_exports([write(tmp_path, {"click_src_daily": rows})])
    seen = set()
    for n in range(
        7
    ):  # Monday 2026-11-16 .. Sunday 2026-11-22: same latest-but-one week
        as_of = date(2026, 11, 16) + timedelta(days=n)
        r = build_report(export, period="week", offset=1, as_of=as_of)
        assert r["release"]["start"] == "2026-11-02"
        seen.add(json.dumps(r["click_matrix"], sort_keys=True))
    assert len(seen) == 1


def test_offset_and_period_validation() -> None:
    with pytest.raises(ValueError):
        period_bounds("week", -1, date(2026, 11, 20))
    with pytest.raises(ValueError):
        period_bounds("year", 0, date(2026, 11, 20))
    with pytest.raises(ValueError):
        period_bounds("month", 30000, date(2026, 11, 20))
    assert period_bounds("week", 0, date(2026, 11, 9)) == (  # a Monday
        date(2026, 11, 2),
        date(2026, 11, 8),
    )
    assert period_bounds("month", 0, date(2027, 3, 10)) == (
        date(2026, 11, 1),
        date(2026, 11, 30),
    )


# ---- the remainder bounds nothing ------------------------------------------


def test_the_remainder_and_the_sort_order_never_bound_the_hidden_cells(
    tmp_path: Path,
) -> None:
    """Adversarial generator. A reader sees the printed cells (largest first)
    and the remainder R. Every cell moved into R is at least the largest cell
    still printed (and at least the floor), so the hidden sum is at most
    R - max(floor, largest printed). That bound must be no tighter than the
    floor - 1 a reader already knows for a single hidden cell; otherwise a hidden
    cell is pinned to a small range."""
    rng = random.Random(7)  # noqa: S311 - test data
    floor = 5
    for _ in range(3000):
        n = rng.randint(1, 7)
        cells = {
            f"s{i}": rng.choice([1, 1, 2, 3, 4, 5, 5, 6, 7, 9, 12, 30, 80])
            for i in range(n)
        }
        r = report_of(tmp_path, {"click_src_daily": src_rows({"d": cells})})
        got = {c["source"]: c["clicks"] for c in r["click_matrix"]["cells"]}
        rem = got.pop(REMAINDER, None)
        hidden = sum(v for v in cells.values() if v < floor)
        if rem is None:
            assert hidden == 0 or not got  # nothing is hidden, or nothing prints
            continue
        if got:
            assert hidden < rem  # a printed cell always joins the remainder
        bound = rem - max([floor, *got.values()])
        assert bound >= floor - 1
        if got:
            assert hidden <= bound  # the bound is true
        assert rem >= 2 * floor - 1
        assert all(v >= floor for v in got.values())
        # printed cells are exact true values
        assert all(cells[k] == v for k, v in got.items())


def test_a_remainder_that_cannot_be_made_safe_prints_nothing(tmp_path: Path) -> None:
    # one hidden cell of 3 and one printed 5: moving the 5 gives 8 < 2 * 5 - 1.
    r = report_of(tmp_path, {"click_src_daily": src_rows({"d": {"a": 5, "b": 3}})})
    assert r["click_matrix"]["cells"] == []
    assert r["click_matrix"]["state"] == "withheld"


# ---- months ----------------------------------------------------------------


def month_rows(spec: dict[str, dict[str, int]], month: str) -> list[dict[str, Any]]:
    return [
        {"site": "links", "month": month, "link_id": link, "src": s, "clicks": n}
        for link, cells in spec.items()
        for s, n in cells.items()
    ]


def test_a_whole_month_is_read_from_the_monthly_rows_and_never_twice(
    tmp_path: Path,
) -> None:
    daily = src_rows({"twitch": {"a": 6}}, day="2026-10-15")
    monthly = month_rows({"twitch": {"a": 40}}, "2026-10")
    r = report_of(
        tmp_path,
        {"click_src_daily": daily, "click_src_monthly": monthly},
        as_of=date(2027, 2, 1),
    )
    assert r["click_matrix"]["cells"][0]["clicks"] == 40
    assert r["click_matrix"]["months_used"] == ["2026-10"]


def test_a_week_inside_a_month_uses_day_cells_not_the_monthly_row(
    tmp_path: Path,
) -> None:
    daily = src_rows({"twitch": {"a": 6}}, day="2026-10-15")
    monthly = month_rows({"twitch": {"a": 40}}, "2026-10")
    r = report_of(
        tmp_path,
        {"click_src_daily": daily, "click_src_monthly": monthly},
        period="week",
        as_of=date(2026, 10, 21),
    )
    assert r["click_matrix"]["cells"][0]["clicks"] == 6
    assert r["click_matrix"]["months_used"] == []


def test_the_floor_is_applied_after_summing_days_of_the_period(tmp_path: Path) -> None:
    rows = src_rows({"twitch": {"a": 3}}, "2026-11-01") + src_rows(
        {"twitch": {"a": 3}}, "2026-11-02"
    )
    r = report_of(tmp_path, {"click_src_daily": rows})
    assert r["click_matrix"]["cells"][0]["clicks"] == 6


def test_monthly_only_export_is_accepted(tmp_path: Path) -> None:
    r = report_of(
        tmp_path,
        {
            "click_country_monthly": [
                {
                    "site": "links",
                    "month": "2026-11",
                    "link_id": "twitch",
                    "country": "US",
                    "clicks": 9,
                }
            ]
        },
    )
    assert r["click_countries"]["state"] == "ok"


# ---- output formats --------------------------------------------------------


def _files(tmp_path: Path, tables: dict[str, Any]) -> dict[str, list[list[str]]]:
    r = report_of(tmp_path, tables)
    files = csv_files(r, generated_at=datetime(2026, 11, 9, tzinfo=UTC))
    return {
        name: list(csv.reader(io.StringIO(data.decode())))
        for name, data in files.items()
    }


def test_csv_files_hold_only_printed_cells(tmp_path: Path) -> None:
    files = _files(
        tmp_path,
        {
            "click_src_daily": src_rows(
                {"twitch": {"a": 8, "b": 7}, "kick": {"a": 8, "b": 2}}
            ),
            "click_country_daily": cc_rows({"twitch": {"US": 15}}),
        },
    )
    assert files["click_matrix.csv"][0] == [
        "source",
        "destination",
        "clicks",
        "row_type",
    ]
    body = files["click_matrix.csv"][1:]
    assert sorted(body) == [
        ["(below the floor)", "kick", "10", "cell"],
        ["a", "twitch", "8", "cell"],
        ["b", "twitch", "7", "cell"],
    ]
    assert files["click_flows.csv"][1][0] == "a -> twitch"
    assert files["click_countries.csv"][1] == ["US", "twitch", "15", "cell"]


def test_csv_state_rows_when_not_in_export(tmp_path: Path) -> None:
    files = _files(tmp_path, {"kit_events": [{"ts": TS, "kind": "view"}] * 5})
    assert files["click_matrix.csv"][1] == ["", "", "", "not_in_export"]
    assert files["click_countries.csv"][1] == ["", "", "", "not_in_export"]
    assert files["click_flows.csv"][1] == ["", "", "", "not_in_export"]


def test_csv_neutralizes_a_formula_label(tmp_path: Path) -> None:
    files = _files(
        tmp_path,
        {"click_src_daily": src_rows({"twitch": {"-1": 9}})},
    )
    assert files["click_matrix.csv"][1][0] == "'-1"


def test_cli_json_and_text(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday() + 7)  # the latest complete week
    day = (monday + timedelta(days=2)).isoformat()
    path = write(
        tmp_path,
        {
            "click_src_daily": src_rows(
                {"twitch": {"instagram": 9, "direct": 40}}, day
            ),
            "click_country_daily": cc_rows({"twitch": {"US": 49}}, day),
        },
    )
    base = ["report", "--input", str(path)]
    assert main([*base, "--period", "week", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["click_matrix"]["direct_share_high"] is True
    assert data["release"]["period"] == "week"
    assert main([*base, "--period", "week"]) == 0
    out = capsys.readouterr().out
    assert "no source label (direct)" in out and "Released period" in out
    out_dir = tmp_path / "csv"
    assert main([*base, "--period", "week", "--csv", str(out_dir)]) == 0
    capsys.readouterr()
    assert oct((out_dir / "click_matrix.csv").stat().st_mode & 0o777) == "0o600"
    meta = dict(csv.reader(io.StringIO((out_dir / "metadata.csv").read_text())))
    assert meta["period"] == "week"
    assert meta["period_start_utc"] == monday.isoformat()
    assert meta["period_end_utc"] == (monday + timedelta(days=6)).isoformat()
    # a week this far back has no day cells: refused with a pointer to months
    assert main([*base, "--period", "week", "--offset", "30"]) == 2
    assert "use --period month" in capsys.readouterr().err


def test_cli_refuses_a_free_window_with_a_period(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write(tmp_path, {"click_src_daily": src_rows({"twitch": {"a": 9}})})
    for extra in (["--since", DAY], ["--until", DAY], ["--days", "5"]):
        code = main(["report", "--input", str(path), "--period", "week", *extra])
        err = capsys.readouterr().err
        assert code == 2 and "fixed calendar week or month" in err
    assert main(["report", "--input", str(path), "--offset", "2"]) == 2
    capsys.readouterr()
    # Without a period the free window still works for the aggregate sections.
    assert main(["report", "--input", str(path), "--since", DAY, "--json"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["click_matrix"]["state"]
        == "period_required"
    )


# ---- export loader ---------------------------------------------------------


@pytest.mark.parametrize(
    "table,row",
    [
        (
            "click_src_daily",
            {"day": DAY, "link_id": "a", "src": "Instagram", "clicks": 1},
        ),
        (
            "click_src_daily",
            {"day": DAY, "link_id": "a", "src": "198.51.100.7", "clicks": 1},
        ),
        ("click_src_daily", {"day": DAY, "link_id": "a", "src": "", "clicks": 1}),
        ("click_src_daily", {"day": DAY, "link_id": "a", "src": "x", "clicks": 0}),
        (
            "click_src_daily",
            {"day": DAY, "link_id": "a", "src": "x", "clicks": 1, "ip": "x"},
        ),
        (
            "click_country_daily",
            {"day": DAY, "link_id": "a", "country": "usa", "clicks": 1},
        ),
        (
            "click_country_daily",
            {"day": DAY, "link_id": "a@b.test", "country": "US", "clicks": 1},
        ),
        (
            "click_country_monthly",
            {"month": "2026-13", "link_id": "a", "country": "US", "clicks": 1},
        ),
        (
            "click_src_monthly",
            {"month": "2026-1", "link_id": "a", "src": "x", "clicks": 1},
        ),
        ("click_src_monthly", {"day": DAY, "link_id": "a", "src": "x", "clicks": 1}),
    ],
)
def test_hostile_counter_rows_are_refused(
    tmp_path: Path, table: str, row: dict[str, Any]
) -> None:
    with pytest.raises(ExportError):
        load_exports([write(tmp_path, {table: [row]})])


def test_a_repeated_counter_key_is_refused(tmp_path: Path) -> None:
    row = {"day": DAY, "link_id": "a", "src": "x", "clicks": 5}
    with pytest.raises(ExportError):
        load_exports([write(tmp_path, {"click_src_daily": [row, row]})])


def test_wrangler_blocks_and_csv_infer_the_counter_tables(tmp_path: Path) -> None:
    block = [
        {
            "results": [
                {
                    "site": "links",
                    "day": DAY,
                    "link_id": "a",
                    "country": "US",
                    "clicks": 7,
                }
            ]
        }
    ]
    path = tmp_path / "w.json"
    path.write_text(json.dumps(block), encoding="utf-8")
    assert load_exports([path]).click_country_daily[0]["clicks"] == 7
    csv_path = tmp_path / "m.csv"
    csv_path.write_text(
        "site,month,link_id,src,clicks\nlinks,2026-10,a,x,9\n", encoding="utf-8"
    )
    assert load_exports([csv_path]).click_src_monthly[0]["clicks"] == 9


def test_click_events_and_the_counters_are_told_apart(tmp_path: Path) -> None:
    ex = load_exports(
        [
            write(
                tmp_path,
                {
                    "click_events": [{"ts": TS, "link_id": "a"}],
                    "click_src_daily": [
                        {"day": DAY, "link_id": "a", "src": "x", "clicks": 5}
                    ],
                },
            )
        ]
    )
    assert len(ex.click_events) == 1 and len(ex.click_src_daily) == 1
