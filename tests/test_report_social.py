# SPDX-License-Identifier: Apache-2.0
"""The unified owner report (#398): social numbers, change windows, the imported
(Beacons) block, gaps and the display policy. Synthetic data only."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.counts.history import Record, append_records, read_history
from reach_around.funnel.social import build_social, social_csv_rows, social_lines
from test_report_devices_csv import GOOD, export, run

TODAY = date(2026, 11, 10)


def rec(
    platform: str,
    day: date,
    value: int,
    *,
    metric: str = "followers",
    source: str = "api",
    era: str | None = None,
    api_provider: str | None = None,
) -> Record:
    return Record(
        platform=platform,
        metric=metric,
        value=value,
        as_of_utc=f"{day.isoformat()}T09:00:00Z",
        source=source,
        run_id="refresh-test",
        era=era,
        api_provider=api_provider,
    )


def social(records: list[Record], **kw: Any) -> dict[str, Any]:
    from reach_around.counts.history import History

    args: dict[str, Any] = dict(cutover=None, today=TODAY, zone=None)
    args.update(kw)
    return build_social(History(tuple(records)), **args)


def only(block: dict[str, Any], platform: str) -> dict[str, Any]:
    rows = [s for s in block["platforms"] if s["platform"] == platform]
    assert len(rows) == 1
    row: dict[str, Any] = rows[0]
    return row


def change(series: dict[str, Any], days: int) -> dict[str, Any]:
    rows = [c for c in series["changes"] if c["days"] == days]
    assert len(rows) == 1
    row: dict[str, Any] = rows[0]
    return row


def test_latest_and_30_90_365_day_changes() -> None:
    recs = [
        rec("bluesky", TODAY - timedelta(days=n), v)
        for n, v in ((0, 1500), (30, 1400), (90, 1000), (365, 500))
    ]
    row = only(social(recs), "bluesky")
    assert row["latest"]["value"] == 1500
    assert row["latest"]["as_of_utc"] == "2026-11-10T09:00:00Z"
    assert [c["days"] for c in row["changes"]] == [30, 90, 365]
    assert (change(row, 30)["change"], change(row, 30)["percent"]) == (100, 7.1)
    assert (change(row, 90)["change"], change(row, 90)["percent"]) == (500, 50.0)
    assert (change(row, 365)["change"], change(row, 365)["percent"]) == (1000, 200.0)
    assert all(c["status"] == "ok" for c in row["changes"])


def test_a_missing_baseline_day_is_no_record_never_zero() -> None:
    recs = [rec("bluesky", TODAY, 1500), rec("bluesky", TODAY - timedelta(days=31), 1)]
    c = change(only(social(recs), "bluesky"), 30)
    assert c["status"] == "no_record"
    assert c["change"] is None and c["percent"] is None
    assert c["baseline_day"] == "2026-10-11"


def test_gaps_are_listed_days_not_zeros() -> None:
    recs = [
        rec("kick", TODAY, 10),
        rec("kick", TODAY - timedelta(days=1), 9),
        rec("kick", TODAY - timedelta(days=5), 5),
    ]
    row = only(social(recs), "kick")
    assert row["gaps"]["count"] == 3
    assert row["gaps"]["days"] == ["2026-11-06", "2026-11-07", "2026-11-08"]


def test_imported_history_is_its_own_block_never_merged() -> None:
    recs = [
        rec("bluesky", TODAY, 1500),
        rec("bluesky", TODAY - timedelta(days=30), 1400),
        rec("bluesky", TODAY - timedelta(days=30), 9999, source="imported"),
        rec("instagram", TODAY - timedelta(days=400), 700, source="imported"),
    ]
    block = social(recs)
    assert [s["platform"] for s in block["platforms"]] == ["bluesky"]
    assert change(only(block, "bluesky"), 30)["change"] == 100  # 9999 not used
    imported = block["imported"]
    assert "Beacons" in imported["label"]
    assert sorted(s["platform"] for s in imported["series"]) == [
        "bluesky",
        "instagram",
    ]
    assert all(s["latest"]["source"] == "imported" for s in imported["series"])


def test_change_across_a_definition_break_is_withheld() -> None:
    recs = [
        rec("kick", TODAY, 300, metric="subscribers", era="includes-gifted"),
        rec("kick", TODAY - timedelta(days=30), 100, metric="subscribers"),
    ]
    c = change(only(social(recs), "kick"), 30)
    assert c["status"] == "definition_changed"
    assert c["change"] is None


def test_window_across_the_cutover_is_labeled() -> None:
    recs = [rec("bluesky", TODAY, 20), rec("bluesky", TODAY - timedelta(days=30), 10)]
    block = social(recs, cutover=date(2026, 10, 20))
    c = change(only(block, "bluesky"), 30)
    assert c["era"] == "spans"
    assert "cutover" in c["era_text"]
    assert only(block, "bluesky")["latest"]["era"] == "after"
    assert block["cutover_utc"] == "2026-10-20"
    no_cut = social(recs)
    assert "era" not in change(only(no_cut, "bluesky"), 30)


@pytest.mark.parametrize(
    ("records", "status_text"),
    [
        ([rec("bluesky", TODAY, 20)], "no record"),
        (
            [
                rec("bluesky", TODAY, 20, era="current"),
                rec("bluesky", TODAY - timedelta(days=30), 10, era="old"),
            ],
            "withheld",
        ),
    ],
)
def test_non_ok_change_across_cutover_keeps_cutover_label(
    records: list[Record], status_text: str
) -> None:
    block = social(records, cutover=date(2026, 10, 20))

    rendered = "\n".join(social_lines(block))

    assert status_text in rendered
    assert f"{status_text}" in rendered and "[spans the cutover" in rendered


def test_future_cutover_adds_no_label() -> None:
    recs = [rec("bluesky", TODAY, 20), rec("bluesky", TODAY - timedelta(days=30), 10)]
    c = change(only(social(recs, cutover=date(2026, 12, 1)), "bluesky"), 30)
    assert "era" not in c


def test_collected_youtube_analytics_is_refused_but_subscribers_pass() -> None:
    recs = [
        rec(
            "youtube",
            TODAY,
            5000,
            metric="subscribers",
            api_provider="youtube_data_api",
        ),
        rec("youtube", TODAY, 77, metric="views_28d"),
        rec("youtube", TODAY, 88, metric="views_28d", source="hand_kept"),
    ]
    block = social(recs)
    metrics = sorted(s["metric"] for s in block["platforms"])
    assert metrics == ["subscribers", "views_28d"]
    kept = [s for s in block["platforms"] if s["metric"] == "views_28d"][0]
    assert kept["latest"]["source"] == "hand_kept"
    assert block["refused"] == [
        {
            "platform": "youtube",
            "metric": "views_28d",
            "reason": "collected YouTube Analytics figures are refused "
            "(display policy); type the figure by hand to include it",
        }
    ]


def test_unprovenanced_youtube_api_values_are_excluded_from_every_output() -> None:
    records = [
        rec("youtube-analytics", TODAY - timedelta(days=30), 10, metric="views_28d"),
        rec("youtube-analytics", TODAY, 87654321, metric="views_28d"),
        rec("youtube", TODAY, 5000, metric="subscribers"),
        rec(
            "youtube",
            TODAY - timedelta(days=1),
            88,
            metric="views_28d",
            source="hand_kept",
        ),
        rec("youtube", TODAY, 999, metric="views_28d"),
    ]

    block = social(records)

    assert [(s["platform"], s["metric"]) for s in block["platforms"]] == [
        ("youtube", "views_28d")
    ]
    kept = block["platforms"][0]
    assert kept["latest"]["value"] == 88
    assert kept["latest"]["source"] == "hand_kept"
    assert all(item["baseline_value"] is None for item in kept["changes"])
    assert kept["gaps"]["to"] == TODAY.isoformat()
    assert TODAY.isoformat() in kept["gaps"]["days"]
    assert {item["metric"] for item in block["refused"]} == {
        "subscribers",
        "views_28d",
    }
    rendered = "\n".join(social_lines(block))
    csv_text = "\n".join(",".join(map(str, row)) for row in social_csv_rows(block))
    serialized = json.dumps(block)
    for output in (rendered, csv_text, serialized):
        assert "5000" not in output
        assert "999" not in output
        assert "87654321" not in output


def test_only_provenanced_youtube_data_api_subscribers_are_accepted() -> None:
    records = [
        rec(
            "youtube",
            TODAY - timedelta(days=1),
            123,
            metric="subscribers",
            api_provider="youtube_data_api",
        ),
        rec(
            "youtube",
            TODAY,
            999,
            metric="subscribers",
            api_provider="youtube_analytics",
        ),
    ]

    block = social(records)

    row = only(block, "youtube")
    assert row["latest"]["value"] == 123
    assert block["refused"][0]["metric"] == "subscribers"


def test_display_zone_labels_instants() -> None:
    row = only(social([rec("kick", TODAY, 5)], zone="America/New_York"), "kick")
    assert row["latest"]["as_of_display"] == "2026-11-10 04:00 EST"
    assert row["latest"]["as_of_utc"] == "2026-11-10T09:00:00Z"
    plain = only(social([rec("kick", TODAY, 5)]), "kick")
    assert "as_of_display" not in plain["latest"]


def test_display_zone_text_keeps_utc_instant_beside_local_time() -> None:
    block = social([rec("kick", TODAY, 5)], zone="America/New_York")

    rendered = "\n".join(social_lines(block))

    assert "2026-11-10 04:00 EST (2026-11-10T09:00:00Z)" in rendered


def test_intervening_definition_break_withholds_change() -> None:
    records = [
        rec("kick", TODAY - timedelta(days=30), 100, era="A"),
        rec("kick", TODAY - timedelta(days=21), 150, era="B"),
        rec("kick", TODAY, 200, era="A"),
    ]

    result = change(only(social(records), "kick"), 30)

    assert result["status"] == "definition_changed"
    assert result["change"] is None


def test_all_gap_days_are_listed_in_text_and_csv() -> None:
    block = social(
        [
            rec("kick", TODAY - timedelta(days=20), 5),
            rec("kick", TODAY, 10),
        ]
    )
    expected = [(TODAY - timedelta(days=days)).isoformat() for days in range(19, 0, -1)]

    rendered = "\n".join(social_lines(block))
    rows = social_csv_rows(block)

    assert all(day in rendered for day in expected)
    assert "..." not in rendered
    assert rows[0][-1] == ";".join(expected)


def test_refused_youtube_metric_is_named_in_csv() -> None:
    block = social([rec("youtube", TODAY, 77, metric="views_28d")])

    rows = social_csv_rows(block)

    assert len(rows) == 1
    assert rows[0][0:3] == ["refused", "youtube", "views_28d"]
    assert rows[0][11] == "refused"


def test_empty_history_still_yields_a_labeled_block() -> None:
    block = social([])
    assert block["platforms"] == [] and block["imported"]["series"] == []
    assert block["refused"] == []


# ---- command line ----------------------------------------------------------


def make_history(tmp_path: Path) -> Path:
    today = (datetime.now(UTC) - timedelta(hours=9)).date()  # never future
    path = tmp_path / "social.jsonl"
    records = [
        rec("bluesky", today, 1500),
        rec("bluesky", today - timedelta(days=30), 1400),
        rec("youtube", today, 12, metric="views_28d"),
        rec("instagram", today - timedelta(days=45), 700, source="imported"),
    ]
    append_records(path, records)
    return path


def test_cli_text_has_social_block_and_labels(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = make_history(tmp_path)
    code, out, err = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--history", str(hist)
    )
    assert code == 0, err
    assert "Social numbers" in out
    assert "bluesky" in out and "+100" in out
    assert "Imported history (Beacons)" in out
    assert "instagram" in out
    assert "gap" in out.lower()
    assert "refused youtube views_28d" in out


def test_cli_json_carries_social_and_default_run_does_not(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inp = str(export(tmp_path, GOOD))
    _, plain, _ = run(capsys, "--input", inp, "--json")
    assert "social" not in json.loads(plain)
    hist = make_history(tmp_path)
    code, out, _ = run(capsys, "--input", inp, "--json", "--history", str(hist))
    assert code == 0
    data = json.loads(out)
    assert [s["platform"] for s in data["social"]["platforms"]] == ["bluesky"]
    assert [r["metric"] for r in data["social"]["refused"]] == ["views_28d"]


def test_cli_csv_writes_social_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = make_history(tmp_path)
    out_dir = tmp_path / "csv"
    code, _, err = run(
        capsys,
        "--input",
        str(export(tmp_path, GOOD)),
        "--history",
        str(hist),
        "--csv",
        str(out_dir),
    )
    assert code == 0, err
    rows = list(csv.DictReader(io.StringIO((out_dir / "social.csv").read_text())))
    blocks = {r["block"] for r in rows}
    assert blocks == {"collected", "imported", "refused"}
    mine = [r for r in rows if r["platform"] == "bluesky" and r["window_days"] == "30"]
    assert mine[0]["change"] == "100" and mine[0]["status"] == "ok"
    gap = [r for r in rows if r["platform"] == "bluesky" and r["window_days"] == "90"]
    assert gap[0]["change"] == "" and gap[0]["status"] == "no_record"


def test_cli_without_history_writes_no_social_csv(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "csv"
    run(capsys, "--input", str(export(tmp_path, GOOD)), "--csv", str(out_dir))
    assert not (out_dir / "social.csv").exists()


def test_cli_damaged_history_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = make_history(tmp_path)
    hist.write_bytes(hist.read_bytes()[:-5])
    code, _, err = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--history", str(hist)
    )
    assert code == 2 and err.startswith("error:")


def test_cli_missing_history_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "does-not-exist.jsonl"

    code, _, err = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--history", str(missing)
    )

    assert code == 2
    assert err.startswith("error:")


def test_cli_future_dated_history_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "future.jsonl"
    future = datetime.now(UTC) + timedelta(days=1)
    record = rec("kick", future.date(), 10)
    append_records(path, [record], now=future + timedelta(days=1))

    code, _, err = run(
        capsys, "--input", str(export(tmp_path, GOOD)), "--history", str(path)
    )

    assert code == 2
    assert "future" in err


def test_cli_history_file_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hist = make_history(tmp_path)
    before = hist.read_bytes()
    run(capsys, "--input", str(export(tmp_path, GOOD)), "--history", str(hist))
    assert hist.read_bytes() == before
    assert read_history(hist).records
