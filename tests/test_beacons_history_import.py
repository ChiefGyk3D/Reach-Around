# SPDX-License-Identifier: Apache-2.0
from datetime import date, timedelta
from pathlib import Path

import pytest

import reach_around.commands.counts as counts_cmd
from reach_around.cli import main
from reach_around.counts.beacons import (
    MAX_BEACONS_BYTES,
    MAX_BEACONS_ROWS,
    BeaconsImportError,
    parse_beacons_csv,
)
from reach_around.counts.history import read_history

FIXTURE = Path(__file__).parent / "fixtures" / "beacons_link_in_bio.csv"
HEADER = "Section,Item,Detail,Views,Clicks,CTR (%)\n"
CONFIG = """\
version: 1
creator: {name: Synthetic Creator}
tabs:
  - id: home
    title: Home
    blocks: []
numbers:
  history_file: history/collected.jsonl
"""


@pytest.fixture
def config(tmp_path: Path) -> Path:
    (tmp_path / "history").mkdir()
    path = tmp_path / "site.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def beacons_command(
    config: Path, csv_path: Path, target: Path, *extra: str
) -> list[str]:
    return [
        "counts",
        "history-import-beacons",
        "--config",
        str(config),
        "--csv",
        str(csv_path),
        "--file",
        str(target),
        *extra,
    ]


def test_imports_daily_history_with_source_and_provenance_only(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "beacons-history.jsonl"

    assert main(beacons_command(config, FIXTURE, target)) == 0

    records = read_history(target).records
    assert [
        (row.as_of_utc, row.metric, row.value, row.source, row.note) for row in records
    ] == [
        ("2026-10-01T00:00:00Z", "views", 12, "imported", "Beacons"),
        ("2026-10-01T00:00:00Z", "clicks", 3, "imported", "Beacons"),
        ("2026-10-03T00:00:00Z", "views", 0, "imported", "Beacons"),
        ("2026-10-03T00:00:00Z", "clicks", 1, "imported", "Beacons"),
        ("2026-10-04T00:00:00Z", "views", 25, "imported", "Beacons"),
        ("2026-10-04T00:00:00Z", "clicks", 5, "imported", "Beacons"),
    ]
    stored = target.read_text(encoding="utf-8")
    assert "Synthetic guide" not in stored
    assert "example.com" not in stored
    assert "verified: 9 export rows, 3 daily rows, 6 history records" in (
        capsys.readouterr().out
    )


def test_dry_run_validates_without_writing(
    tmp_path: Path, config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "beacons-history.jsonl"
    assert main(beacons_command(config, FIXTURE, target, "--dry-run")) == 0
    assert not target.exists()
    assert "dry run" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("Mystery,item,,1,1,100.0", "unknown section"),
        ("Daily,2026-02-30,,1,1,", "real date"),
        ("Daily,2026-10-01,,-1,1,", "whole number"),
        ("Daily,2026-10-01,,1,1.5,", "whole number"),
        ("Links,=2+2,https://example.com,,,", "formula"),
        ("Sources,example.com,,1,1,not-a-rate", "CTR"),
    ],
)
def test_rejects_malformed_rows_without_repeating_values(
    row: str, problem: str
) -> None:
    data = (HEADER + "Total,Link in Bio,,1,1,100.0\n" + row + "\n").encode()
    with pytest.raises(BeaconsImportError, match=problem) as caught:
        parse_beacons_csv(data)
    assert row not in str(caught.value)


def test_rejects_duplicate_daily_dates() -> None:
    data = (
        HEADER
        + "Total,Link in Bio,,2,1,50.0\n"
        + "Daily,2026-10-01,,1,0,\n"
        + "Daily,2026-10-01,,1,1,\n"
    ).encode()
    with pytest.raises(BeaconsImportError, match="duplicate day"):
        parse_beacons_csv(data)


def test_rejects_oversized_file_and_row_count() -> None:
    with pytest.raises(BeaconsImportError, match=str(MAX_BEACONS_BYTES)):
        parse_beacons_csv(b"x" * (MAX_BEACONS_BYTES + 1))

    rows = ["Daily,2026-10-01,,0,0,"] * MAX_BEACONS_ROWS
    data = (HEADER + "Total,Link in Bio,,0,0,0.0\n" + "\n".join(rows) + "\n").encode()
    assert len(data) < MAX_BEACONS_BYTES
    with pytest.raises(BeaconsImportError, match=str(MAX_BEACONS_ROWS)):
        parse_beacons_csv(data)


def test_rejects_integer_over_python_digit_limit_as_import_error() -> None:
    oversized_integer = "9" * 5_000
    data = (
        HEADER
        + "Total,Link in Bio,,1,1,100.0\n"
        + f"Daily,2026-10-01,,{oversized_integer},1,\n"
    ).encode()

    with pytest.raises(BeaconsImportError, match="whole number"):
        parse_beacons_csv(data)


def test_refuses_configured_collection_history_even_when_empty(
    config: Path,
) -> None:
    target = config.parent / "history" / "collected.jsonl"
    target.touch()

    assert main(beacons_command(config, FIXTURE, target)) == 2
    assert target.read_bytes() == b""


def test_refuses_to_merge_into_a_nonempty_history_file(
    tmp_path: Path, config: Path
) -> None:
    target = tmp_path / "history.jsonl"
    target.write_text("already here\n", encoding="utf-8")
    before = target.read_bytes()
    assert main(beacons_command(config, FIXTURE, target)) == 2
    assert target.read_bytes() == before


def test_write_failure_leaves_destination_unchanged(
    tmp_path: Path, config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "beacons-history.jsonl"

    def fail_write(path: Path, text: str) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(counts_cmd, "write_atomic", fail_write)

    assert main(beacons_command(config, FIXTURE, target)) == 2
    assert not target.exists()


def test_future_daily_row_is_rejected_before_dry_run_or_write(
    tmp_path: Path, config: Path
) -> None:
    first_day = date.today() - timedelta(days=99)
    rows = [
        f"Daily,{(first_day + timedelta(days=offset)).isoformat()},,1,1,"
        for offset in range(100)
    ]
    rows.append("Daily,9999-12-31,,1,1,")
    csv_path = tmp_path / "future.csv"
    csv_path.write_text(
        HEADER + "Total,Link in Bio,,1,1,100.0\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    target = tmp_path / "beacons-history.jsonl"

    assert main(beacons_command(config, csv_path, target, "--dry-run")) == 2
    assert not target.exists()
    assert main(beacons_command(config, csv_path, target)) == 2
    assert not target.exists()
