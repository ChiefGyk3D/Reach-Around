# SPDX-License-Identifier: Apache-2.0
"""Group rollup of the click data in ``reach-around report`` (#678).

Synthetic exports only. The privacy property under test: the rollup prints no
number that is not a sum of rows the link tables already print, and a group
under the floor prints "Under 5", never its count."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.config import load_site_config
from reach_around.funnel.csvout import csv_files
from reach_around.funnel.export import load_exports
from reach_around.funnel.groups import UNGROUPED, group_map
from reach_around.funnel.report import build_report, render_text

TS = 1_793_491_200_000  # 2026-11-01 00:00 UTC
EXAMPLE = Path(__file__).parent.parent / "examples" / "instance" / "site.yaml"


def clicks(spec: dict[str, int], kind: str = "link") -> list[dict[str, Any]]:
    return [
        {"ts": TS, "link_id": link, "destination_kind": kind}
        for link, n in spec.items()
        for _ in range(n)
    ]


def export_of(tmp_path: Path, rows: list[dict[str, Any]]) -> Any:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": {"click_events": rows}}), encoding="utf-8")
    return load_exports([path])


GROUPS = {
    "big-1": "alpha",
    "big-2": "alpha",
    "tiny-1": "beta",
    "tiny-2": "beta",
    "mixed-big": "gamma",
    "mixed-tiny": "gamma",
    "icon-a": "alpha",
}


def rollup(
    tmp_path: Path, rows: list[dict[str, Any]], **kw: Any
) -> dict[tuple[str, str], dict[str, Any]]:
    report = build_report(export_of(tmp_path, rows), groups=GROUPS, **kw)
    return {(r["group"], r["route_class"]): r for r in report["groups"]["rows"]}


def test_group_map_reads_tabs_sections_and_platforms() -> None:
    mapping = group_map(load_site_config(EXAMPLE))
    assert mapping["home-page"] == "start-here"
    assert mapping["example-mic"] == "start-here"  # a link inside a section
    assert mapping["spring-meetup"] == "events"
    assert UNGROUPED not in mapping.values()


def test_rollup_sums_by_group_and_route_class(tmp_path: Path) -> None:
    rows = (
        clicks({"big-1": 30, "big-2": 12})
        + clicks({"icon-a": 9}, "social_icon")
        + clicks({"unmapped": 7})
    )
    got = rollup(tmp_path, rows)
    assert got[("alpha", "link")]["clicks"] == 42
    assert got[("alpha", "social_icon")]["clicks"] == 9
    assert got[(UNGROUPED, "link")]["clicks"] == 7
    assert got[("alpha", "link")]["status"] == "ok"


def test_group_under_the_floor_is_never_a_number(tmp_path: Path) -> None:
    rows = clicks({"big-1": 40, "tiny-1": 2, "tiny-2": 1})
    got = rollup(tmp_path, rows)
    beta = got[("beta", "link")]
    assert beta["clicks"] is None and beta["status"] == "under_floor"
    report = build_report(export_of(tmp_path, rows), groups=GROUPS)
    text = render_text(report)
    assert "Under 5" in text
    section = text.split("Link clicks by group")[1].split("\n\n")[0]
    assert "  3" not in section and " 2 " not in section


def test_rollup_prints_only_sums_of_printed_link_rows(tmp_path: Path) -> None:
    """A group with a hidden member must not expose that member by subtraction."""
    rows = clicks({"mixed-big": 20, "mixed-tiny": 2, "big-1": 15, "tiny-1": 1})
    report = build_report(export_of(tmp_path, rows), groups=GROUPS)
    printed = {r["label"]: r["views"] for r in report["links"]["rows"]}
    got = {(r["group"], r["route_class"]): r for r in report["groups"]["rows"]}
    for (group, _), row in got.items():
        if row["clicks"] is None:
            continue
        members = [i for i, g in GROUPS.items() if g == group]
        assert row["clicks"] == sum(printed.get(m, 0) for m in members)
    # The 22 total of the gamma group is never printed beside the link rows.
    assert all(r["clicks"] != 22 for r in got.values())


def test_group_with_no_printed_row_over_the_floor_is_withheld(
    tmp_path: Path,
) -> None:
    # Two members of 3 each: the group holds 6, no member prints.
    rows = clicks({"tiny-1": 3, "tiny-2": 3, "big-1": 50})
    got = rollup(tmp_path, rows)
    assert got[("beta", "link")]["status"] == "withheld"
    assert got[("beta", "link")]["clicks"] is None


def test_no_groups_given_means_no_section(tmp_path: Path) -> None:
    report = build_report(export_of(tmp_path, clicks({"big-1": 30})))
    assert "groups" not in report
    assert "Link clicks by group" not in render_text(report)


def test_groups_do_not_change_the_other_sections(tmp_path: Path) -> None:
    rows = clicks({"big-1": 30, "tiny-1": 2, "icon-a": 8}, "link")
    export = export_of(tmp_path, rows)
    plain = build_report(export)
    grouped = build_report(export, groups=GROUPS)
    grouped.pop("groups")
    assert plain == grouped


def test_csv_has_a_groups_file_with_no_small_number(tmp_path: Path) -> None:
    rows = clicks({"big-1": 30, "big-2": 12, "tiny-1": 2})
    report = build_report(export_of(tmp_path, rows), groups=GROUPS)
    files = csv_files(report, generated_at=datetime(2026, 11, 2, tzinfo=UTC))
    table = list(csv.reader(io.StringIO(files["groups.csv"].decode())))
    assert table[0] == ["group", "route_class", "clicks", "status"]
    by = {(r[0], r[1]): r for r in table[1:]}
    assert by[("alpha", "link")][2:] == ["12", "ok"]  # 30 joined the remainder
    assert by[("beta", "link")][2:] == ["", "under_floor"]
    plain = build_report(export_of(tmp_path, rows))
    assert "groups.csv" not in csv_files(
        plain, generated_at=datetime(2026, 11, 2, tzinfo=UTC)
    )


def test_cli_config_flag_adds_the_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    rows = clicks(
        {"home-page": 30, "example-mic": 12, "example-stand": 9, "spring-meetup": 2}
    )
    path.write_text(json.dumps({"tables": {"click_events": rows}}), encoding="utf-8")
    assert main(["report", "--input", str(path), "--config", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    section = out.split("Link clicks by group")[1]
    # the largest printed link row joins the link tables' remainder line, so
    # the rollup (a sum of printed rows only) holds 12 + 9, never the hidden 30
    assert "start-here" in section and "21" in section and "30" not in section
    assert "Under 5" in section  # the events group holds 2 clicks


def test_cli_bad_config_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": {"click_events": []}}), encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nnot: a config\n", encoding="utf-8")
    assert main(["report", "--input", str(path), "--config", str(bad)]) == 2
    assert "config" in capsys.readouterr().err.lower()


def leaks(report: dict[str, Any]) -> bool:
    """True if a group row prints a figure the link tables do not account for."""
    printed = {r["label"]: r["views"] for r in report["links"]["rows"]}
    for row in report["groups"]["rows"]:
        members = [i for i, g in GROUPS.items() if g == row["group"]]
        if row["status"] != "ok":
            if row["clicks"] is not None:
                return True
        elif row["clicks"] != sum(printed.get(m, 0) for m in members):
            return True
    return False


def test_enumerated_small_datasets_print_no_figure_beyond_printed_rows(
    tmp_path: Path,
) -> None:
    import itertools

    names = ["big-1", "big-2", "tiny-1", "mixed-tiny"]
    checked = 0
    for values in itertools.product((0, 1, 4, 5, 9), repeat=len(names)):
        spec = dict(zip(names, values, strict=True))
        report = build_report(export_of(tmp_path, clicks(spec)), groups=GROUPS)
        assert not leaks(report), spec
        checked += len(report["groups"]["rows"])
    assert checked > 100


def test_guard_negative_case_the_leak_check_bites(tmp_path: Path) -> None:
    report = build_report(
        export_of(tmp_path, clicks({"big-1": 30, "big-2": 12, "tiny-1": 2})),
        groups=GROUPS,
    )
    assert not leaks(report)
    beta = next(r for r in report["groups"]["rows"] if r["group"] == "beta")
    beta["clicks"] = 2  # a regression that prints the small count
    assert leaks(report)
    beta["clicks"] = None
    alpha = next(r for r in report["groups"]["rows"] if r["group"] == "alpha")
    alpha["clicks"] += 30  # a total that includes a row the tables hide
    assert leaks(report)
