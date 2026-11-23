# SPDX-License-Identifier: Apache-2.0
"""Group rollup of the click data in ``reach-around report`` (#678).

Synthetic exports only. The privacy property under test: the rollup is a pure
function of the link rows the report prints. It never classifies a group from a
hidden total, so two configs that group the same hidden ids differently cannot
tell a reader anything (the threshold oracle)."""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.config import load_site_config
from reach_around.funnel.csvout import csv_files
from reach_around.funnel.export import load_exports
from reach_around.funnel.groups import UNGROUPED, group_map
from reach_around.funnel.report import _build_report, build_report, render_text

TS = 1_793_491_200_000  # 2026-11-01 00:00 UTC
TS2 = TS + 86_400_000  # 2026-11-02
EXAMPLE = Path(__file__).parent.parent / "examples" / "instance" / "site.yaml"
NOW = datetime(2026, 11, 3, tzinfo=UTC)


def clicks(
    spec: Mapping[str, int], kind: str = "link", ts: int = TS
) -> list[dict[str, Any]]:
    return [
        {"ts": ts, "link_id": link, "destination_kind": kind}
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
CONFIG_A = {f"one-{n}": f"solo-{n}" for n in range(1, 5)}  # four groups
CONFIG_B = {f"one-{n}": "together" for n in range(1, 5)}  # one group
for _cfg in (CONFIG_A, CONFIG_B):
    _cfg.update({"big-1": "alpha", "big-2": "alpha", "icon-a": "alpha"})


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


def test_rollup_sums_printed_rows_by_group_and_route_class(tmp_path: Path) -> None:
    # No row is hidden here, so every link prints and the sums are complete.
    rows = (
        clicks({"big-1": 30, "big-2": 12})
        + clicks({"icon-a": 9}, "social_icon")
        + clicks({"unmapped": 7})
    )
    got = rollup(tmp_path, rows)
    assert got[("alpha", "link")]["clicks"] == 42
    assert got[("alpha", "social_icon")]["clicks"] == 9
    assert got[(UNGROUPED, "link")]["clicks"] == 7
    assert {r["status"] for r in got.values()} == {"ok"}


def test_no_groups_given_means_no_section(tmp_path: Path) -> None:
    report = build_report(export_of(tmp_path, clicks({"big-1": 30})))
    assert "groups" not in report
    assert "Link clicks by group" not in render_text(report)


def test_groups_do_not_change_the_other_sections_or_the_core_digest(
    tmp_path: Path,
) -> None:
    export = export_of(tmp_path, clicks({"big-1": 30, "tiny-1": 2, "icon-a": 8}))
    plain, plain_digest = _build_report(export)
    grouped, grouped_digest = _build_report(export, groups=GROUPS)
    assert grouped.pop("groups")
    assert plain == grouped
    assert plain_digest == grouped_digest  # the legacy ledger digest is unchanged


PARAMS = {"floor": 5, "top": 10, "period": None, "offset": 0, "kind": None}


def test_rollup_digest_matches_the_reference_and_binds_printed_inputs(
    tmp_path: Path,
) -> None:
    export = export_of(tmp_path, clicks({"one-1": 1, "big-1": 30, "big-2": 12}))
    for config in (CONFIG_A, GROUPS, {}):
        report, _ = _build_report(export, groups=config)
        want = reference_digest(report, config, {**PARAMS, "period": report["period"]})
        assert report["groups"]["rollup_digest"] == want
    # It differs when a PRINTED id moves group, and with top and window.
    moved = {**CONFIG_A, "big-2": "elsewhere"}
    base = build_report(export, groups=CONFIG_A)["groups"]["rollup_digest"]
    assert base != build_report(export, groups=moved)["groups"]["rollup_digest"]
    assert (
        base != build_report(export, groups=CONFIG_A, top=3)["groups"]["rollup_digest"]
    )
    raised = build_report(export, groups=CONFIG_A, floor=6)
    assert base != raised["groups"]["rollup_digest"]
    assert "rollup digest" not in render_text(build_report(export))
    assert "rollup digest (v1)" in render_text(build_report(export, groups=CONFIG_A))


def test_hidden_id_group_does_not_change_the_digest(tmp_path: Path) -> None:
    rows = clicks({"one-1": 1, "big-1": 30, "big-2": 12})
    export = export_of(tmp_path, rows)
    base = build_report(export, groups=CONFIG_A)
    for other in ("solo-9", "alpha", "together"):
        report = build_report(export, groups={**CONFIG_A, "one-1": other})
        assert report["groups"]["rollup_digest"] == base["groups"]["rollup_digest"]
        assert render_all(report) == render_all(base)


def test_digest_ignores_hidden_cells_and_cannot_be_brute_forced(
    tmp_path: Path,
) -> None:
    """Two exports with the same printed rows and different hidden cells give
    the SAME rollup digest, so testing candidate hidden counts against the
    printed digest cannot tell them apart (the reviewer's attack)."""
    base = {"big-1": 30, "big-2": 12}
    true = build_report(
        export_of(tmp_path, clicks({**base, "one-1": 1})), groups=CONFIG_A
    )
    target = true["groups"]["rollup_digest"]
    matching = []
    for guess in range(0, 5):
        rows = clicks({**base, "one-1": guess})
        got = build_report(export_of(tmp_path, rows), groups=CONFIG_A)
        if got["groups"]["rollup_digest"] == target:
            matching.append(guess)
    # 0 hidden changes what prints (nothing is hidden, so 30 prints); every
    # guess that hides the id gives the same digest: no candidate is pinned.
    assert matching == [1, 2, 3, 4]
    # Different hidden ids and totals, same printed rows: same digest too.
    other = build_report(
        export_of(tmp_path, clicks({**base, "one-2": 3, "one-3": 4})),
        groups=CONFIG_A,
    )
    assert other["groups"]["rollup_digest"] == target
    assert render_all(other) == render_all(true)


def test_negative_a_digest_over_private_cells_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A digest that also hashes a hidden cell (as the core digest did) makes
    every guess distinguishable, so the brute force above would work."""
    from reach_around.funnel import report as report_mod
    from reach_around.funnel.groups import rollup_digest as real

    base = {"big-1": 30, "big-2": 12}
    seen = set()
    for guess in (1, 2, 3, 4):
        monkeypatch.setattr(
            report_mod,
            "group_rollup_digest",
            lambda e, sec, p, g=guess: real(e, sec, {**p, "hidden": g}),
        )
        export = export_of(tmp_path, clicks({**base, "one-1": guess}))
        seen.add(build_report(export, groups=CONFIG_A)["groups"]["rollup_digest"])
    assert len(seen) == 4  # each guess is distinguishable: the oracle


BASE_PARAMS = {"floor": 5, "top": 10, "period": {"a": 1}, "offset": 0, "kind": None}
BASE_ROWS = {
    "state": "ok",
    "rows": [{"group": "g", "route_class": "link", "clicks": 12, "status": "ok"}],
}
BASE_ASSIGN = [["x", "g", "link"]]


def digest_of(**changes: Any) -> str:
    from reach_around.funnel.groups import rollup_digest

    params = {**BASE_PARAMS, **{k: v for k, v in changes.items() if k in BASE_PARAMS}}
    section = changes.get("section", BASE_ROWS)
    return rollup_digest(changes.get("assign", BASE_ASSIGN), section, params)


@pytest.mark.parametrize(
    "change",
    [
        {"floor": 6},
        {"top": 3},
        {"period": {"a": 2}},
        {"offset": 1},
        {"kind": "week"},
        {"section": {**BASE_ROWS, "state": "withheld"}},
        {"section": {"state": "ok", "rows": []}},
        {"assign": [["x", "h", "link"]]},
        {"assign": [["x", "g", "social_icon"]]},
    ],
)
def test_every_bound_input_changes_the_digest(change: dict[str, Any]) -> None:
    assert digest_of(**change) != digest_of()
    assert digest_of() == digest_of()


def test_section_is_skipped_when_a_link_table_is_withheld(tmp_path: Path) -> None:
    # Links: every row under the floor, so that table is withheld.
    rows = clicks({"big-1": 40}, "social_icon") + clicks({"tiny-1": 2, "tiny-2": 1})
    report = build_report(export_of(tmp_path, rows), groups=GROUPS)
    assert report["links"]["state"] == "withheld"
    assert report["groups"]["state"] == "withheld"
    assert report["groups"]["rows"] == []


# ---- the oracle -------------------------------------------------------------


def render_all(report: dict[str, Any]) -> dict[str, str]:
    """Everything a reader sees of the rollup, digest included: the text
    section, the JSON object, groups.csv and metadata.csv."""
    text = render_text(report).split("Link clicks by group")[1].split("\n\n")[0]
    files = csv_files(report, generated_at=NOW)
    return {
        "text": text.split("\n  A link's breakdown")[0],
        "json": json.dumps(report["groups"], sort_keys=True),
        "groups_csv": files["groups.csv"].decode("utf-8"),
        "metadata_csv": files["metadata.csv"].decode("utf-8"),
    }


def reference_digest(
    report: dict[str, Any], groups: Mapping[str, str], params: dict[str, Any]
) -> str:
    """Independent reference. It binds exactly: the printed (id, group, route
    class) triples, the group rows and state, and the parameters. Nothing
    private: no core digest, no hidden total, no hidden id's group."""
    triples = sorted(
        [r["label"], groups.get(r["label"], UNGROUPED), kind]
        for key, kind in (("links", "link"), ("social_icons", "social_icon"))
        for r in report[key]["rows"]
    )
    blob = json.dumps(
        {
            "version": 1,
            "assignments": triples,
            "rows": report["groups"]["rows"],
            "state": report["groups"]["state"],
            "params": params,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def expected_rows(report: dict[str, Any], groups: Mapping[str, str]) -> set[Any]:
    sums: dict[tuple[str, str], int] = {}
    for key, route_class in (("links", "link"), ("social_icons", "social_icon")):
        for row in report[key]["rows"]:
            cell = (groups.get(row["label"], UNGROUPED), route_class)
            sums[cell] = sums.get(cell, 0) + row["views"]
    return {(g, c, n) for (g, c), n in sums.items()}


def leaks(report: dict[str, Any], groups: Mapping[str, str]) -> bool:
    """True if the rollup is anything but the sums of the printed link rows:
    a row from a hidden total, a missing or extra row, or a status other than
    ok (rows present or absent are part of what a reader sees)."""
    section = report["groups"]
    if section["state"] != "ok":
        return bool(section["rows"])
    if any(r["status"] != "ok" or r["clicks"] is None for r in section["rows"]):
        return True
    got = {(r["group"], r["route_class"], r["clicks"]) for r in section["rows"]}
    return got != expected_rows(report, groups)


def old_classification(
    groups: Mapping[str, str], totals: Mapping[str, int], floor: int = 5
) -> dict[str, str]:
    """The first version: a status from the TRUE group total (the oracle)."""
    sums: dict[str, int] = {}
    for ident, n in totals.items():
        group = groups.get(ident, UNGROUPED)
        sums[group] = sums.get(group, 0) + n
    return {g: ("under_floor" if n < floor else "withheld") for g, n in sums.items()}


def test_threshold_oracle_four_one_click_ids_under_two_configs(
    tmp_path: Path,
) -> None:
    spec = {"one-1": 1, "one-2": 1, "one-3": 1, "one-4": 1}
    rows = clicks({**spec, "big-1": 30, "big-2": 12}) + clicks(
        {"icon-a": 9}, "social_icon"
    )
    shown = {}
    for name, config in (("a", CONFIG_A), ("b", CONFIG_B)):
        report = build_report(export_of(tmp_path, rows), groups=config)
        assert not leaks(report, config)
        shown[name] = render_all(report)
    # Identical output under both configs, in every rendering: the hidden ids
    # contribute nothing, so nothing pins any of them to exactly 1.
    assert shown["a"] == shown["b"]
    assert "solo-" not in json.dumps(shown) and "together" not in json.dumps(shown)


def test_hidden_totals_do_not_change_the_output(tmp_path: Path) -> None:
    """A group with no printed row looks the same at 1, 4 or many hidden clicks."""
    seen = set()
    for hidden in (1, 4):
        for count in (1, 2, 4):
            spec = {f"one-{n}": hidden for n in range(1, count + 1)}
            rows = clicks({**spec, "big-1": 30, "big-2": 12})
            report = build_report(export_of(tmp_path, rows), groups=CONFIG_A)
            assert not leaks(report, CONFIG_A)
            seen.add(json.dumps(render_all(report), sort_keys=True))
    assert len(seen) == 1


@pytest.mark.parametrize("top", [1, 2, 10])
@pytest.mark.parametrize("window", [(None, None), ("2026-11-02", "2026-11-02")])
def test_leak_check_across_configs_tops_and_windows(
    tmp_path: Path, top: int, window: tuple[str | None, str | None]
) -> None:
    names = ["big-1", "big-2", "tiny-1", "ghost"]
    checked = 0
    for values in itertools.product((0, 1, 5, 9), repeat=len(names)):
        spec = dict(zip(names, values, strict=True))
        rows = (
            clicks(spec)
            + clicks({"icon-a": values[0], "ghost": values[2]}, "social_icon", TS2)
            + clicks({"big-2": values[1]}, "link", TS2)
        )
        for config in (GROUPS, CONFIG_A, {}):
            report = build_report(
                export_of(tmp_path, rows),
                groups=config,
                top=top,
                since=window[0],
                until=window[1],
            )
            assert not leaks(report, config), (spec, top, window)
            renders = render_all(report)
            assert json.loads(renders["json"])["rows"] == report["groups"]["rows"]
            checked += 1
    assert checked > 500


def test_negative_old_classification_is_an_oracle_and_the_check_bites(
    tmp_path: Path,
) -> None:
    totals = {"one-1": 1, "one-2": 1, "one-3": 1, "one-4": 1}
    # The old rule: A shows four "Under 5" groups, B shows one: a reader
    # learns each id is at least 1 (A) and the sum at most 4 (B), so each is 1.
    assert old_classification(CONFIG_A, totals) != old_classification(CONFIG_B, totals)
    # The current rule gives one answer under both configs.
    rows = clicks({**totals, "big-1": 30, "big-2": 12})
    outs = [
        render_all(build_report(export_of(tmp_path, rows), groups=c))
        for c in (CONFIG_A, CONFIG_B)
    ]
    assert outs[0] == outs[1]
    # leaks() goes red when a row comes from a hidden total (old behavior).
    report = build_report(export_of(tmp_path, rows), groups=CONFIG_A)
    assert not leaks(report, CONFIG_A)
    report["groups"]["rows"].append(
        {
            "group": "solo-1",
            "route_class": "link",
            "clicks": None,
            "status": "under_floor",
        }
    )
    assert leaks(report, CONFIG_A)
    report["groups"]["rows"].pop()
    report["groups"]["rows"][0]["clicks"] += 30  # a total with a hidden row
    assert leaks(report, CONFIG_A)


# ---- CLI and CSV ------------------------------------------------------------


def test_csv_has_a_groups_file_and_the_digest_only_with_groups(tmp_path: Path) -> None:
    rows = clicks({"big-1": 30, "big-2": 12, "tiny-1": 2})
    export = export_of(tmp_path, rows)
    report = build_report(export, groups=GROUPS)
    files = csv_files(report, generated_at=NOW)
    table = list(csv.reader(io.StringIO(files["groups.csv"].decode())))
    assert table[0] == ["group", "route_class", "clicks", "status"]
    assert {tuple(r) for r in table[1:]} == {("alpha", "link", "12", "ok")}
    meta = files["metadata.csv"].decode()
    assert "rollup_digest_v1" in meta
    plain = csv_files(build_report(export), generated_at=NOW)
    assert (
        "groups.csv" not in plain
        and "rollup_digest" not in plain["metadata.csv"].decode()
    )


def test_cli_config_flag_adds_the_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    rows = clicks(
        {"home-page": 30, "example-mic": 12, "example-stand": 9, "spring-meetup": 2}
    )
    path.write_text(json.dumps({"tables": {"click_events": rows}}), encoding="utf-8")
    assert main(["report", "--input", str(path)]) == 0
    plain = capsys.readouterr().out
    assert "Link clicks by group" not in plain
    assert main(["report", "--input", str(path), "--config", str(EXAMPLE)]) == 0
    out = capsys.readouterr().out
    section = out.split("Link clicks by group")[1].split("\n\n")[0]
    section = "\n".join(x for x in section.splitlines() if "digest" not in x)
    # the largest printed row joins the remainder line; the hidden 30 and the
    # 2 never show, and nothing prints "Under 5"
    assert "start-here" in section and "21" in section and "30" not in section
    assert "events" not in section and "Under 5" not in section


def test_cli_bad_config_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": {"click_events": []}}), encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nnot: a config\n", encoding="utf-8")
    assert main(["report", "--input", str(path), "--config", str(bad)]) == 2
    assert "config" in capsys.readouterr().err.lower()


DANGEROUS = ["=1+1", "+1", "-1", "@SUM(A1)", "\t=1", "\r=1"]


@pytest.mark.parametrize("name", DANGEROUS)
def test_csv_neutralizes_a_formula_leading_group_name_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """Tab ids are slugs and cannot start this way, but the CSV writer must not
    rely on that: a name that reaches groups.csv is neutralized by the report
    command's own encoder."""
    from reach_around.commands import report as report_cmd

    monkeypatch.setattr(
        report_cmd, "group_map", lambda _config: {"big-1": name, "big-2": name}
    )
    export = tmp_path / "export.json"
    export.write_text(
        json.dumps({"tables": {"click_events": clicks({"big-1": 30, "big-2": 12})}}),
        encoding="utf-8",
    )
    out = tmp_path / "csv"
    code = main(
        ["report", "--input", str(export), "--config", str(EXAMPLE), "--csv", str(out)]
    )
    capsys.readouterr()
    assert code == 0
    with (out / "groups.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    cells = [row[0] for row in rows[1:]]
    assert cells == ["'" + name]
    assert not any(cell.startswith(("=", "+", "-", "@", "\t", "\r")) for cell in cells)
