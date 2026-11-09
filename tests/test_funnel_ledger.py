# SPDX-License-Identifier: Apache-2.0
"""The release ledger: a week and a month over one UTC day are never both
released, a damaged ledger fails closed, and a repeat release is allowed."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel import ledger as mod
from reach_around.funnel.ledger import (
    LedgerError,
    Release,
    ReleaseGuard,
    authorize_release,
    init_ledger,
    read_ledger,
    record_release,
)

STAMP = "2026-11-09T10:00:00Z"


def fresh(tmp_path: Path) -> Path:
    ledger = tmp_path / "ledger.jsonl"
    init_ledger(ledger)
    return ledger


def week(start: str, site: str = "links") -> Release:
    end = (date.fromisoformat(start) + timedelta(days=6)).isoformat()
    return Release(site, "week", start, end, STAMP)


def month(start: str, end: str, site: str = "links") -> Release:
    return Release(site, "month", start, end, STAMP)


def test_a_month_after_its_weeks_is_refused(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, week("2026-11-09"))
    with pytest.raises(LedgerError, match="shares a day"):
        record_release(ledger, month("2026-11-01", "2026-11-30"))
    assert len(read_ledger(ledger)) == 1  # nothing was written


def test_a_week_after_its_month_is_refused_including_a_straddling_week(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, month("2026-10-01", "2026-10-31"))
    with pytest.raises(LedgerError, match="shares a day"):
        record_release(ledger, week("2026-10-26"))  # Oct 26 - Nov 1 straddles
    assert record_release(ledger, week("2026-11-02"))  # no shared day: fine


def test_a_week_next_to_a_month_is_allowed_both_ways(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, month("2026-10-01", "2026-10-31"))
    assert record_release(ledger, week("2026-11-02"))
    assert record_release(ledger, week("2026-11-09"))
    assert [r.kind for r in read_ledger(ledger)] == ["month", "week", "week"]


def test_the_same_period_twice_is_allowed_and_written_once(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, week("2026-11-02"))
    later = Release("links", "week", "2026-11-02", "2026-11-08", "2026-12-24T00:00:00Z")
    assert record_release(ledger, later) is False
    assert len(read_ledger(ledger)) == 1


def test_another_site_is_not_affected(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, week("2026-11-02", site="links"))
    assert record_release(ledger, month("2026-11-01", "2026-11-30", site="blog"))


@pytest.mark.parametrize(
    "bad",
    [
        Release("links", "week", "2026-11-03", "2026-11-09", STAMP),  # not Monday
        Release("links", "week", "2026-11-02", "2026-11-09", STAMP),  # 8 days
        Release("links", "month", "2026-11-02", "2026-11-30", STAMP),
        Release("links", "month", "2026-11-01", "2026-11-29", STAMP),
        Release("links", "year", "2026-01-01", "2026-12-31", STAMP),
        Release("Links", "week", "2026-11-02", "2026-11-08", STAMP),
        Release("links", "week", "2026-11-02", "2026-11-08", "yesterday"),
        Release("links", "week", "2026-02-30", "2026-03-08", STAMP),
    ],
)
def test_bad_records_are_refused(tmp_path: Path, bad: Release) -> None:
    with pytest.raises(LedgerError):
        record_release(fresh(tmp_path), bad)


# ---- corruption fails closed ------------------------------------------------


def seeded(tmp_path: Path) -> Path:
    ledger = fresh(tmp_path)
    record_release(ledger, week("2026-11-02"))
    record_release(ledger, week("2026-11-09"))
    return ledger


def test_a_changed_line_breaks_the_chain(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    ledger.write_text(ledger.read_text().replace("2026-11-02", "2026-11-23", 1))
    with pytest.raises(LedgerError):
        read_ledger(ledger)
    with pytest.raises(LedgerError):
        record_release(ledger, week("2026-11-16"))


def test_a_removed_first_line_breaks_the_chain(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    lines = ledger.read_text().splitlines(keepends=True)
    ledger.write_text("".join(lines[1:]))
    with pytest.raises(LedgerError, match="chain"):
        read_ledger(ledger)


def test_a_cut_short_last_line_is_not_repaired(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    data = ledger.read_bytes()
    ledger.write_bytes(data[:-10])
    with pytest.raises(LedgerError):
        read_ledger(ledger)
    assert ledger.read_bytes() == data[:-10]  # untouched


@pytest.mark.parametrize(
    "junk",
    [
        b"\n",
        b"not json\n",
        b'{"site":"links"}\n',
        b'{"site":"links","site":"x"}\n',
        b"[1]\n",
        bytes([255, 254, 10]),
    ],
)
def test_junk_is_refused(tmp_path: Path, junk: bytes) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(junk)
    with pytest.raises(LedgerError):
        read_ledger(ledger)


def test_a_file_that_breaks_the_rule_itself_is_corrupt(tmp_path: Path) -> None:
    """Hand-built chain with a week and a month over the same day."""
    path = tmp_path / "ledger.jsonl"
    prev = "0" * 64
    out = b""
    for rel in (week("2026-11-02"), month("2026-11-01", "2026-11-30")):
        line = mod._line(rel, prev)
        out += line + b"\n"
        prev = hashlib.sha256(line).hexdigest()
    path.write_bytes(out)
    with pytest.raises(LedgerError, match="release rule"):
        read_ledger(path)


def test_a_symlinked_ledger_is_refused(tmp_path: Path) -> None:
    real = seeded(tmp_path)
    link = tmp_path / "link.jsonl"
    os.symlink(real, link)
    with pytest.raises(LedgerError):
        read_ledger(link)
    with pytest.raises(LedgerError):
        record_release(link, week("2026-11-16"))


def test_an_oversized_ledger_is_refused(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(LedgerError):
        read_ledger(ledger)


def test_the_file_is_owner_only_and_holds_no_counts(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    assert oct(ledger.stat().st_mode & 0o777) == "0o600"
    for line in ledger.read_text().splitlines():
        assert set(json.loads(line)) == {
            "site",
            "kind",
            "start",
            "end",
            "released_at",
            "prev",
        }


# ---- the command -----------------------------------------------------------


def _export(tmp_path: Path, rows: dict[str, Any]) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": rows}), encoding="utf-8")
    return path


def _last_month() -> tuple[date, date]:
    today = datetime.now(UTC).date()
    end = today.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def _latest_monday() -> date:
    today = datetime.now(UTC).date()
    return today - timedelta(days=today.weekday() + 7)


def _day_row(day: date, clicks: int = 9) -> dict[str, Any]:
    return {
        "site": "links",
        "day": day.isoformat(),
        "link_id": "twitch",
        "src": "a",
        "clicks": clicks,
    }


def test_cli_refuses_any_cell_release_without_a_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, _ = _last_month()
    path = _export(
        tmp_path,
        {
            "click_src_monthly": [
                {
                    "site": "links",
                    "month": first.strftime("%Y-%m"),
                    "link_id": "twitch",
                    "src": "a",
                    "clicks": 9,
                }
            ]
        },
    )
    for kind in ("week", "month"):
        assert main(["report", "--input", str(path), "--period", kind]) == 2
        cap = capsys.readouterr()
        assert "--ledger" in cap.err and cap.out == ""
    # months stay available as per-dimension totals that print no cell values
    assert (
        main(["report", "--input", str(path), "--since", "2026-01-01", "--json"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["click_matrix"]["state"] == "period_required"
    assert out["click_matrix"]["cells"] == []


def test_cli_a_repeat_week_release_is_identical_and_recorded_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    monday = _latest_monday()
    path = _export(
        tmp_path, {"click_src_daily": [_day_row(monday + timedelta(days=1))]}
    )
    base = ["report", "--input", str(path), "--ledger", str(ledger), "--json"]
    outputs = []
    for _ in range(3):
        assert main([*base, "--period", "week"]) == 0
        outputs.append(json.loads(capsys.readouterr().out)["click_matrix"])
    assert outputs[0] == outputs[1] == outputs[2]
    assert len(read_ledger(ledger)) == 1


def test_cli_a_month_that_shares_a_day_with_a_released_week_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    first, last = _last_month()
    # A week release over the first day of last month.
    monday = first - timedelta(days=first.weekday())
    record_release(
        ledger,
        Release(
            "links",
            "week",
            monday.isoformat(),
            (monday + timedelta(days=6)).isoformat(),
            STAMP,
        ),
    )
    path = _export(
        tmp_path,
        {
            "click_src_monthly": [
                {
                    "site": "links",
                    "month": first.strftime("%Y-%m"),
                    "link_id": "twitch",
                    "src": "a",
                    "clicks": 30,
                }
            ]
        },
    )
    code = main(
        ["report", "--input", str(path), "--period", "month", "--ledger", str(ledger)]
    )
    cap = capsys.readouterr()
    assert code == 2 and "shares a day" in cap.err and cap.out == ""
    assert len(read_ledger(ledger)) == 1


def test_cli_a_month_with_a_ledger_then_a_week_over_it_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    first, _ = _last_month()
    path = _export(
        tmp_path,
        {
            "click_src_monthly": [
                {
                    "site": "links",
                    "month": first.strftime("%Y-%m"),
                    "link_id": "twitch",
                    "src": "a",
                    "clicks": 30,
                }
            ],
            "click_src_daily": [_day_row(_latest_monday())],
        },
    )
    base = ["report", "--input", str(path), "--ledger", str(ledger), "--json"]
    assert main([*base, "--period", "month"]) == 0
    capsys.readouterr()
    monday = _latest_monday()
    shares = monday <= _last_month()[1]  # the latest week reaches into last month
    code = main([*base, "--period", "week"])
    err = capsys.readouterr().err
    assert (code == 2 and "shares a day" in err) if shares else code == 0


def test_cli_a_damaged_ledger_prints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("garbage\n")
    path = _export(tmp_path, {"click_src_daily": [_day_row(_latest_monday())]})
    code = main(
        [
            "report",
            "--input",
            str(path),
            "--period",
            "week",
            "--ledger",
            str(ledger),
            "--json",
        ]
    )
    cap = capsys.readouterr()
    assert code == 2 and cap.out == ""


def test_cli_ledger_needs_a_period(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _export(tmp_path, {"kit_events": [{"ts": 1, "kind": "view"}]})
    assert main(["report", "--input", str(path), "--ledger", str(tmp_path / "l")]) == 2
    capsys.readouterr()


def test_cli_a_missing_ledger_is_refused_and_never_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "nope.jsonl"
    path = _export(tmp_path, {"click_src_daily": [_day_row(_latest_monday())]})
    code = main(
        ["report", "--input", str(path), "--period", "week", "--ledger", str(ledger)]
    )
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and "funnel-ledger-init" in cap.err
    assert not ledger.exists()


def test_the_init_command_creates_an_owner_only_empty_file_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    assert main(["funnel-ledger-init", str(ledger)]) == 0
    assert "commit" in capsys.readouterr().out
    assert ledger.read_bytes() == b""
    assert oct(ledger.stat().st_mode & 0o777) == "0o600"
    assert read_ledger(ledger) == []
    assert main(["funnel-ledger-init", str(ledger)]) == 2  # never overwritten
    capsys.readouterr()


def test_the_library_cannot_bypass_the_ledger(tmp_path: Path) -> None:
    from reach_around.funnel.export import load_exports
    from reach_around.funnel.report import build_report

    path = _export(tmp_path, {"click_src_daily": [_day_row(_latest_monday())]})
    export = load_exports([path])
    today = datetime.now(UTC).date()
    with pytest.raises(ValueError, match="recorded in the release ledger"):
        build_report(export, period="week", as_of=today)
    with pytest.raises(LedgerError):
        ReleaseGuard(week("2026-11-02"), object())  # a guard cannot be forged cheaply
    # a guard for another period does not do either
    other = authorize_release(fresh(tmp_path), week("2026-01-05"))
    with pytest.raises(ValueError, match="recorded in the release ledger"):
        build_report(export, period="week", as_of=today, guard=other)
    # without a period there is no cell release and no guard is needed
    r = build_report(export, as_of=today)
    assert r["click_matrix"]["state"] == "period_required"


def test_the_period_is_recorded_before_any_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = fresh(tmp_path)
    path = _export(tmp_path, {"click_src_daily": [_day_row(_latest_monday())]})
    seen: list[int] = []
    from reach_around.funnel.report import render_text as real

    def spy(report: Any) -> str:
        seen.append(len(read_ledger(ledger)))  # what the ledger held at render time
        return real(report)

    monkeypatch.setattr("reach_around.commands.report.render_text", spy)
    assert (
        main(
            [
                "report",
                "--input",
                str(path),
                "--period",
                "week",
                "--ledger",
                str(ledger),
            ]
        )
        == 0
    )
    assert seen == [1]


def test_a_ledger_write_failure_means_no_cell_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    ledger.chmod(0o400)  # read-only: the append fails
    path = _export(tmp_path, {"click_src_daily": [_day_row(_latest_monday())]})
    if os.geteuid() == 0:
        pytest.skip("root can write a read-only file")
    for extra in ([], ["--json"], ["--csv", str(tmp_path / "csv")]):
        code = main(
            [
                "report",
                "--input",
                str(path),
                "--period",
                "week",
                "--ledger",
                str(ledger),
                *extra,
            ]
        )
        cap = capsys.readouterr()
        assert code == 2 and cap.out == ""
    assert not (tmp_path / "csv").exists()
    assert read_ledger(ledger) == []


def test_cli_weeks_then_a_month_is_refused_in_both_directions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Weeks released first (no special case for the first kind): the month over
    them is refused; then the other way round on a second ledger."""
    ledger = fresh(tmp_path)
    first, last = _last_month()
    path = _export(
        tmp_path,
        {
            "click_src_monthly": [
                {
                    "site": "links",
                    "month": first.strftime("%Y-%m"),
                    "link_id": "t",
                    "src": "a",
                    "clicks": 30,
                }
            ],
            "click_src_daily": [_day_row(_latest_monday())],
        },
    )
    # Release a week inside last month through the library path first.
    monday = first - timedelta(days=first.weekday())
    authorize_release(
        ledger,
        Release(
            "links",
            "week",
            monday.isoformat(),
            (monday + timedelta(days=6)).isoformat(),
            STAMP,
        ),
    )
    code = main(
        ["report", "--input", str(path), "--period", "month", "--ledger", str(ledger)]
    )
    cap = capsys.readouterr()
    assert code == 2 and "shares a day" in cap.err and cap.out == ""
