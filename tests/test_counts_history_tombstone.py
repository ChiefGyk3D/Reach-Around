# SPDX-License-Identifier: Apache-2.0
"""Tombstones in the history store (issue #586; design in docs/research)."""

import fcntl
import hashlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import reach_around.counts.history as hist
from reach_around.cli import main
from reach_around.counts.history import (
    GENESIS,
    REASONS,
    HistoryError,
    Record,
    append_records,
    plan_redaction,
    read_history,
    redact_records,
    tombstoned,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)
REDACT_AT = datetime(2026, 11, 19, 12, 0, 0, tzinfo=UTC)
VALUE = 424242
NOTE = "SENTINEL-NOTE"
ERA = "SENTINEL-ERA"


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "youtube",
        "metric": "subscribers",
        "value": 10,
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
    }
    fields.update(over)
    return Record(**fields)


def add(path: Path, *records: Record) -> None:
    append_records(path, list(records), now=NOW)


def digests(path: Path) -> list[str]:
    data = path.read_bytes()
    return [hashlib.sha256(x).hexdigest() for x in data[:-1].split(b"\n")]


def build(path: Path) -> None:
    add(path, rec(value=VALUE, note=None, era=ERA))
    add(
        path,
        rec(
            as_of_utc="2026-10-01T09:00:00Z",
            value=VALUE + 1,
            note=NOTE,
            type="correction",
        ),
    )
    add(path, rec(as_of_utc="2026-10-02T09:00:00Z", value=11))
    add(path, rec(as_of_utc="2026-10-03T09:00:00Z", value=12))
    add(path, rec(as_of_utc="2026-10-03T09:00:00Z", source="hand_kept", value=77))


def redact(path: Path, **over: Any) -> int:
    history = read_history(path)
    plan = plan_redaction(
        history, platform="youtube", source="api", before="2026-10-03"
    )
    args: dict[str, Any] = {
        "reason": "platform_expiry",
        "trigger_date": "2026-10-20",
        "now": REDACT_AT,
        "confirm": True,
    }
    args.update(over)
    return redact_records(path, plan, **args)


