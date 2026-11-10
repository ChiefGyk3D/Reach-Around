# SPDX-License-Identifier: Apache-2.0
"""Gap detection (issue #402): a missing day is a gap with a reason, never a zero.

Seeded coverage rows and hand-written exports only; nothing is read from a live system.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel import grafana
from reach_around.funnel.csvout import csv_files
from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.gaps import classify, coverage_for_window
from reach_around.funnel.report import build_report, render_text

TODAY = date(2026, 11, 9)
OK = "o" * 24


def cov(day: str, hours: str = OK) -> dict[str, str]:
    return {"day": day, "hours": hours}


def view(day: str, views: int = 40) -> dict[str, Any]:
    return {
        "site": "links", "day": day, "day_tz": "UTC", "page": "home", "src_kind": "d",
        "src": "", "country": "US", "bot_class": "h", "views": views, "uniques": 1,
    }  # fmt: skip


def seeded() -> list[dict[str, str]]:
    """Nov 1 first day; 2 ok, 3 missing, 4 killed, 5 budget, 6 partial, 7-8 ok."""
    return [
        cov("2026-11-01", "-" * 10 + "o" * 14),
        cov("2026-11-02"),
        cov("2026-11-04", "k" * 24),
        cov("2026-11-05", "q" * 24),
        cov("2026-11-06", "o" * 10 + "-" * 3 + "o" * 11),
        cov("2026-11-07"),
        cov("2026-11-08"),
    ]


def test_classify_whole_and_partial_days() -> None:
    assert classify(OK) is None
    assert classify("-" * 24) is not None
    assert classify("-" * 24).status == "gap"  # type: ignore[union-attr]
    assert classify("-" * 24).reasons == ("outage",)  # type: ignore[union-attr]
    mixed = classify("o" * 20 + "k" * 3 + "q")
    assert mixed is not None
    assert mixed.status == "partial"
    assert mixed.reasons == ("kill_switch", "quota")


def test_a_missing_day_is_an_outage_gap_and_other_reasons_are_named() -> None:
    c = coverage_for_window(seeded(), True, "2026-11-01", "2026-11-08", TODAY)
    assert [(g.day, g.reasons[0]) for g in c.gaps] == [
        ("2026-11-03", "outage"),
        ("2026-11-04", "kill_switch"),
        ("2026-11-05", "quota"),
    ]
    assert [(p.day, p.reasons) for p in c.partial] == [("2026-11-06", ("outage",))]
    assert c.unmonitored_days == 1  # the first covered day is not judged
    assert c.spans_gap


def test_a_quiet_but_collected_day_is_not_a_gap() -> None:
    c = coverage_for_window(
        [cov("2026-11-01"), cov("2026-11-02"), cov("2026-11-03")],
        True,
        None,
        "2026-11-03",
        TODAY,
    )
    assert not c.spans_gap
    assert c.as_dict()["status"] == "complete"


def test_today_and_days_before_monitoring_are_never_judged() -> None:
    rows = [cov("2026-11-05"), cov("2026-11-06", "-" * 24)]
    c = coverage_for_window(rows, True, "2026-10-01", "2026-11-09", TODAY)
    assert (
        c.unmonitored_days == 36
    )  # 1 October to 5 November, the first covered day included
    assert [g.day for g in c.gaps] == [
        "2026-11-06",
        "2026-11-07",
        "2026-11-08",
    ]  # today (11-09) is not judged
    assert all(g.day >= "2026-11-06" for g in c.gaps)


def test_no_coverage_table_makes_no_claim() -> None:
    c = coverage_for_window([], False, None, None, TODAY)
    assert not c.monitored
    assert c.as_dict()["status"] == "not_exported"
    assert c.as_dict()["gap_days"] == 0


def test_window_is_respected() -> None:
    c = coverage_for_window(seeded(), True, "2026-11-04", "2026-11-05", TODAY)
    assert [g.day for g in c.gaps] == ["2026-11-04", "2026-11-05"]
    assert c.partial == []


# ---- the export loader ----------------------------------------------------


def test_the_export_accepts_coverage_and_refuses_bad_rows(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"tables": {"analytics_coverage": seeded()}}), encoding="utf-8"
    )
    assert len(load_exports([good]).analytics_coverage) == 7
    for bad in (
        cov("2026-11-01", "o" * 23),
        cov("2026-11-01", "o" * 23 + "x"),
        cov("2026-11-01", "https://example.test/xxxxxxxx"),
        cov("2026-13-01"),
    ):
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps({"tables": {"analytics_coverage": [bad]}}), encoding="utf-8"
        )
        with pytest.raises(ExportError):
            load_exports([path])
    dup = tmp_path / "dup.json"
    dup.write_text(
        json.dumps(
            {"tables": {"analytics_coverage": [cov("2026-11-01"), cov("2026-11-01")]}}
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExportError):
        load_exports([dup])


def _export(tmp_path: Path, name: str = "e.json") -> Path:
    views = [
        view(f"2026-11-0{d}") for d in (1, 2, 4, 5, 6, 7, 8)
    ]  # no view rows for 11-03: a gap, not a zero
    path = tmp_path / name
    path.write_text(
        json.dumps({"tables": {"view_daily": views, "analytics_coverage": seeded()}}),
        encoding="utf-8",
    )
    return path


# ---- report ---------------------------------------------------------------


def test_report_lists_gaps_with_reasons_and_says_totals_cover_collected_time(
    tmp_path: Path,
) -> None:
    report = build_report(
        load_exports([_export(tmp_path)]), as_of=TODAY, until="2026-11-08"
    )
    c = report["coverage"]
    assert c["status"] == "gaps"
    assert (c["gap_days"], c["partial_days"]) == (3, 1)
    text = render_text(report)
    assert "Collection gaps: 3 full-day gaps, 1 partial days" in text
    assert "2026-11-03  gap (outage)" in text
    assert "2026-11-04  gap (kill switch)" in text
    assert "2026-11-05  gap (quota)" in text
    assert "it is not a zero" in text
    # The note comes before the first figure.
    assert text.index("Collection gaps") < text.index("Funnel")


def test_report_labels_kit_figures_when_the_window_spans_a_gap(
    tmp_path: Path,
) -> None:
    path = _export(tmp_path, "kit-gap.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tables"]["kit_events"] = [
        {"ts": 1_794_096_000_000, "kind": "view"} for _ in range(5)
    ]
    path.write_text(json.dumps(data), encoding="utf-8")

    report = build_report(load_exports([path]), as_of=TODAY, until="2026-11-08")
    text = render_text(report)

    assert report["funnel"]["spans_collection_gap"] is True
    assert "Kit views (spans collection gap)" in text
    assert "Kit unlocks (spans collection gap)" in text


def test_report_without_gaps_or_coverage_is_unchanged_text(tmp_path: Path) -> None:
    only_views = tmp_path / "v.json"
    only_views.write_text(
        json.dumps({"tables": {"view_daily": [view("2026-11-01")]}}), encoding="utf-8"
    )
    r = build_report(load_exports([only_views]), as_of=TODAY)
    assert r["coverage"]["status"] == "not_exported"
    assert "Collection gaps" not in render_text(r)
    clean = tmp_path / "c.json"
    clean.write_text(
        json.dumps(
            {
                "tables": {
                    "view_daily": [view("2026-11-02")],
                    "analytics_coverage": [cov("2026-11-01"), cov("2026-11-02")],
                }
            }
        ),
        encoding="utf-8",
    )
    r2 = build_report(load_exports([clean]), as_of=TODAY)
    assert r2["coverage"]["status"] == "complete"
    assert "Collection gaps" not in render_text(r2)


def test_report_json_holds_only_dates_and_closed_words(tmp_path: Path) -> None:
    report = build_report(
        load_exports([_export(tmp_path)]), as_of=TODAY, until="2026-11-08"
    )
    allowed = {"outage", "quota", "kill_switch"}
    for g in report["coverage"]["gaps"]:
        assert set(g) == {"day", "reason"} and g["reason"] in allowed
    for p in report["coverage"]["partial"]:
        assert set(p) == {"day", "reasons"} and set(p["reasons"]) <= allowed


def test_csv_metadata_and_gaps_file(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    report = build_report(
        load_exports([_export(tmp_path)]), as_of=TODAY, until="2026-11-08"
    )
    files = csv_files(report, generated_at=datetime(2026, 11, 9, tzinfo=UTC))
    meta = files["metadata.csv"].decode()
    assert "coverage_status,gaps" in meta and "coverage_gap_days,3" in meta
    assert files["gaps.csv"].decode().splitlines()[1:] == [
        "2026-11-03,gap,outage",
        "2026-11-04,gap,kill_switch",
        "2026-11-05,gap,quota",
        "2026-11-06,partial,outage",
    ]
    clean = build_report(
        load_exports([_export(tmp_path)]),
        as_of=TODAY,
        since="2026-11-07",
        until="2026-11-08",
    )
    assert "gaps.csv" not in csv_files(
        clean, generated_at=datetime(2026, 11, 9, tzinfo=UTC)
    )


# ---- Grafana export -------------------------------------------------------


def test_grafana_marks_gaps_and_writes_no_zero_rows(tmp_path: Path) -> None:
    export = load_exports([_export(tmp_path)])
    out = tmp_path / "g.sqlite3"
    grafana.write_store(out, grafana.build_subset(export, today=TODAY, floor=5))
    con = sqlite3.connect(f"file:{out}?mode=ro", uri=True)
    try:
        gaps = con.execute(
            "SELECT day, status, reason FROM grafana_gaps_daily ORDER BY day"
        ).fetchall()
        view_days = {r[0] for r in con.execute("SELECT day FROM grafana_views_daily")}
        zeros = con.execute(
            "SELECT COUNT(*) FROM grafana_views_daily WHERE views = 0"
        ).fetchone()[0]
    finally:
        con.close()
    assert gaps == [
        ("2026-11-03", "gap", "outage"),
        ("2026-11-04", "gap", "kill_switch"),
        ("2026-11-05", "gap", "quota"),
        ("2026-11-06", "partial", "outage"),
    ]
    assert "2026-11-03" not in view_days  # the gap day has no row at all
    assert zeros == 0


def test_grafana_cli_runs_with_coverage(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "grafana-export",
            "--input",
            str(_export(tmp_path)),
            "--output",
            str(tmp_path / "g.sqlite3"),
        ]
    )
    out = capsys.readouterr()
    assert code == 0, out.err
    assert "grafana_gaps_daily" in out.out


# ---- review findings (Copilot, PR 480) ------------------------------------


def test_an_empty_coverage_table_is_not_monitored_never_complete() -> None:
    c = coverage_for_window([], True, None, None, TODAY)
    assert not c.monitored
    assert c.as_dict()["status"] == "not_monitored"
    assert c.as_dict()["gap_days"] == 0


def test_coverage_is_labeled_account_wide() -> None:
    c = coverage_for_window(seeded(), True, None, "2026-11-08", TODAY).as_dict()
    assert c["scope"] == "account"
    assert "account-wide" in c["note"]


def test_some_sites_off_is_partial_never_a_full_gap() -> None:
    day = classify("p" * 24)
    assert day is not None
    assert (day.status, day.reasons) == ("partial", ("site_kill_switch",))
    rows = [cov("2026-11-01"), cov("2026-11-02", "p" * 24), cov("2026-11-03")]
    c = coverage_for_window(rows, True, None, "2026-11-03", TODAY)
    assert c.gaps == []
    assert [p.day for p in c.partial] == ["2026-11-02"]


def test_the_export_accepts_the_p_letter(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    path.write_text(
        json.dumps({"tables": {"analytics_coverage": [cov("2026-11-01", "p" * 24)]}}),
        encoding="utf-8",
    )
    assert load_exports([path]).analytics_coverage[0]["hours"] == "p" * 24


def test_grafana_judges_the_tail_up_to_the_newest_observed_day(tmp_path: Path) -> None:
    # Coverage stops on 11-03 but views exist for 11-06: 11-04 to 11-06 are gaps.
    data = {
        "tables": {
            "view_daily": [view("2026-11-02"), view("2026-11-06")],
            "analytics_coverage": [
                cov("2026-11-01"),
                cov("2026-11-02"),
                cov("2026-11-03"),
            ],
        }
    }
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    subset = grafana.build_subset(load_exports([path]), today=TODAY)
    assert [(g[0], g[2]) for g in subset.gaps] == [
        ("2026-11-04", "outage"),
        ("2026-11-05", "outage"),
        ("2026-11-06", "outage"),
    ]
