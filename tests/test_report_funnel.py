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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel.csvout import csv_files
from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.report import build_report, render_text

DAY = "2026-11-01"
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


def report_of(tmp_path: Path, tables: dict[str, Any], *argv: str) -> dict[str, Any]:
    out = build_report(load_exports([write(tmp_path, tables)]))
    return out


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


def test_a_destination_with_a_small_cell_prints_none_of_its_cells(
    tmp_path: Path,
) -> None:
    src = {"twitch": {"instagram": 12, "direct": 3}, "kick": {"instagram": 9}}
    r = report_of(tmp_path, {"click_src_daily": src_rows(src)})
    m = r["click_matrix"]
    assert {c["destination"] for c in m["cells"]} == {"kick"}
    assert m["links_withheld"] == 1
    assert "twitch" not in json.dumps(m)
    assert '"source": "direct"' not in json.dumps(m)


def test_everything_small_is_withheld(tmp_path: Path) -> None:
    r = report_of(tmp_path, {"click_src_daily": src_rows({"twitch": {"a": 2, "b": 4}})})
    assert r["click_matrix"]["state"] == "withheld"
    assert "nothing shown" in render_text(r)


def test_tables_missing_from_the_export_say_so(tmp_path: Path) -> None:
    r = report_of(tmp_path, {"kit_events": [{"ts": TS, "kind": "view"}] * 6})
    assert r["click_matrix"]["state"] == "not_in_export"
    assert r["click_countries"]["state"] == "not_in_export"
    assert "not in this export" in render_text(r)


def test_country_by_destination_counts_clicks_the_raw_rows_do_not(
    tmp_path: Path,
) -> None:
    # 20 clicks on twitch, 12 with a source (so the raw rows keep no country).
    # The counter table holds the country of all 20.
    cc = {"twitch": {"US": 11, "DE": 9}}
    r = report_of(
        tmp_path,
        {
            "click_events": [{"ts": TS, "link_id": "twitch", "src": "instagram"}] * 12
            + [{"ts": TS, "link_id": "twitch", "country": "US"}] * 8,
            "click_country_daily": cc_rows(cc),
        },
    )
    assert sum(x["clicks"] for x in r["click_countries"]["cells"]) == 20


def test_destinations_follow_the_links_section_when_raw_clicks_are_present(
    tmp_path: Path,
) -> None:
    # twitch (30) and kick (6): the links table folds kick away (its smallest
    # shown row joins the merged line), so the breakdown for kick is not printed
    # even though every kick cell is at the floor.
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


def _printed_numbers(report: dict[str, Any]) -> set[int]:
    """Every integer the report prints in the three click sections and links."""
    out: set[int] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in (
                    "clicks",
                    "views",
                    "other_below_floor",
                    "ranked_other",
                ) and isinstance(value, int):
                    out.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for key in ("click_matrix", "click_countries", "links", "click_sources"):
        walk(report[key])
    return out


def _check_nothing_recoverable(
    report: dict[str, Any], key: str, label_key: str, spec: dict[str, dict[str, int]]
) -> None:
    floor = report["floor"]
    section = report[key]
    printed: dict[str, dict[str, int]] = {}
    for c in section["cells"]:
        printed.setdefault(c["destination"], {})[c[label_key]] = c["clicks"]
    dest_totals = {d["destination"]: d["clicks"] for d in section["destinations"]}
    for link, cells in spec.items():
        small = any(n < floor for n in cells.values())
        if small:
            # no part of the link is printed: not a cell, not a margin
            assert link not in printed and link not in dest_totals
            continue
        if link in printed:
            assert sum(printed[link].values()) == dest_totals[link]
    # Margins are sums of printed cells only
    shown_total = sum(c["clicks"] for c in section["cells"])
    assert shown_total == sum(dest_totals.values())
    # No printed integer below the floor except exact zero
    for n in _printed_numbers(report):
        assert n == 0 or n >= floor
    # The label totals (matrix) are sums of printed cells too
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
        {"twitch": {"a": 7, "b": 6}, "kick": {"other": 5, "direct": 5}},
    ],
)
def test_matrix_never_lets_a_small_cell_be_recovered(
    tmp_path: Path, spec: dict[str, dict[str, int]]
) -> None:
    r = report_of(tmp_path, {"click_src_daily": src_rows(spec)})
    _check_nothing_recoverable(r, "click_matrix", "source", spec)


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
        # the country section folds the long tail per destination, from shown cells only
        section = r["click_countries"]
        for c in section["cells"]:
            assert c["clicks"] >= r["floor"]
        dest = {d["destination"]: d["clicks"] for d in section["destinations"]}
        by_dest: dict[str, int] = {}
        for c in section["cells"]:
            by_dest[c["destination"]] = by_dest.get(c["destination"], 0) + c["clicks"]
        assert by_dest == dest
        for link in dest:
            assert all(n >= r["floor"] for n in cc[link].values())
        # a link printed in either section is also a printed row of the links table
        if "click_events" in tables:
            rows = {row["label"] for row in r["links"]["rows"]}
            assert set(dest) <= rows
            assert {d["destination"] for d in r["click_matrix"]["destinations"]} <= rows


def test_the_folded_other_country_is_a_sum_of_shown_cells_only(tmp_path: Path) -> None:
    cc = {"twitch": {"US": 30, "DE": 8, "FR": 7, "BR": 6, "ZZ": 5}}
    r = build_report(
        load_exports([write(tmp_path, {"click_country_daily": cc_rows(cc)})]), top=2
    )
    got = {
        (c["country"], c["destination"]): c["clicks"]
        for c in r["click_countries"]["cells"]
    }
    assert got == {("US", "twitch"): 30, ("DE", "twitch"): 8, ("other", "twitch"): 18}
    assert r["click_countries"]["more_countries"] == 2


# ---- months and windows ----------------------------------------------------


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
    )
    assert r["click_matrix"]["cells"][0]["clicks"] == 40
    assert r["click_matrix"]["months_used"] == ["2026-10"]


def test_a_partial_month_in_the_window_uses_day_cells(tmp_path: Path) -> None:
    daily = src_rows({"twitch": {"a": 6}}, day="2026-10-15")
    monthly = month_rows({"twitch": {"a": 40}}, "2026-10")
    r = build_report(
        load_exports(
            [write(tmp_path, {"click_src_daily": daily, "click_src_monthly": monthly})]
        ),
        since="2026-10-10",
        until="2026-11-30",
    )
    assert r["click_matrix"]["cells"][0]["clicks"] == 6
    assert r["click_matrix"]["months_used"] == []


def test_the_floor_is_applied_after_summing_days(tmp_path: Path) -> None:
    # 3 + 3 clicks on two days: each day is under the floor, the sum is not.
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
                    "month": "2026-09",
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
    assert sorted(body) == [["a", "twitch", "8", "cell"], ["b", "twitch", "7", "cell"]]
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
    path = write(
        tmp_path,
        {
            "click_src_daily": src_rows({"twitch": {"instagram": 9, "direct": 12}}),
            "click_country_daily": cc_rows({"twitch": {"US": 21}}),
        },
    )
    assert main(["report", "--input", str(path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["click_matrix"]["direct_share_high"] is True  # 12 of 21
    assert main(["report", "--input", str(path)]) == 0
    out = capsys.readouterr().out
    assert "no source label (direct)" in out
    out_dir = tmp_path / "csv"
    assert main(["report", "--input", str(path), "--csv", str(out_dir)]) == 0
    assert (out_dir / "click_matrix.csv").exists()
    assert oct((out_dir / "click_matrix.csv").stat().st_mode & 0o777) == "0o600"


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