def test_redacted_file_verifies_and_the_day_is_a_gap(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    assert redact(path) == 2
    history = read_history(path)
    assert tombstoned(history) == frozenset(
        {
            ("youtube", "subscribers", "2026-10-01T09:00:00Z", "api"),
            ("youtube", "subscribers", "2026-10-02T09:00:00Z", "api"),
        }
    )
    series = history.series("youtube", "subscribers")
    assert [p.value for p in series.points()] == [12, 77]  # api 12, hand-kept 77
    assert series.gaps(date(2026, 10, 1), date(2026, 10, 3)) == [
        date(2026, 10, 1),
        date(2026, 10, 2),
    ]
    assert len(path.read_bytes().splitlines()) == 4  # 2 tombstones + 2 kept rows


def test_no_old_value_note_era_or_digest_survives(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    old = digests(path)
    redact(path)
    text = path.read_text(encoding="ascii")
    for needle in (str(VALUE), str(VALUE + 1), NOTE, ERA, '"value":11', *old[:3]):
        assert needle not in text
    first = json.loads(text.splitlines()[0])
    assert first["type"] == "tombstone"
    assert "value" not in first and "note" not in first and "era" not in first
    assert first["reason"] == "platform_expiry"
    assert first["trigger_date"] == "2026-10-20"
    assert first["redacted_at_utc"] == "2026-11-19T12:00:00Z"
    assert first["run_id"].startswith("redact-")
    assert first["prev"] == GENESIS
    assert "replaces" not in first


def test_later_lines_are_rechained_and_tampering_is_caught(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    redact(path)
    read_history(path)  # chain intact
    tampered = path.read_bytes().replace(b'"value":12', b'"value":99', 1)
    assert tampered != path.read_bytes()
    path.write_bytes(tampered)
    with pytest.raises(HistoryError, match="hash chain is broken"):
        read_history(path)


def test_redacted_key_cannot_be_refilled(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    redact(path)
    before = path.read_bytes()
    with pytest.raises(HistoryError):
        add(path, rec(value=5))
    with pytest.raises(HistoryError):
        add(path, rec(value=5, type="correction", note="again"))
    with pytest.raises(HistoryError):  # same UTC day, same source, other time
        add(path, rec(as_of_utc="2026-10-01T20:00:00Z", value=5))
    assert path.read_bytes() == before
    # An untouched key still appends.
    add(path, rec(as_of_utc="2026-10-04T09:00:00Z", value=13))


def test_hand_kept_is_refused_without_the_flag(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    history = read_history(path)
    with pytest.raises(HistoryError, match="hand-kept"):
        plan_redaction(history, platform="youtube", source="hand_kept", before=None)
    with pytest.raises(HistoryError, match="hand-kept"):
        plan_redaction(history, platform="youtube", source="imported", before=None)
    api_only = plan_redaction(history, platform="youtube", source=None, before=None)
    assert {r.source for r in api_only} == {"api"}
    everything = plan_redaction(
        history,
        platform="youtube",
        source=None,
        before=None,
        include_hand_kept=True,
    )
    assert {r.source for r in everything} == {"api", "hand_kept"}
    # A hand-built plan cannot slip a hand-kept row past redact_records either.
    hand = [r for r in everything if r.source == "hand_kept"]
    before = path.read_bytes()
    with pytest.raises(HistoryError, match="hand-kept"):
        redact_records(
            path,
            hand,
            reason="owner_decision",
            trigger_date="2026-10-20",
            now=REDACT_AT,
            confirm=True,
        )
    assert path.read_bytes() == before
    redact_records(
        path,
        hand,
        reason="owner_decision",
        trigger_date="2026-10-20",
        now=REDACT_AT,
        confirm=True,
        include_hand_kept=True,
    )
    assert "hand_kept" in path.read_text(encoding="ascii")  # key stays, value gone
    assert '"value":77' not in path.read_text(encoding="ascii")


def test_plan_filters_and_never_touches_disk(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    history = read_history(path)
    plan = plan_redaction(
        history, platform="youtube", source="api", before="2026-10-03"
    )
    assert [r.as_of_utc for r in plan] == [
        "2026-10-01T09:00:00Z",
        "2026-10-02T09:00:00Z",
    ]
    assert (
        plan_redaction(
            history,
            platform="youtube",
            source="api",
            before="2026-10-03",
            metric="views",
        )
        == []
    )
    assert plan_redaction(history, platform="twitch", source="api", before=None) == []
    for bad in ("2026-13-01", "2026-1-1", "yesterday", "2026-10-03T00:00:00Z"):
        with pytest.raises(HistoryError):
            plan_redaction(history, platform="youtube", source="api", before=bad)
    assert path.read_bytes() == before


def test_without_confirm_nothing_is_written(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    for flag in (False, 1, "yes", None):
        with pytest.raises(HistoryError, match="confirm"):
            redact(path, confirm=flag)
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == [".h.jsonl.lock", "h.jsonl"]


def test_a_failed_write_leaves_the_original_and_no_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()

    def boom(*args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(hist, "_exchange", lambda *a: False)
    monkeypatch.setattr(os, "rename", boom)
    with pytest.raises(HistoryError, match="cannot write"):
        redact(path)
    monkeypatch.undo()
    assert path.read_bytes() == before
    assert sorted(os.listdir(tmp_path)) == [".h.jsonl.lock", "h.jsonl"]
    read_history(path)


def test_written_file_is_private_and_a_new_file(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    path.chmod(0o644)
    inode = path.stat().st_ino
    redact(path)
    assert (path.stat().st_mode & 0o777) == 0o600
    assert path.stat().st_ino != inode
    assert sorted(os.listdir(tmp_path)) == [".h.jsonl.lock", "h.jsonl"]


def test_symlink_fifo_and_symlinked_folder_are_refused(tmp_path: Path) -> None:
    real = tmp_path / "real.jsonl"
    build(real)
    plan = plan_redaction(
        read_history(real), platform="youtube", source="api", before=None
    )
    kwargs: dict[str, Any] = {
        "reason": "platform_expiry",
        "trigger_date": "2026-10-20",
        "now": REDACT_AT,
        "confirm": True,
    }
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)
    with pytest.raises(HistoryError):
        redact_records(link, plan, **kwargs)
    fifo = tmp_path / "pipe.jsonl"
    os.mkfifo(fifo)
    with pytest.raises(HistoryError):
        redact_records(fifo, plan, **kwargs)
    folder = tmp_path / "dir"
    folder.symlink_to(tmp_path)
    with pytest.raises(HistoryError):
        redact_records(folder / "real.jsonl", plan, **kwargs)
    with pytest.raises(HistoryError):
        redact_records(tmp_path / "missing.jsonl", plan, **kwargs)
    assert VALUE.__str__() in real.read_text(encoding="ascii")  # untouched


def test_only_the_closed_reasons_and_real_dates_are_accepted(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    assert set(REASONS) == {
        "platform_expiry",
        "platform_revocation",
        "platform_request",
        "user_request",
        "owner_decision",
    }

    class Hostile(str):
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("hostile")

        __hash__ = str.__hash__

    for reason in ("because I said so", "", "PLATFORM_EXPIRY", None, 5, Hostile("x")):
        with pytest.raises(HistoryError):
            redact(path, reason=reason)
    for trigger in ("2026-13-40", "2026-10-1", "later", None, "2026-12-01", ""):
        with pytest.raises(HistoryError):
            redact(path, trigger_date=trigger)  # 2026-12-01 is after redaction
    assert path.read_bytes() == before


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    assert redact(path) == 2
    once = path.read_bytes()
    assert redact(path) == 0
    assert path.read_bytes() == once
    # A stale plan (rows already gone) is refused, not applied twice.
    stale = [rec(as_of_utc="2026-10-01T09:00:00Z")]
    with pytest.raises(HistoryError, match="plan"):
        redact_records(
            path,
            stale,
            reason="platform_expiry",
            trigger_date="2026-10-20",
            now=REDACT_AT,
            confirm=True,
        )
    assert path.read_bytes() == once


def test_a_second_redaction_extends_the_first(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    redact(path)
    history = read_history(path)
    plan = plan_redaction(history, platform="youtube", source="api", before=None)
    assert len(plan) == 1
    redact_records(
        path,
        plan,
        reason="user_request",
        trigger_date="2026-10-21",
        now=REDACT_AT,
        confirm=True,
    )
    assert len(tombstoned(read_history(path))) == 3


def test_a_running_refresh_makes_redaction_refuse(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    plan = plan_redaction(
        read_history(path), platform="youtube", source="api", before=None
    )
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # what a refresh holds while it appends
        with pytest.raises(HistoryError, match="locked"):
            redact_records(
                path,
                plan,
                reason="platform_expiry",
                trigger_date="2026-10-20",
                now=REDACT_AT,
                confirm=True,
                lock_wait=0.05,
            )
    finally:
        os.close(fd)
    assert path.read_bytes() == before


def test_an_append_that_waited_through_a_redaction_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    real_lock = hist._lock

    def swap_after_lock(*args: Any, **kwargs: Any) -> None:
        real_lock(*args, **kwargs)
        replacement = tmp_path / "other"
        replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path)  # what a redaction's rename does

    monkeypatch.setattr(hist, "_lock", swap_after_lock)
    with pytest.raises(HistoryError, match="replaced"):
        add(path, rec(as_of_utc="2026-10-09T09:00:00Z", value=1))


def tomb(**over: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "type": "tombstone",
        "platform": "youtube",
        "metric": "subscribers",
        "as_of_utc": "2026-10-01T09:00:00Z",
        "source": "api",
        "reason": "platform_expiry",
        "trigger_date": "2026-10-20",
        "redacted_at_utc": "2026-11-19T12:00:00Z",
        "run_id": "redact-1",
        "prev": GENESIS,
    }
    fields.update(over)
    return {k: v for k, v in fields.items() if v is not None}


def line(obj: dict[str, Any]) -> bytes:
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return text.encode("ascii") + b"\n"


def test_a_well_formed_tombstone_line_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(line(tomb()))
    assert len(tombstoned(read_history(path))) == 1


@pytest.mark.parametrize(
    "over",
    [
        {"value": 5},
        {"note": "free text"},
        {"era": "x"},
        {"replaces": "a" * 64},
        {"api_provider": "youtube_data_api"},
        {"reason": "free text"},
        {"reason": None},
        {"trigger_date": None},
        {"trigger_date": "2026-02-30"},
        {"trigger_date": "2026-10-20T00:00:00Z"},
        {"trigger_date": "2026-12-01"},  # after redacted_at_utc
        {"redacted_at_utc": "2999-01-01T00:00:00Z"},
        {"redacted_at_utc": "2026-11-19"},
        {"run_id": "refresh-1"},
        {"run_id": None},
        {"source": "elsewhere"},
        {"source": None},
        {"platform": "Bad Platform"},
        {"as_of_utc": "yesterday"},
        {"as_of_utc": "2999-01-01T00:00:00Z"},
        {"prev": "abc"},
        {"prev": "f" * 64},
        {"reason": 5},
        {"trigger_date": ["2026-10-20"]},
    ],
)
def test_hostile_tombstone_lines_are_refused(
    tmp_path: Path, over: dict[str, Any]
) -> None:
    path = tmp_path / "h.jsonl"
    path.write_bytes(line(tomb(**over)))
    with pytest.raises(HistoryError):
        read_history(path)


def test_duplicate_tombstone_and_tombstone_over_a_live_record_are_refused(
    tmp_path: Path,
) -> None:
    first = line(tomb())
    second = line(tomb(prev=hashlib.sha256(first[:-1]).hexdigest()))
    path = tmp_path / "h.jsonl"
    path.write_bytes(first + second)
    with pytest.raises(HistoryError, match="duplicate"):
        read_history(path)
    path2 = tmp_path / "g.jsonl"
    add(path2, rec())
    live = path2.read_bytes()
    nxt = line(tomb(prev=hashlib.sha256(live[:-1]).hexdigest()))
    path2.write_bytes(live + nxt)
    with pytest.raises(HistoryError):
        read_history(path2)


def test_a_record_line_after_a_tombstone_for_its_key_is_refused(tmp_path: Path) -> None:
    first = line(tomb())
    prev = hashlib.sha256(first[:-1]).hexdigest()
    record = line(
        {
            "platform": "youtube",
            "metric": "subscribers",
            "value": 1,
            "as_of_utc": "2026-10-01T09:00:00Z",
            "source": "api",
            "run_id": "r",
            "prev": prev,
        }
    )
    path = tmp_path / "h.jsonl"
    path.write_bytes(first + record)
    with pytest.raises(HistoryError, match="redacted"):
        read_history(path)


# CLI


def run_cli(path: Path, *extra: str) -> int:
    return main(
        [
            "counts",
            "history-redact",
            "--file",
            str(path),
            "--platform",
            "youtube",
            "--source",
            "api",
            "--before",
            "2026-10-03",
            "--reason",
            "platform_expiry",
            "--trigger-date",
            "2026-10-20",
            *extra,
        ]
    )


def test_cli_default_is_a_dry_run_that_prints_counts_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    assert run_cli(path) == 0
    out = capsys.readouterr()
    text = out.out + out.err
    assert "dry run" in text and "2 " in text
    for needle in (str(VALUE), NOTE, ERA, *digests(path)):
        assert needle not in text
    assert run_cli(path, "--dry-run") == 0
    assert path.read_bytes() == before


def test_cli_confirm_rewrites_and_verify_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    assert run_cli(path, "--confirm") == 0
    out = capsys.readouterr().out
    assert "redacted 2" in out
    assert str(VALUE) not in out
    assert main(["counts", "history-verify", "--file", str(path)]) == 0
    assert len(tombstoned(read_history(path))) == 2
    assert run_cli(path, "--confirm") == 0  # idempotent
    assert "redacted 0" in capsys.readouterr().out


def test_cli_refuses_hand_kept_without_the_flag_and_bad_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    before = path.read_bytes()
    args = [
        "counts",
        "history-redact",
        "--file",
        str(path),
        "--platform",
        "youtube",
        "--source",
        "hand_kept",
        "--reason",
        "owner_decision",
        "--trigger-date",
        "2026-10-20",
        "--confirm",
    ]
    assert main(args) == 1
    assert "hand-kept" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert main([*args, "--include-hand-kept"]) == 0
    with pytest.raises(SystemExit) as bad:  # reason outside the closed list
        run_cli(path, "--reason", "whatever")
    assert bad.value.code == 2
    both = [*args[:-1], "--dry-run", "--confirm"]
    with pytest.raises(SystemExit):
        main(both)
    with pytest.raises(SystemExit):  # the trigger date is required
        main(
            [
                "counts",
                "history-redact",
                "--file",
                str(path),
                "--platform",
                "youtube",
                "--reason",
                "owner_decision",
            ]
        )


def test_two_tombstones_for_one_utc_day_are_refused(tmp_path: Path) -> None:
    first = line(tomb())
    second = line(
        tomb(
            as_of_utc="2026-10-01T20:00:00Z",
            prev=hashlib.sha256(first[:-1]).hexdigest(),
        )
    )
    path = tmp_path / "h.jsonl"
    path.write_bytes(first + second)
    with pytest.raises(HistoryError, match="UTC day"):
        read_history(path)
