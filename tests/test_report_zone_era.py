# SPDX-License-Identifier: Apache-2.0
"""``reach-around report --display-zone`` and ``--cutover`` (#372, #375)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from test_report_devices_csv import GOOD, export, run

DATA = "2026-11-01"


def meta(csv_dir: Path) -> dict[str, str]:
    text = (csv_dir / "metadata.csv").read_text(encoding="utf-8")
    return dict(list(csv.reader(io.StringIO(text)))[1:])


def test_default_json_is_utc_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "--input", str(export(tmp_path, GOOD)), "--json")
    assert code == 0
    data = json.loads(out)
    assert "display" not in data and "era" not in data
    assert data["period"]["since"] == DATA


def test_zone_adds_display_block_and_keeps_utc(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--input", str(export(tmp_path, GOOD)), "--json"]
    code, out, _ = run(capsys, *args, "--display-zone", "America/Los_Angeles")
    assert code == 0
    data = json.loads(out)
    assert data["period"]["since"] == DATA
    assert data["display"]["zone"] == "America/Los_Angeles"
    assert data["display"]["window_start"] == "2026-10-31 17:00 PDT"
    assert data["display"]["window_end_exclusive"] == "2026-11-01 16:00 PST"


def test_text_names_the_zone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--display-zone", "Asia/Tokyo"
    )
    assert code == 0
    assert "Display zone Asia/Tokyo" in out and "JST" in out and "UTC" in out


@pytest.mark.parametrize("bad", ["Mars/Base", "utc", "../x", "America/New_York;"])
def test_invalid_zone_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    code, out, err = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--display-zone", bad
    )
    assert code == 2 and out == ""
    assert bad not in err


def test_csv_metadata_states_zone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = tmp_path / "plain"
    code, _, _ = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--csv", str(plain)
    )
    assert code == 0
    base = meta(plain)
    assert base["timezone"] == "UTC"
    assert "display_timezone" not in base and "cutover_utc" not in base
    zoned = tmp_path / "zoned"
    run(
        capsys,
        "--input",
        str(export(tmp_path, GOOD)),
        "--csv",
        str(zoned),
        "--display-zone",
        "Europe/Berlin",
        "--cutover",
        "2026-11-01",
    )
    got = meta(zoned)
    assert got["timezone"] == "UTC"
    assert got["display_timezone"] == "Europe/Berlin"
    assert got["window_start_display"] == "2026-11-01 01:00 CET"
    assert got["window_era"] == "after"
    assert got["cutover_utc"] == "2026-11-01"
    assert got["generated_at_utc"].endswith("Z")


@pytest.mark.parametrize(
    ("cutover", "word"),
    [
        ("2026-11-02", "before"),
        ("2026-11-01", "after"),
        ("2026-10-01", "after"),
    ],
)
def test_window_era(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cutover: str, word: str
) -> None:
    code, out, _ = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--json", "--cutover", cutover
    )
    assert code == 0
    assert json.loads(out)["era"]["window"] == word


def test_future_or_unset_cutover_adds_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys,
        "--input",
        str(export(tmp_path, GOOD)),
        "--json",
        "--cutover",
        "2999-01-01",
    )
    assert code == 0 and "era" not in json.loads(out)


def test_text_labels_window_before(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--cutover", "2026-11-05"
    )
    assert code == 0
    assert "Cutover: 2026-11-05 UTC; this window is before Reach Around." in out


def test_bad_cutover_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--cutover", "11/05/2026"
    )
    assert code == 2 and "--cutover" in err


def test_no_data_still_names_the_zone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"tables": {"view_daily": []}}), encoding="utf-8")
    code, out, _ = run(capsys, "--input", str(path), "--display-zone", "Asia/Tokyo")
    assert code == 0
    assert "Display zone Asia/Tokyo: no data in this period" in out
