# SPDX-License-Identifier: Apache-2.0
"""``reach-around report``: devices, browsers, systems and ``--csv``."""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel.csvout import csv_files, render_csv, safe_cell
from reach_around.funnel.export import load_exports
from reach_around.funnel.report import DEVICE_SECTIONS, build_report

DAY = "2026-11-01"


def dim(kind: str, value: str, views: int, bot_class: str = "h") -> dict[str, Any]:
    return {
        "site": "links",
        "day": DAY,
        "day_tz": "UTC",
        "dim": kind,
        "value": value,
        "bot_class": bot_class,
        "views": views,
        "uniques": 0,
    }


def view(views: int, bot_class: str = "h", page: str = "home") -> dict[str, Any]:
    return {
        "site": "links",
        "day": DAY,
        "day_tz": "UTC",
        "page": page,
        "src_kind": "d",
        "src": "",
        "country": "US",
        "bot_class": bot_class,
        "views": views,
        "uniques": 1,
    }


def export(tmp_path: Path, dims: list[dict[str, Any]], views: int = 200) -> Path:
    data = {"tables": {"view_daily": [view(views)], "view_dim_daily": dims}}
    path = tmp_path / "export.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["report", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


GOOD = [
    dim("device", "mobile", 120),
    dim("device", "desktop", 70),
    dim("device", "tablet", 3),
    dim("browser", "chrome", 100),
    dim("browser", "safari", 60),
    dim("browser", "firefox", 40),
    dim("os", "windows", 90),
    dim("os", "ios", 80),
    dim("os", "linux", 30),
    dim("device", "mobile", 50, "a"),
    dim("browser", "curl", 50, "a"),
]


def _report() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        path = export(Path(tmp), GOOD)
        return build_report(load_exports([path]))


def test_sections_appear_in_text_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export(tmp_path, GOOD)
    code, out, err = run(capsys, "--input", str(path), "--json")
    assert code == 0, err
    report = json.loads(out)
    assert {"devices", "browsers", "systems"} <= report.keys()
    devices = {r["label"]: r["views"] for r in report["devices"]["rows"]}
    assert devices.get("desktop") is not None or devices.get("mobile") is not None
    assert "tablet" not in devices
    assert all(r["views"] >= 5 for k in DEVICE_SECTIONS for r in report[k]["rows"])
    assert "curl" not in {r["label"] for r in report["browsers"]["rows"]}
    _, text, _ = run(capsys, "--input", str(path))
    for title in ("Device classes", "Browsers", "Operating systems"):
        assert title in text


def test_include_bots_counts_bot_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export(tmp_path, GOOD)
    _, out, _ = run(capsys, "--input", str(path), "--json", "--include-bots")
    labels = {r["label"] for r in json.loads(out)["browsers"]["rows"]}
    assert "curl" in labels


def test_small_cells_suppressed_and_merged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dims = [
        dim("device", "mobile", 100),
        dim("device", "desktop", 60),
        dim("device", "tablet", 2),
        dim("device", "tv", 1),
    ]
    _, out, _ = run(capsys, "--input", str(export(tmp_path, dims)), "--json")
    sec = json.loads(out)["devices"]
    labels = {r["label"] for r in sec["rows"]}
    assert "tablet" not in labels and "tv" not in labels
    # the largest shown row joins the remainder, as in every other table
    assert sec["other_below_floor"] >= 5
    assert sec["other_below_floor"] == 100 + 2 + 1


def test_unsafe_small_cuts_withhold_the_view_population(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dims = [dim("browser", "safari", 2)]
    path = export(tmp_path, dims, views=6)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tables"]["view_hour_daily"] = [
        {
            "site": "links",
            "day": DAY,
            "hour": 9,
            "bot_class": "h",
            "views": 6,
            "clicks": 5,
        }
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    code, out, err = run(capsys, "--input", str(path), "--json")
    assert code == 0, err
    report = json.loads(out)
    assert report["browsers"]["state"] == "withheld"
    assert report["funnel"]["page_views"] is None
    assert report["pages"]["state"] == "withheld"
    assert report["devices"]["rows"] == []
    assert report["patterns"]["views"]["state"] == "withheld"
    assert report["patterns"]["views"]["rows"] == []
    pattern_csv = csv_files(report, generated_at=datetime(2026, 11, 8, tzinfo=UTC))[
        "owner_time_patterns.csv"
    ].decode("utf-8")
    assert ["views", "", "", "withheld"] in list(
        csv.reader(io.StringIO(pattern_csv, newline=""))
    )


def test_missing_dim_table_says_not_in_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"tables": {"view_daily": [view(50)]}}))
    _, out, _ = run(capsys, "--input", str(path), "--json")
    assert json.loads(out)["devices"]["state"] == "not_in_export"
    _, text, _ = run(capsys, "--input", str(path))
    assert "not in this export" in text


