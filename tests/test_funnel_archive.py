# SPDX-License-Identifier: Apache-2.0
"""The 25 month analytics archive (#399): floored, append-only, UTC months."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel import archive
from reach_around.funnel.export import Export
from reach_around.funnel.report import FLOOR, REMAINDER

TODAY = date(2026, 11, 10)


def src(month: str, link: str, label: str, clicks: int) -> dict[str, Any]:
    return {
        "site": "links",
        "month": month,
        "link_id": link,
        "src": label,
        "clicks": clicks,
    }


def cty(month: str, link: str, country: str, clicks: int) -> dict[str, Any]:
    return {
        "site": "links",
        "month": month,
        "link_id": link,
        "country": country,
        "clicks": clicks,
    }


def log(month: str, exhausted: int = 0) -> dict[str, Any]:
    return {
        "site": "links",
        "month": month,
        "rolled_at": 1_793_491_200_000,
        "exhausted_days": exhausted,
        "folded_clicks": 0,
    }


def export(months: tuple[str, ...] = ("2026-10",)) -> Export:
    ex = Export()
    for m in months:
        ex.click_src_monthly += [
            src(m, "kit", "direct", 40),
            src(m, "kit", "newsletter", 20),
            src(m, "kit", "tiny", 2),  # under the floor
            src(m, "rare", "direct", 1),  # a link made only of small cells
        ]
        ex.click_country_monthly += [
            cty(m, "kit", "US", 50),
            cty(m, "kit", "DE", 10),
            cty(m, "kit", "FR", 2),
            cty(m, "rare", "FR", 1),
        ]
        ex.click_month_log.append(log(m))
    ex.present = {"click_src_monthly", "click_country_monthly", "click_month_log"}
    return ex


def build(ex: Export, **kw: Any) -> list[dict[str, Any]]:
    return archive.build_records(ex, site="links", floor=FLOOR, today=TODAY, **kw)


def cells(rec: dict[str, Any], kind: str) -> dict[tuple[str, str], int]:
    return {(r["link_id"], r["label"]): r["clicks"] for r in rec[kind]}


def test_small_cells_are_folded_before_archiving() -> None:
    (rec,) = build(export())
    got = cells(rec, "sources")
    assert ("kit", "tiny") not in got
    assert ("rare", "direct") not in got  # a link of only small cells prints nothing
    assert got[("kit", REMAINDER)] >= FLOOR
    assert all(n >= FLOOR for n in got.values())
    assert all(n >= FLOOR for n in cells(rec, "countries").values())


def test_record_is_monthly_only_no_day_or_raw_fields() -> None:
    (rec,) = build(export())
    text = json.dumps(rec)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)
    assert set(rec) == {
        "version",
        "site",
        "month",
        "floor",
        "capacity_ok",
        "sources",
        "countries",
    }
    assert rec["month"] == "2026-10"


def test_only_closed_months_with_a_rollup_marker_inside_25_months() -> None:
    ex = export(("2024-09", "2024-10", "2026-10", "2026-11"))
    ex.click_month_log = [r for r in ex.click_month_log if r["month"] != "2026-11"]
    ex.click_src_monthly.append(src("2026-08", "kit", "direct", 30))  # no marker
    months = [r["month"] for r in build(ex)]
    # 25 closed months back from 2026-10 reaches 2024-10; 2024-09 is dropped,
    # 2026-11 is the open month, 2026-08 was never marked rolled up.
    assert months == ["2024-10", "2026-10"]


def test_exhausted_capacity_is_a_coarse_flag_not_a_number() -> None:
    ex = export()
    ex.click_month_log = [log("2026-10", exhausted=3)]
    (rec,) = build(ex)
    assert rec["capacity_ok"] is False
    assert "3" not in json.dumps(
        {k: v for k, v in rec.items() if k not in ("sources", "countries", "floor")}
    )


def test_floor_below_minimum_refused() -> None:
    with pytest.raises(ValueError):
        archive.build_records(export(), site="links", floor=FLOOR - 1, today=TODAY)


@pytest.mark.parametrize(
    "missing",
    ["click_src_monthly", "click_country_monthly", "click_month_log"],
)
def test_cli_refuses_an_incomplete_monthly_export_before_retention(
    tmp_path: Path, missing: str
) -> None:
    tables = {
        "click_src_monthly": [src("2026-10", "kit", "direct", 40)],
        "click_country_monthly": [cty("2026-10", "kit", "US", 40)],
        "click_month_log": [log("2026-10")],
    }
    del tables[missing]
    export_file = tmp_path / "export.json"
    export_file.write_text(json.dumps({"version": 1, "tables": tables}))
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    old = archive_dir / "links-2020-01.json"
    old.write_text("keep")

    assert (
        main(
            [
                "analytics-archive",
                "--input",
                str(export_file),
                "--archive",
                str(archive_dir),
            ]
        )
        == 2
    )
    assert old.read_text() == "keep"


def test_other_site_rows_are_ignored() -> None:
    ex = export()
    ex.click_src_monthly.append(
        {**src("2026-10", "kit", "direct", 999), "site": "blog"}
    )
    (rec,) = build(ex)
    assert 999 not in cells(rec, "sources").values()


def test_write_is_append_only_and_repeat_is_a_noop(tmp_path: Path) -> None:
    recs = build(export())
    assert archive.write_records(tmp_path, recs, today=TODAY).written == ["2026-10"]
    path = tmp_path / "links-2026-10.json"
    before = path.read_bytes()
    again = archive.write_records(tmp_path, recs, today=TODAY)
    assert again.written == [] and again.unchanged == ["2026-10"]
    assert path.read_bytes() == before


def test_changed_data_for_an_archived_month_is_refused(tmp_path: Path) -> None:
    archive.write_records(tmp_path, build(export()), today=TODAY)
    ex = export()
    ex.click_src_monthly.append(src("2026-10", "kit", "extra", 77))
    path = tmp_path / "links-2026-10.json"
    before = path.read_bytes()
    with pytest.raises(archive.ArchiveError, match="already archived"):
        archive.write_records(tmp_path, build(ex), today=TODAY)
    assert path.read_bytes() == before


def test_old_months_are_dropped_and_nothing_else_is_touched(tmp_path: Path) -> None:
    archive.write_records(tmp_path, build(export(("2024-10", "2026-10"))), today=TODAY)
    (tmp_path / "notes.txt").write_text("keep")
    (tmp_path / "other-2020-01.json").write_text("{}")  # another site's name pattern
    res = archive.write_records(tmp_path, [], today=date(2026, 12, 1))
    assert res.dropped == ["2024-10", "2020-01"] or sorted(res.dropped) == [
        "2020-01",
        "2024-10",
    ]
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["links-2026-10.json", "notes.txt"]


def test_retention_refuses_a_matching_stale_symlink(tmp_path: Path) -> None:
    stale = tmp_path / "links-2020-01.json"
    stale.symlink_to(tmp_path / "missing.json")

    with pytest.raises(archive.ArchiveError, match="regular file"):
        archive.write_records(tmp_path, [], today=TODAY)

    assert stale.is_symlink()


def test_symlinked_archive_directory_is_refused(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    with pytest.raises(archive.ArchiveError):
        archive.write_records(link, build(export()), today=TODAY)


def test_symlinked_month_file_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "elsewhere"
    secret.write_text("x")
    d = tmp_path / "a"
    d.mkdir()
    os.symlink(secret, d / "links-2026-10.json")
    with pytest.raises(archive.ArchiveError):
        archive.write_records(d, build(export()), today=TODAY)
    assert secret.read_text() == "x"


def test_load_validates_and_rejects_tampered_files(tmp_path: Path) -> None:
    archive.write_records(tmp_path, build(export()), today=TODAY)
    assert [
        r["month"] for r in archive.load_archive(tmp_path, site="links", today=TODAY)
    ] == ["2026-10"]
    p = tmp_path / "links-2026-10.json"
    data = json.loads(p.read_text())
    data["sources"].append(
        {"link_id": "kit", "label": "x", "clicks": 1}
    )  # under the floor
    p.write_text(json.dumps(data))
    with pytest.raises(archive.ArchiveError):
        archive.load_archive(tmp_path, site="links", today=TODAY)


def test_load_rejects_month_that_disagrees_with_filename(tmp_path: Path) -> None:
    archive.write_records(tmp_path, build(export()), today=TODAY)
    (tmp_path / "links-2026-10.json").rename(tmp_path / "links-2026-09.json")
    with pytest.raises(archive.ArchiveError):
        archive.load_archive(tmp_path, site="links", today=TODAY)


def test_load_rejects_duplicate_cells(tmp_path: Path) -> None:
    archive.write_records(tmp_path, build(export()), today=TODAY)
    path = tmp_path / "links-2026-10.json"
    data = json.loads(path.read_text())
    data["sources"].append(data["sources"][0].copy())
    path.write_text(json.dumps(data))

    with pytest.raises(archive.ArchiveError, match="repeats a cell"):
        archive.load_archive(tmp_path, site="links", today=TODAY)


def test_cli_archives_and_grafana_gains_the_monthly_series(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ex_json = {
        "version": 1,
        "tables": {
            "click_src_monthly": [
                src("2026-10", "kit", "direct", 40),
                src("2026-10", "kit", "tiny", 2),
            ],
            "click_country_monthly": [
                cty("2026-10", "kit", "US", 40),
                cty("2026-10", "kit", "FR", 2),
            ],
            "click_month_log": [log("2026-10")],
        },
    }
    f = tmp_path / "export.json"
    f.write_text(json.dumps(ex_json))
    arch = tmp_path / "archive"
    arch.mkdir()
    assert main(["analytics-archive", "--input", str(f), "--archive", str(arch)]) == 0
    assert (arch / "links-2026-10.json").exists()
    store = tmp_path / "g.sqlite3"
    assert (
        main(
            [
                "grafana-export",
                "--input",
                str(f),
                "--output",
                str(store),
                "--archive",
                str(arch),
            ]
        )
        == 0
    )
    con = sqlite3.connect(store)
    rows = con.execute(
        "SELECT month, link_id, clicks FROM grafana_clicks_monthly"
    ).fetchall()
    assert rows == [("2026-10", "kit", 42)]
    assert con.execute(
        "SELECT value FROM grafana_meta WHERE key='schema_version'"
    ).fetchone() == ("4",)
    for t in ("grafana_sources_monthly", "grafana_countries_monthly"):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] >= 1  # noqa: S608 - fixed names
    con.close()


def test_cli_refuses_a_second_different_export(tmp_path: Path) -> None:
    def make(n: int) -> Path:
        f = tmp_path / f"e{n}.json"
        f.write_text(
            json.dumps(
                {
                    "version": 1,
                    "tables": {
                        "click_src_monthly": [src("2026-10", "kit", "direct", n)],
                        "click_country_monthly": [cty("2026-10", "kit", "US", n)],
                        "click_month_log": [log("2026-10")],
                    },
                }
            )
        )
        return f

    arch = tmp_path / "archive"
    arch.mkdir()
    assert (
        main(["analytics-archive", "--input", str(make(40)), "--archive", str(arch)])
        == 0
    )
    assert (
        main(["analytics-archive", "--input", str(make(60)), "--archive", str(arch)])
        == 2
    )
