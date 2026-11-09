# SPDX-License-Identifier: Apache-2.0
"""Funnel release integrity: the release ledger and its head file, the one
release function, completeness evidence and capacity markers.

Threat model: the owner runs the report on his own machine over his own data and
is trusted. These tests cover accidents (a cut or restored ledger, a partial
export, an overlapping week and month, a repeat with different data), not an
owner who edits his files on purpose."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.cli import main
from reach_around.funnel import ledger as mod
from reach_around.funnel.export import load_exports
from reach_around.funnel.ledger import (
    LedgerError,
    Release,
    init_ledger,
    read_ledger,
    reanchor_ledger,
    record_release,
)
from reach_around.funnel.release import IncompletePeriod, release_report
from reach_around.funnel.report import build_report

STAMP = "2026-11-09T10:00:00Z"
D1 = "1" * 64
D2 = "2" * 64


def week(start: str, site: str = "links", digest: str = D1) -> Release:
    end = (date.fromisoformat(start) + timedelta(days=6)).isoformat()
    return Release(site, "week", start, end, STAMP, digest)


def month(start: str, end: str, site: str = "links", digest: str = D1) -> Release:
    return Release(site, "month", start, end, STAMP, digest)


def fresh(tmp_path: Path, name: str = "ledger.jsonl") -> Path:
    ledger = tmp_path / name
    init_ledger(ledger)
    return ledger


def head_of(ledger: Path) -> Path:
    return ledger.with_name(ledger.name + ".head")


# ---- the overlap rule ------------------------------------------------------


def test_a_month_after_its_weeks_is_refused(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, week("2026-11-09")) == "release"
    with pytest.raises(LedgerError, match="shares a day"):
        record_release(ledger, month("2026-11-01", "2026-11-30"))
    assert len(read_ledger(ledger)) == 1


def test_a_week_after_its_month_is_refused_including_a_straddling_week(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    record_release(ledger, month("2026-10-01", "2026-10-31"))
    with pytest.raises(LedgerError, match="shares a day"):
        record_release(ledger, week("2026-10-26"))
    assert record_release(ledger, week("2026-11-02")) == "release"


def test_a_week_next_to_a_month_is_allowed_both_ways(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    record_release(ledger, month("2026-10-01", "2026-10-31"))
    record_release(ledger, week("2026-11-02"))
    record_release(ledger, week("2026-11-09"))
    assert [r.kind for r in read_ledger(ledger)] == ["month", "week", "week"]


def test_another_site_is_not_affected(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    record_release(ledger, week("2026-11-02", site="links"))
    assert record_release(ledger, month("2026-11-01", "2026-11-30", site="blog"))


@pytest.mark.parametrize(
    "bad",
    [
        Release("links", "week", "2026-11-03", "2026-11-09", STAMP, D1),
        Release("links", "week", "2026-11-02", "2026-11-09", STAMP, D1),
        Release("links", "month", "2026-11-02", "2026-11-30", STAMP, D1),
        Release("links", "month", "2026-11-01", "2026-11-29", STAMP, D1),
        Release("links", "year", "2026-01-01", "2026-12-31", STAMP, D1),
        Release("Links", "week", "2026-11-02", "2026-11-08", STAMP, D1),
        Release("links", "week", "2026-11-02", "2026-11-08", "yesterday", D1),
        Release("links", "week", "2026-02-30", "2026-03-08", STAMP, D1),
        Release("links", "week", "2026-11-02", "2026-11-08", STAMP, "nothex"),
    ],
)
def test_bad_records_are_refused(tmp_path: Path, bad: Release) -> None:
    with pytest.raises(LedgerError):
        record_release(fresh(tmp_path), bad)


# ---- a repeat is the same release only with the same data ------------------


def test_a_repeat_with_the_same_digest_is_allowed_and_written_once(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    assert record_release(ledger, week("2026-11-02")) == "release"
    assert record_release(ledger, week("2026-11-02")) == "repeat"
    assert len(read_ledger(ledger)) == 1


def test_a_repeat_with_changed_data_is_refused_until_superseded(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    record_release(ledger, week("2026-11-02", digest=D1))
    with pytest.raises(LedgerError, match="different data"):
        record_release(ledger, week("2026-11-02", digest=D2))
    assert record_release(ledger, week("2026-11-02", digest=D2), supersede=True) == (
        "supersede"
    )
    rows = read_ledger(ledger)
    assert [r.type for r in rows] == ["release", "supersede"]
    # the latest digest is now the reference
    assert record_release(ledger, week("2026-11-02", digest=D2)) == "repeat"
    with pytest.raises(LedgerError):
        record_release(ledger, week("2026-11-02", digest=D1))


# ---- the head file catches accidents --------------------------------------


def seeded(tmp_path: Path) -> Path:
    ledger = fresh(tmp_path)
    record_release(ledger, week("2026-11-02"))
    record_release(ledger, week("2026-11-09"))
    record_release(ledger, week("2026-11-16"))
    return ledger


def test_both_files_are_owner_only_and_the_head_names_the_chain(
    tmp_path: Path,
) -> None:
    ledger = seeded(tmp_path)
    assert oct(ledger.stat().st_mode & 0o777) == "0o600"
    assert oct(head_of(ledger).stat().st_mode & 0o777) == "0o600"
    head = json.loads(head_of(ledger).read_text())
    assert head["seq"] == 3
    last = ledger.read_bytes().rstrip(b"\n").split(b"\n")[-1]
    assert head["head"] == hashlib.sha256(last).hexdigest()
    assert not list(tmp_path.glob("*.tmp"))


def test_deleting_only_the_last_complete_line_is_detected(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    lines = ledger.read_bytes().splitlines(keepends=True)
    ledger.write_bytes(b"".join(lines[:-1]))
    with pytest.raises(LedgerError, match="shorter than its .head"):
        read_ledger(ledger)
    with pytest.raises(LedgerError, match="restore the ledger"):
        record_release(ledger, week("2026-11-23"))


def test_replaying_an_older_ledger_with_the_newer_head_is_detected(
    tmp_path: Path,
) -> None:
    ledger = seeded(tmp_path)
    older = tmp_path / "older"
    older.mkdir()
    old = fresh(older)
    record_release(old, week("2026-11-02"))
    shutil.copy(old, ledger)  # an older valid ledger, the newer head stays
    with pytest.raises(LedgerError, match="shorter"):
        read_ledger(ledger)


def test_a_different_ledger_of_the_same_length_is_detected(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    twin = fresh(other)
    for start in ("2026-11-02", "2026-11-09", "2026-11-16"):
        record_release(twin, week(start, digest=D2))
    shutil.copy(twin, ledger)
    with pytest.raises(LedgerError, match="disagree"):
        read_ledger(ledger)


def test_a_missing_head_on_a_non_empty_ledger_is_detected_until_reanchored(
    tmp_path: Path,
) -> None:
    ledger = seeded(tmp_path)
    head_of(ledger).unlink()
    with pytest.raises(LedgerError, match=".head file is missing"):
        read_ledger(ledger)
    with pytest.raises(LedgerError):
        record_release(ledger, week("2026-11-23"))
    assert reanchor_ledger(ledger) == 3
    assert len(read_ledger(ledger)) == 3


def test_a_missing_head_on_an_empty_ledger_is_fine(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    head_of(ledger).unlink()
    assert read_ledger(ledger) == []


def test_the_crash_window_one_record_ahead_is_accepted_and_repaired(
    tmp_path: Path,
) -> None:
    ledger = seeded(tmp_path)
    head = head_of(ledger).read_bytes()
    record_release(ledger, week("2026-11-23"))
    head_of(ledger).write_bytes(head)  # the head write never happened
    assert len(read_ledger(ledger)) == 4
    record_release(ledger, week("2026-11-30"))
    assert json.loads(head_of(ledger).read_text())["seq"] == 5
    # two records ahead is not a crash window
    head_of(ledger).write_bytes(head)
    with pytest.raises(LedgerError, match="does not know"):
        read_ledger(ledger)


def test_a_bad_head_file_fails_closed(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    for junk in (b"", b"[]", b'{"seq":1}', b'{"seq":-1,"head":"' + b"0" * 64 + b'"}'):
        head_of(ledger).write_bytes(junk)
        with pytest.raises(LedgerError):
            read_ledger(ledger)


def test_a_symlinked_head_or_ledger_is_refused(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    link = tmp_path / "link.jsonl"
    os.symlink(ledger, link)
    with pytest.raises(LedgerError):
        read_ledger(link)
    real_head = head_of(ledger)
    moved = tmp_path / "moved.head"
    real_head.rename(moved)
    os.symlink(moved, real_head)
    with pytest.raises(LedgerError):
        read_ledger(ledger)


# ---- the ledger itself fails closed ----------------------------------------


def test_a_changed_line_breaks_the_chain(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    ledger.write_text(ledger.read_text().replace("2026-11-02", "2026-11-23", 1))
    with pytest.raises(LedgerError):
        read_ledger(ledger)


def test_a_cut_short_last_line_is_not_repaired(tmp_path: Path) -> None:
    ledger = seeded(tmp_path)
    data = ledger.read_bytes()
    ledger.write_bytes(data[:-10])
    with pytest.raises(LedgerError):
        read_ledger(ledger)
    assert ledger.read_bytes() == data[:-10]


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
    ledger = fresh(tmp_path)
    ledger.write_bytes(junk)
    head_of(ledger).unlink()
    with pytest.raises(LedgerError):
        read_ledger(ledger)


def test_a_file_that_breaks_the_rule_itself_is_corrupt(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    prev = "0" * 64
    out = b""
    for rel in (week("2026-11-02"), month("2026-11-01", "2026-11-30")):
        line = mod._line(rel, prev)
        out += line + b"\n"
        prev = hashlib.sha256(line).hexdigest()
    ledger.write_bytes(out)
    head_of(ledger).unlink()
    reanchor = None
    with pytest.raises(LedgerError, match="release rule"):
        reanchor = reanchor_ledger(ledger)
    assert reanchor is None


def test_an_oversized_ledger_is_refused(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    ledger.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    with pytest.raises(LedgerError):
        read_ledger(ledger)


def test_init_never_overwrites_and_a_missing_ledger_is_never_created(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    with pytest.raises(LedgerError, match="not overwritten"):
        init_ledger(ledger)
    ghost = tmp_path / "ghost.jsonl"
    with pytest.raises(LedgerError, match="funnel-ledger-init"):
        record_release(ghost, week("2026-11-02"))
    assert not ghost.exists()


# ---- the one release function ----------------------------------------------

MONDAY = date(2026, 11, 2)
AS_OF = date(2026, 11, 10)  # the latest complete week is 2026-11-02 .. 2026-11-08


def _row(day: str, clicks: int = 9, link: str = "twitch") -> dict[str, Any]:
    return {"site": "links", "day": day, "link_id": link, "src": "a", "clicks": clicks}


def _tables(
    *,
    seal_days: int = 7,
    clicks: int = 9,
    capacity: dict[str, int] | None = None,
    total: int | None = None,
) -> dict[str, Any]:
    tables: dict[str, Any] = {
        "click_src_daily": [_row("2026-11-03", clicks)],
        "click_country_daily": [
            {
                "site": "links",
                "day": "2026-11-03",
                "link_id": "twitch",
                "country": "US",
                "clicks": clicks if total is None else total,
            }
        ],
        "click_seal": [
            {
                "site": "links",
                "day": (MONDAY + timedelta(days=n)).isoformat(),
                "sealed_at": 1,
            }
            for n in range(seal_days)
        ],
    }
    if capacity:
        tables["click_capacity"] = [
            {"site": "links", "day": d, "marked_at": 1, "first_hour": 3, "folded": n}
            for d, n in capacity.items()
        ]
    return tables


def _load(tmp_path: Path, tables: dict[str, Any], name: str = "export.json") -> Any:
    path = tmp_path / name
    path.write_text(json.dumps({"tables": tables}), encoding="utf-8")
    return load_exports([path])


def test_the_library_has_no_period_without_the_release_function(
    tmp_path: Path,
) -> None:
    export = _load(tmp_path, _tables())
    with pytest.raises(ValueError, match="release_report"):
        build_report(export, period="week", as_of=AS_OF)
    # Nothing to forge: there is no guard object to build.
    assert not hasattr(mod, "ReleaseGuard") and not hasattr(mod, "_ISSUED")
    # The only door records first.
    ledger = fresh(tmp_path)
    report, status = release_report(export, ledger, period="week", as_of=AS_OF)
    assert status == "release" and report["click_matrix"]["state"] == "ok"
    assert len(read_ledger(ledger)) == 1


def test_a_period_is_recorded_before_it_is_returned_and_a_failed_record_returns_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = _load(tmp_path, _tables())
    ledger = fresh(tmp_path)

    def boom(*a: Any, **k: Any) -> str:
        raise LedgerError("cannot write the ledger")

    monkeypatch.setattr("reach_around.funnel.release.record_release", boom)
    with pytest.raises(LedgerError):
        release_report(export, ledger, period="week", as_of=AS_OF)
    assert read_ledger(ledger) == []


def test_a_failed_build_writes_no_record(tmp_path: Path) -> None:
    export = _load(tmp_path, _tables())
    ledger = fresh(tmp_path)
    with pytest.raises(ValueError, match="floor"):
        release_report(export, ledger, period="week", as_of=AS_OF, floor=1)
    assert read_ledger(ledger) == []


def test_a_partial_export_with_missing_seal_days_is_refused(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    export = _load(tmp_path, _tables(seal_days=5))
    with pytest.raises(IncompletePeriod, match="2 day"):
        release_report(export, ledger, period="week", as_of=AS_OF)
    no_seal = _load(tmp_path, {"click_src_daily": [_row("2026-11-03")]}, "b.json")
    with pytest.raises(IncompletePeriod, match="click_seal"):
        release_report(no_seal, ledger, period="week", as_of=AS_OF)
    assert read_ledger(ledger) == []


def test_a_month_without_its_rollup_marker_is_refused(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    monthly = {
        "site": "links",
        "month": "2026-10",
        "link_id": "twitch",
        "src": "a",
        "clicks": 30,
    }
    for tables, match in (
        ({"click_src_monthly": [monthly]}, "click_month_log"),
        (
            {
                "click_src_monthly": [monthly],
                "click_month_log": [
                    {
                        "site": "links",
                        "month": "2026-09",
                        "rolled_at": 1,
                        "exhausted_days": 0,
                        "folded_clicks": 0,
                    }
                ],
            },
            "no completed rollup marker",
        ),
    ):
        export = _load(tmp_path, tables)
        with pytest.raises(IncompletePeriod, match=match):
            release_report(export, ledger, period="month", as_of=date(2026, 11, 9))
    ok = _load(
        tmp_path,
        {
            "click_src_monthly": [monthly],
            "click_month_log": [
                {
                    "site": "links",
                    "month": "2026-10",
                    "rolled_at": 1,
                    "exhausted_days": 0,
                    "folded_clicks": 0,
                }
            ],
        },
    )
    release_report(ok, ledger, period="month", as_of=date(2026, 11, 9))
    assert [r.kind for r in read_ledger(ledger)] == ["month"]


def test_a_repeat_with_changed_data_is_refused_and_supersede_is_recorded(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    first = _load(tmp_path, _tables(clicks=9))
    release_report(first, ledger, period="week", as_of=AS_OF)
    again, status = release_report(first, ledger, period="week", as_of=AS_OF)
    assert status == "repeat"
    late = _load(tmp_path, _tables(clicks=12), "late.json")
    with pytest.raises(LedgerError, match="different data"):
        release_report(late, ledger, period="week", as_of=AS_OF)
    # run-date independence: the same data on another day is still a repeat
    assert (
        release_report(first, ledger, period="week", as_of=AS_OF + timedelta(days=3))[1]
        == "repeat"
    )
    _, status = release_report(late, ledger, period="week", as_of=AS_OF, supersede=True)
    assert status == "supersede"
    assert [r.type for r in read_ledger(ledger)] == ["release", "supersede"]


def test_the_digest_never_reaches_the_report(tmp_path: Path) -> None:
    export = _load(tmp_path, _tables())
    ledger = fresh(tmp_path)
    report, _ = release_report(export, ledger, period="week", as_of=AS_OF)
    digest = read_ledger(ledger)[0].digest
    assert digest not in json.dumps(report)


# ---- counting capacity -----------------------------------------------------


def test_one_flooded_hour_under_the_limit_releases_with_a_warning(
    tmp_path: Path,
) -> None:
    from reach_around.funnel.report import render_text

    ledger = fresh(tmp_path)
    export = _load(tmp_path, _tables(total=1000, capacity={"2026-11-04": 30}))
    report, status = release_report(export, ledger, period="week", as_of=AS_OF)
    assert status == "release"
    cap = report["capacity"]
    assert cap["days"] == ["2026-11-04"] and cap["folded_clicks"] == 30
    assert cap["share_percent"] == 3.0
    text = render_text(report)
    assert "counting capacity was exhausted on 2026-11-04" in text
    assert "about 3.0% of the clicks" in text


def test_a_heavy_flood_over_the_limit_refuses_with_the_numbers(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    export = _load(tmp_path, _tables(total=1000, capacity={"2026-11-04": 600}))
    with pytest.raises(IncompletePeriod, match="600 of 1000 clicks.*60.0%.*5.0%"):
        release_report(export, ledger, period="week", as_of=AS_OF)
    assert read_ledger(ledger) == []


def test_the_limit_is_five_percent_and_one_hour_cannot_cross_it_in_a_normal_week(
    tmp_path: Path,
) -> None:
    # 1000 normal clicks in the week; an attacker folds clicks in one hour of one day.
    for folded, ok in ((40, True), (50, True), (51, False)):
        ledger = fresh(tmp_path, f"l{folded}.jsonl")
        export = _load(
            tmp_path,
            _tables(total=1000, capacity={"2026-11-04": folded}),
            f"e{folded}.json",
        )
        if ok:
            release_report(export, ledger, period="week", as_of=AS_OF)
        else:
            with pytest.raises(IncompletePeriod, match="exhausted"):
                release_report(export, ledger, period="week", as_of=AS_OF)


def test_capacity_with_no_counted_clicks_is_refused(tmp_path: Path) -> None:
    ledger = fresh(tmp_path)
    tables = _tables(capacity={"2026-11-04": 3})
    tables["click_country_daily"] = []
    with pytest.raises(IncompletePeriod, match="no clicks counted"):
        release_report(_load(tmp_path, tables), ledger, period="week", as_of=AS_OF)


def _month_tables(total: int, exhausted: int, folded: int) -> dict[str, Any]:
    return {
        "click_src_monthly": [
            {
                "site": "links",
                "month": "2026-10",
                "link_id": "t",
                "src": "a",
                "clicks": total,
            }
        ],
        "click_country_monthly": [
            {
                "site": "links",
                "month": "2026-10",
                "link_id": "t",
                "country": "US",
                "clicks": total,
            }
        ],
        "click_month_log": [
            {
                "site": "links",
                "month": "2026-10",
                "rolled_at": 1,
                "exhausted_days": exhausted,
                "folded_clicks": folded,
            }
        ],
    }


def test_a_month_follows_the_same_share_rule_from_its_rollup_marker(
    tmp_path: Path,
) -> None:
    ledger = fresh(tmp_path)
    ok = _load(tmp_path, _month_tables(2000, 2, 80))
    report, _ = release_report(ok, ledger, period="month", as_of=date(2026, 11, 9))
    assert report["capacity"]["months"] == ["2026-10"]
    assert report["capacity"]["share_percent"] == 4.0
    bad = _load(tmp_path, _month_tables(2000, 2, 400), "bad.json")
    with pytest.raises(IncompletePeriod, match="exhausted"):
        release_report(
            bad, fresh(tmp_path, "m.jsonl"), period="month", as_of=date(2026, 11, 9)
        )


def test_the_report_warns_about_exhausted_days_in_its_window(tmp_path: Path) -> None:
    from reach_around.funnel.report import render_text

    export = _load(
        tmp_path,
        _tables(total=500, capacity={"2026-11-04": 10, "2026-11-06": 5}),
    )
    report = build_report(export, since="2026-11-01", until="2026-11-30")
    assert report["capacity"]["days"] == ["2026-11-04", "2026-11-06"]
    assert report["capacity"]["folded_clicks"] == 15
    text = render_text(report)
    assert (
        "counting capacity was exhausted on 2026-11-04, 2026-11-06; cell counts "
        "for those days are incomplete" in text
    )
    outside = build_report(export, since="2026-12-01", until="2026-12-31")
    assert outside["capacity"]["days"] == []
    assert "exhausted" not in render_text(outside)


# ---- the command -----------------------------------------------------------


def _export_file(tmp_path: Path, tables: dict[str, Any]) -> Path:
    path = tmp_path / "export.json"
    path.write_text(json.dumps({"tables": tables}), encoding="utf-8")
    return path


def _latest_week() -> date:
    today = datetime.now(UTC).date()
    return today - timedelta(days=today.weekday() + 7)


def _live_tables(clicks: int = 9, capacity: bool = False) -> dict[str, Any]:
    monday = _latest_week()
    tables: dict[str, Any] = {
        "click_src_daily": [_row((monday + timedelta(days=1)).isoformat(), clicks)],
        "click_country_daily": [
            {
                "site": "links",
                "day": (monday + timedelta(days=1)).isoformat(),
                "link_id": "twitch",
                "country": "US",
                "clicks": clicks,
            }
        ],
        "click_seal": [
            {
                "site": "links",
                "day": (monday + timedelta(days=n)).isoformat(),
                "sealed_at": 1,
            }
            for n in range(7)
        ],
    }
    if capacity:
        tables["click_capacity"] = [
            {
                "site": "links",
                "day": monday.isoformat(),
                "marked_at": 1,
                "first_hour": 3,
                "folded": 900,
            }
        ]
    return tables


def test_cli_refuses_any_cell_release_without_a_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _export_file(tmp_path, _live_tables())
    for kind in ("week", "month"):
        assert main(["report", "--input", str(path), "--period", kind]) == 2
        cap = capsys.readouterr()
        assert "--ledger" in cap.err and cap.out == ""
    # totals only need no ledger and print no cell values
    assert (
        main(["report", "--input", str(path), "--since", "2020-01-01", "--json"]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["click_matrix"]["state"] == "period_required"
    assert out["click_matrix"]["cells"] == []


def test_cli_a_missing_ledger_is_refused_and_never_created(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "nope.jsonl"
    path = _export_file(tmp_path, _live_tables())
    code = main(
        ["report", "--input", str(path), "--period", "week", "--ledger", str(ledger)]
    )
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and "funnel-ledger-init" in cap.err
    assert not ledger.exists()


def test_cli_init_and_reanchor(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger.jsonl"
    assert main(["funnel-ledger-init", str(ledger)]) == 0
    assert "commit" in capsys.readouterr().out
    assert ledger.read_bytes() == b""
    assert head_of(ledger).exists()
    assert main(["funnel-ledger-init", str(ledger)]) == 2
    capsys.readouterr()
    record_release(ledger, week("2026-11-02"))
    head_of(ledger).unlink()
    assert main(["funnel-ledger-reanchor", str(ledger)]) == 0
    assert "1 record" in capsys.readouterr().out


def test_cli_repeat_is_identical_and_recorded_once_and_supersede_warns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    path = _export_file(tmp_path, _live_tables())
    base = ["report", "--input", str(path), "--ledger", str(ledger), "--json"]
    outputs = []
    for _ in range(3):
        assert main([*base, "--period", "week"]) == 0
        outputs.append(json.loads(capsys.readouterr().out)["click_matrix"])
    assert outputs[0] == outputs[1] == outputs[2]
    assert len(read_ledger(ledger)) == 1
    changed = _export_file(tmp_path, _live_tables(clicks=20))
    other = ["report", "--input", str(changed), "--ledger", str(ledger), "--json"]
    assert main([*other, "--period", "week"]) == 2
    assert "different data" in capsys.readouterr().err
    assert main([*other, "--period", "week", "--supersede"]) == 0
    cap = capsys.readouterr()
    assert "correction release" in cap.err
    assert [r.type for r in read_ledger(ledger)] == ["release", "supersede"]


def test_cli_an_incomplete_week_prints_nothing_and_records_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    tables = _live_tables()
    tables["click_seal"] = tables["click_seal"][:4]
    path = _export_file(tmp_path, tables)
    code = main(
        ["report", "--input", str(path), "--period", "week", "--ledger", str(ledger)]
    )
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and "not sealed" in cap.err
    assert read_ledger(ledger) == []


def test_cli_an_exhausted_day_refuses_the_cells(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    path = _export_file(tmp_path, _live_tables(capacity=True))
    code = main(
        ["report", "--input", str(path), "--period", "week", "--ledger", str(ledger)]
    )
    cap = capsys.readouterr()
    assert code == 2 and cap.out == "" and "exhausted" in cap.err


def test_cli_a_ledger_write_failure_means_no_cell_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can write a read-only file")
    ledger = fresh(tmp_path)
    ledger.chmod(0o400)
    path = _export_file(tmp_path, _live_tables())
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


def test_cli_a_damaged_ledger_prints_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = seeded(tmp_path)
    lines = ledger.read_bytes().splitlines(keepends=True)
    ledger.write_bytes(b"".join(lines[:-1]))  # the last line deleted
    path = _export_file(tmp_path, _live_tables())
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
    assert code == 2 and cap.out == "" and "restore the ledger" in cap.err


def test_cli_a_month_over_a_released_week_is_refused_in_both_directions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = fresh(tmp_path)
    monday = _latest_week()
    tables = _live_tables()
    path = _export_file(tmp_path, tables)
    base = ["report", "--input", str(path), "--ledger", str(ledger), "--json"]
    assert main([*base, "--period", "week"]) == 0
    capsys.readouterr()
    # Hand a month record that covers the released week's first day.
    first = monday.replace(day=1)
    nxt = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    with pytest.raises(LedgerError, match="shares a day"):
        record_release(
            ledger, month(first.isoformat(), (nxt - timedelta(days=1)).isoformat())
        )
    assert len(read_ledger(ledger)) == 1


def test_cli_ledger_and_supersede_need_a_period(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _export_file(tmp_path, {"kit_events": [{"ts": 1, "kind": "view"}]})
    assert main(["report", "--input", str(path), "--ledger", str(tmp_path / "l")]) == 2
    assert main(["report", "--input", str(path), "--supersede"]) == 2
    capsys.readouterr()