# ---- CSV ------------------------------------------------------------------


def read(path: Path) -> list[list[str]]:
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))


def test_csv_files_header_and_order(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export(tmp_path, GOOD)
    out_dir = tmp_path / "csv"
    code, out, err = run(capsys, "--input", str(path), "--csv", str(out_dir))
    assert code == 0, err
    assert "UTC" in out
    names = sorted(p.name for p in out_dir.iterdir())
    assert names == [
        "bot_classes.csv",
        "bot_reasons.csv",
        "browsers.csv",
        "click_countries.csv",
        "click_flows.csv",
        "click_matrix.csv",
        "devices.csv",
        "funnel.csv",
        "geography.csv",
        "links.csv",
        "metadata.csv",
        "owner_clicks_by_country.csv",
        "owner_clicks_by_source.csv",
        "owner_countries.csv",
        "owner_daily.csv",
        "owner_devices.csv",
        "owner_links.csv",
        "owner_social_icons.csv",
        "owner_sources.csv",
        "owner_tiles.csv",
        "owner_time_patterns.csv",
        "pages.csv",
        "sources.csv",
        "systems.csv",
    ]
    assert read(out_dir / "devices.csv")[0] == ["label", "views", "row_type"]
    assert read(out_dir / "funnel.csv")[0] == ["step", "value", "status", "coverage"]
    assert (out_dir / "devices.csv").read_bytes().endswith(b"\r\n")
    assert (out_dir / "devices.csv").stat().st_mode & 0o777 == 0o600


def test_csv_is_deterministic_for_one_stamp() -> None:
    report = _report()
    when = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
    first = csv_files(report, generated_at=when)
    assert first == csv_files(report, generated_at=when)
    assert list(first) == list(csv_files(report, generated_at=when))


def test_owner_time_patterns_csv_preserves_each_unavailable_metric_state(
    tmp_path: Path,
) -> None:
    report = build_report(load_exports([export(tmp_path, GOOD)]))
    rows = list(
        csv.reader(
            io.StringIO(
                csv_files(report, generated_at=datetime(2026, 11, 8, tzinfo=UTC))[
                    "owner_time_patterns.csv"
                ].decode("utf-8"),
                newline="",
            )
        )
    )
    assert rows[1:] == [
        ["views", "", "", "not_in_export"],
        ["clicks", "", "", "not_in_export"],
    ]


def test_metadata_has_generated_at_and_window(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "csv"
    run(
        capsys,
        "--input",
        str(export(tmp_path, GOOD)),
        "--csv",
        str(out_dir),
        "--since",
        DAY,
    )
    meta = dict(read(out_dir / "metadata.csv")[1:])
    assert meta["generated_at_utc"].endswith("Z") and "T" in meta["generated_at_utc"]
    assert meta["window_since_utc"] == DAY and meta["window_until_utc"] == DAY
    assert meta["requested_since_utc"] == DAY
    assert meta["timezone"] == "UTC"
    assert meta["privacy_floor_views"] == "5"


def test_csv_keeps_suppression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dims = [
        dim("device", "mobile", 100),
        dim("device", "desktop", 60),
        dim("device", "tablet", 2),
    ]
    out_dir = tmp_path / "csv"
    run(capsys, "--input", str(export(tmp_path, dims)), "--csv", str(out_dir))
    rows = read(out_dir / "devices.csv")
    assert all(r[0] != "tablet" for r in rows)
    below = [r for r in rows if r[2] == "below_floor"]
    assert below and int(below[0][1]) >= 5
    for r in rows[1:]:
        if r[2] == "row":
            assert int(r[1]) >= 5


def test_csv_and_json_conflict_and_symlinked_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = export(tmp_path, GOOD)
    args = ("--input", str(path), "--json", "--csv", str(tmp_path / "d"))
    code, _, err = run(capsys, *args)
    assert code == 2 and "cannot be combined" in err
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    os.symlink(target, link)
    code, _, err = run(capsys, "--input", str(path), "--csv", str(link))
    assert code == 2
    assert list(target.iterdir()) == []


def test_csv_does_not_follow_a_symlinked_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "csv"
    out_dir.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    os.symlink(victim, out_dir / "devices.csv")
    path = export(tmp_path, GOOD)
    code, _, _ = run(capsys, "--input", str(path), "--csv", str(out_dir))
    assert code == 2
    assert victim.read_text() == "keep"


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_formula_injection_prefix(lead: str) -> None:
    cell = lead + "HYPERLINK(1)"
    assert safe_cell(cell) == "'" + cell
    data = render_csv(["a"], [[cell]]).decode("utf-8")
    parsed = list(csv.reader(io.StringIO(data, newline="")))
    assert parsed[1][0].startswith("'")


def test_safe_cell_leaves_numbers_and_plain_text() -> None:
    assert safe_cell(5) == 5
    assert safe_cell(0.5) == 0.5
    assert safe_cell("chrome") == "chrome"
    assert safe_cell("a=b") == "a=b"
    assert safe_cell(None) == ""


def test_rfc4180_quoting_round_trips() -> None:
    cells = ["a,b", 'say "hi"', "line\nbreak", "plain", "café"]
    data = render_csv(["x"], [[c] for c in cells])
    assert b'"a,b"' in data and b'"say ""hi"""' in data
    text = data.decode("utf-8")
    parsed = [r[0] for r in csv.reader(io.StringIO(text, newline=""))]
    assert parsed[0] == "x"
    # a leading line feed would be prefixed; these cells start with letters
    assert parsed[1:] == cells
    assert data.startswith(b"x\r\n")


def test_no_per_visitor_columns() -> None:
    files = csv_files(_report(), generated_at=datetime(2026, 11, 8, tzinfo=UTC))
    banned = {"ip", "ua", "user_agent", "visitor", "uniques", "ts", "lead_id"}
    for name, data in files.items():
        header = set(next(csv.reader(io.StringIO(data.decode("utf-8")))))
        assert not header & banned, name


def test_docs_describe_devices_and_csv() -> None:
    doc = (Path(__file__).resolve().parent.parent / "docs" / "report.md").read_text(
        encoding="utf-8"
    )
    for needle in ("--csv", "metadata.csv", "generated_at_utc", "formula", "Devices"):
        assert needle in doc
    assert "does not show browsers" not in doc


def test_existing_file_is_tightened_to_0600(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "csv"
    out_dir.mkdir()
    old = out_dir / "devices.csv"
    old.write_text("old")
    old.chmod(0o644)
    path = export(tmp_path, GOOD)
    code, _, _ = run(capsys, "--input", str(path), "--csv", str(out_dir))
    assert code == 0
    assert old.stat().st_mode & 0o777 == 0o600


def test_symlinked_ancestor_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    path = export(tmp_path, GOOD)
    code, _, _ = run(capsys, "--input", str(path), "--csv", str(link / "sub"))
    assert code == 2
    assert list(real.iterdir()) == []


def test_symlinked_final_directory_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "final")
    path = export(tmp_path, GOOD)
    code, _, err = run(capsys, "--input", str(path), "--csv", str(tmp_path / "final"))
    assert code == 2 and str(real) not in err
    assert list(real.iterdir()) == []


def test_swap_between_create_and_open_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    real_mkdir = os.mkdir

    def swapping_mkdir(name: Any, mode: int = 0o777, *, dir_fd: Any = None) -> None:
        real_mkdir(name, mode, dir_fd=dir_fd)
        if name == "swapme":
            os.rmdir(name, dir_fd=dir_fd)
            os.symlink(target, name, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", swapping_mkdir)
    path = export(tmp_path, GOOD)
    code, _, _ = run(capsys, "--input", str(path), "--csv", str(tmp_path / "swapme"))
    assert code == 2
    assert list(target.iterdir()) == []


def test_directory_owned_by_someone_else_is_refused(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ownership cannot be changed without privileges, so the current user id is
    # faked: the directory then looks foreign to the check.
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(tmp_path).st_uid + 1)
    path = export(tmp_path, GOOD)
    out_dir = tmp_path / "csv"
    code, _, err = run(capsys, "--input", str(path), "--csv", str(out_dir))
    assert code == 2
    assert list(out_dir.iterdir()) == []
