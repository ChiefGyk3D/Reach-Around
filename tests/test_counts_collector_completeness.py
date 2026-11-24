# SPDX-License-Identifier: Apache-2.0
"""Count collectors and the history store: completeness and recovery (#766).

Synthetic data only. Covers a streamed append, a retry after a partly failed
write, and a retention policy built without the config model.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.counts as counts_cmd
import reach_around.counts.history as hist
from fakenet import FakeTransport, reply
from reach_around.counts.files import write_atomic
from reach_around.counts.history import (
    HistoryError,
    Record,
    append_records,
    dump_new_history,
    read_history,
)
from reach_around.counts.pending import (
    MAX_IDS,
    PendingError,
    mark_pending,
    pending_path,
    read_pending,
)
from reach_around.counts.retention import RetentionPolicy, retention_warnings

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)


def rec(**over: Any) -> Record:
    fields: dict[str, Any] = {
        "platform": "twitch",
        "metric": "followers",
        "value": 10,
        "as_of_utc": "2026-11-08T09:00:00Z",
        "source": "api",
        "run_id": "run-1",
    }
    fields.update(over)
    return Record(**fields)


# ---- F-40: the append does not hold the file (or a copy of it) --------------


def _big_history(path: Path, rows: int) -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    records = [
        rec(
            platform=f"p{i % 40}",
            metric=f"m{i // 40}",
            as_of_utc=(start + timedelta(days=i // 1000)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            value=i,
        )
        for i in range(rows)
    ]
    path.write_text(dump_new_history(records, now=NOW), encoding="ascii")
    path.chmod(0o600)


def test_an_append_streams_the_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    _big_history(path, 3000)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the whole file was read into memory")

    monkeypatch.setattr(hist, "_read_fd", refuse)
    monkeypatch.setattr(hist, "_parse", refuse)
    kept = append_records(
        path, [rec(platform="new", as_of_utc="2026-11-09T09:00:00Z")], now=NOW
    )
    assert kept == 1
    monkeypatch.undo()
    assert len(read_history(path).records) == 3001


def test_a_streamed_append_still_refuses_a_damaged_file(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    _big_history(path, 50)
    good = path.read_bytes()
    new = rec(platform="new", as_of_utc="2026-11-09T09:00:00Z")
    path.write_bytes(good[:-1])  # the last newline is missing
    with pytest.raises(HistoryError, match="truncated"):
        append_records(path, [new], now=NOW)
    path.write_bytes(good + b"x" * (hist.MAX_LINE_BYTES + 10))  # no newline, huge
    with pytest.raises(HistoryError):
        append_records(path, [new], now=NOW)
    rows = good.splitlines(keepends=True)
    assert b'"value":2' in rows[2]
    rows[2] = rows[2].replace(b'"value":2', b'"value":9', 1)
    path.write_bytes(b"".join(rows))  # one line changed: the chain is broken
    with pytest.raises(HistoryError, match="hash chain"):
        append_records(path, [new], now=NOW)
    path.write_bytes(good)
    assert append_records(path, [new], now=NOW) == 1


def test_a_streamed_append_still_refuses_a_second_record_for_the_day(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    append_records(path, [rec()], now=NOW)
    before = path.read_bytes()
    other = rec(as_of_utc="2026-11-08T10:00:00Z")
    with pytest.raises(HistoryError, match="second record"):
        append_records(path, [other], now=NOW)
    assert path.read_bytes() == before
    assert append_records(path, [other], now=NOW, skip_days=True) == 0


def test_an_append_that_would_pass_the_size_cap_writes_nothing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "h.jsonl"
    append_records(path, [rec()], now=NOW)
    before = path.read_bytes()
    with pytest.raises(HistoryError, match="too large"):
        append_records(
            path,
            [rec(as_of_utc="2026-11-09T09:00:00Z")],
            now=NOW,
            max_bytes=len(before) + 10,
        )
    assert path.read_bytes() == before


# ---- F-47: a retry after a partly failed write keeps both sides in step -----

CONFIG = """\
version: 1
creator: {name: Test Creator}
tabs:
  - id: home
    title: Home
    blocks:
      - id: socials
        items:
          - {platform: bluesky, handle: "@example", url: "https://example.com/b"}
          - {platform: kick, label: subscribers, handle: "@e", url: "https://example.com/k"}
numbers:
  history_file: history/social.jsonl
  sources:
    - {platform: bluesky, kind: bluesky, actor: example.bsky.social}
"""
BSKY = "https://public.api.bsky.app/"
DAY1 = datetime(2026, 11, 20, 9, 30, tzinfo=UTC)
DAY1_LATER = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "site.yaml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "history").mkdir()
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    return tmp_path


def serve(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    monkeypatch.setattr(
        counts_cmd,
        "default_transport",
        lambda: FakeTransport({BSKY: reply({"followersCount": count})}),
    )


def run(root: Path, when: datetime) -> int:
    return counts_cmd.run_refresh(
        root / "site.yaml", None, None, False, env={}, now=lambda: when
    )


def fail_numbers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    real = write_atomic
    armed = [True]

    def write(path: Path, text: str, **kwargs: Any) -> None:
        if armed[0] and path.name == "numbers.json":
            armed[0] = False
            raise OSError(28, "No space left on device")
        real(path, text, **kwargs)

    monkeypatch.setattr(counts_cmd, "write_atomic", write)


def current(root: Path, key: str = "bluesky") -> int:
    data = json.loads((root / "numbers.json").read_text("utf-8"))
    return int(data["platforms"][key]["count"])


def points(root: Path, platform: str, metric: str) -> list[Any]:
    series = read_history(root / "history" / "social.jsonl").series(platform, metric)
    return series.points()


def test_a_retry_after_a_failed_numbers_write_leaves_history_and_numbers_equal(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 100)
    fail_numbers_once(monkeypatch)
    assert run(setup, DAY1) == counts_cmd.EXIT_WRITE
    assert not (setup / "numbers.json").exists()
    assert pending_path(setup / "numbers.json").exists()
    assert [p.value for p in points(setup, "bluesky", "followers")] == [100]
    serve(monkeypatch, 105)  # the count moved before the retry, same UTC day
    assert run(setup, DAY1_LATER) == 0
    assert current(setup) == 105
    got = points(setup, "bluesky", "followers")
    assert [p.value for p in got] == [105]  # corrected, not left at 100
    assert got[0].corrected is True
    assert not pending_path(setup / "numbers.json").exists()


def test_a_clean_run_leaves_no_note_and_a_same_day_rerun_keeps_the_first(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 100)
    assert run(setup, DAY1) == 0
    assert not pending_path(setup / "numbers.json").exists()
    serve(monkeypatch, 105)
    assert run(setup, DAY1_LATER) == 0
    assert [p.value for p in points(setup, "bluesky", "followers")] == [100]
    assert current(setup) == 105  # unchanged behavior for a committed first run


def test_two_failures_in_a_row_still_end_in_step(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 100)
    fail_numbers_once(monkeypatch)
    assert run(setup, DAY1) == counts_cmd.EXIT_WRITE
    serve(monkeypatch, 105)
    fail_numbers_once(monkeypatch)
    assert run(setup, DAY1_LATER) == counts_cmd.EXIT_WRITE
    serve(monkeypatch, 111)
    assert run(setup, DAY1_LATER + timedelta(hours=1)) == 0
    assert current(setup) == 111
    assert [p.value for p in points(setup, "bluesky", "followers")] == [111]
    assert not pending_path(setup / "numbers.json").exists()


def test_the_pending_note_holds_run_ids_only_and_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / ".numbers.json.pending"
    ids: list[str] = []
    for n in range(MAX_IDS):
        mark_pending(path, ids, f"refresh-{n}")
        ids = read_pending(path)
    assert ids == [f"refresh-{n}" for n in range(MAX_IDS)]
    before = path.read_bytes()
    with pytest.raises(PendingError):  # an id is never dropped to make room
        mark_pending(path, ids, "refresh-over")
    assert path.read_bytes() == before
    path.write_text("good-1\n../etc/passwd\nbad id\n" + "x" * 5000, encoding="utf-8")
    with pytest.raises(PendingError):  # over the size cap: not trusted, not absent
        read_pending(path)
    path.write_text("good-1\n../etc/passwd\nbad id\n", encoding="utf-8")
    with pytest.raises(PendingError):  # an invalid line is not skipped
        read_pending(path)
    path.write_text("good-1\ngood-2\n", encoding="utf-8")
    assert read_pending(path) == ["good-1", "good-2"]
    path.unlink()
    assert read_pending(path) == []  # only an absent note means nothing pending


# ---- an unusable pending note stops the run; it is never treated as absent ---


def _stop_after_history_write(setup: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    serve(monkeypatch, 100)
    fail_numbers_once(monkeypatch)
    assert run(setup, DAY1) == counts_cmd.EXIT_WRITE
    assert [p.value for p in points(setup, "bluesky", "followers")] == [100]


@pytest.mark.parametrize(
    "damage", ["oversize", "garbage", "binary", "empty", "directory", "symlink"]
)
def test_an_unusable_pending_note_stops_the_run_and_is_kept(
    setup: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    damage: str,
) -> None:
    _stop_after_history_write(setup, monkeypatch)
    note = pending_path(setup / "numbers.json")
    secret = "ZZ-PRIVATE-ZZ"  # noqa: S105
    if damage == "oversize":
        note.write_text(f"{secret}\n" + "x" * 5000, encoding="utf-8")
    elif damage == "garbage":
        note.write_text(f"{secret} not an id\n", encoding="utf-8")
    elif damage == "binary":
        note.write_bytes(b"\xff\xfe" + secret.encode())
    elif damage == "empty":
        note.write_bytes(b"")
    elif damage == "directory":
        note.unlink()
        note.mkdir()
    else:
        note.unlink()
        note.symlink_to(setup / "site.yaml")
    before = (setup / "history" / "social.jsonl").read_bytes()
    capsys.readouterr()
    serve(monkeypatch, 105)
    assert run(setup, DAY1_LATER) == counts_cmd.EXIT_WRITE
    err = capsys.readouterr().err
    assert "pending note" in err and secret not in err
    assert not (setup / "numbers.json").exists()  # nothing else was changed
    assert (setup / "history" / "social.jsonl").read_bytes() == before
    assert note.is_symlink() or note.exists()  # kept, never deleted or rewritten
    if damage in ("oversize", "garbage", "binary"):
        assert secret.encode() in note.read_bytes()
    # A person clears it; the next run then ends in step.
    if note.is_dir():
        note.rmdir()
    else:
        note.unlink()
    assert run(setup, DAY1_LATER) == 0
    assert current(setup) == 105


def test_an_import_with_an_unusable_pending_note_stops_the_same_way(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv = setup / "in.csv"
    csv.write_text(
        "platform,metric,value,as_of\nkick,subscribers,41,2026-11-20\n",
        encoding="utf-8",
    )
    pending_path(setup / "numbers.json").write_text("not an id\n", encoding="utf-8")
    day = date(2026, 11, 20)
    assert (
        counts_cmd.run_import(setup / "site.yaml", csv, None, False, today=day)
        == counts_cmd.EXIT_WRITE
    )
    assert not (setup / "numbers.json").exists()
    assert not (setup / "history" / "social.jsonl").exists()


# ---- a committed run is never replayed because its note was not removed -----


def test_a_note_left_after_a_committed_run_does_not_rewrite_history(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 100)

    def crash(paths: Any) -> None:  # the process dies after the numbers write
        raise KeyboardInterrupt

    monkeypatch.setattr(counts_cmd, "_settle_history", crash)
    with pytest.raises(KeyboardInterrupt):
        run(setup, DAY1)
    assert current(setup) == 100
    assert pending_path(setup / "numbers.json").exists()  # stale, but committed
    monkeypatch.undo()
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    serve(monkeypatch, 105)
    assert run(setup, DAY1_LATER) == 0
    assert [p.value for p in points(setup, "bluesky", "followers")] == [100]
    assert current(setup) == 105
    assert not pending_path(setup / "numbers.json").exists()


def test_a_committed_run_whose_numbers_file_is_byte_identical_is_not_replayed(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 100)
    assert run(setup, DAY1) == 0
    before = (setup / "numbers.json").read_bytes()
    (setup / "history" / "social.jsonl").unlink()  # this run will record the day

    def crash(paths: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(counts_cmd, "_settle_history", crash)
    with pytest.raises(KeyboardInterrupt):
        run(setup, DAY1)  # same count, same stamp: the rewrite changes no byte
    assert (setup / "numbers.json").read_bytes() == before
    assert pending_path(setup / "numbers.json").exists()
    monkeypatch.undo()
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    serve(monkeypatch, 105)
    assert run(setup, DAY1_LATER) == 0
    assert [p.value for p in points(setup, "bluesky", "followers")] == [100]
    assert current(setup) == 105


def test_a_checkout_between_a_failed_run_and_its_retry_is_not_a_commit(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 90)
    assert run(setup, DAY1 - timedelta(days=1)) == 0
    numbers = setup / "numbers.json"
    old = numbers.read_bytes()
    serve(monkeypatch, 100)
    fail_numbers_once(monkeypatch)
    assert run(setup, DAY1) == counts_cmd.EXIT_WRITE
    # `git checkout -- numbers.json`: the old content, as a new file
    copy = setup / "restore.tmp"
    copy.write_bytes(old)
    os.replace(copy, numbers)
    assert numbers.read_bytes() == old
    serve(monkeypatch, 105)
    assert run(setup, DAY1_LATER) == 0
    assert current(setup) == 105
    assert [p.value for p in points(setup, "bluesky", "followers")][-1] == 105


def test_the_pending_note_never_drops_an_id_to_make_room(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    note = pending_path(setup / "numbers.json")
    serve(monkeypatch, 100)
    ids = [f"refresh-{n}" for n in range(MAX_IDS)]
    note.write_text("\n".join(ids) + "\n", encoding="utf-8")
    before = note.read_bytes()
    assert run(setup, DAY1) == counts_cmd.EXIT_WRITE  # a 17th id: stop
    assert "pending note" in capsys.readouterr().err
    assert note.read_bytes() == before
    assert not (setup / "numbers.json").exists()
    assert not (setup / "history" / "social.jsonl").exists()
    note.write_text("\n".join(ids[:-1]) + "\n", encoding="utf-8")  # 15 + this run
    assert run(setup, DAY1) == 0
    assert current(setup) == 100


def test_a_note_that_cannot_be_removed_does_not_rewrite_committed_history(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serve(monkeypatch, 100)

    def refuse(path: Path) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(counts_cmd, "clear_pending", refuse)
    assert run(setup, DAY1) == 0
    assert pending_path(setup / "numbers.json").exists()
    monkeypatch.undo()
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)
    serve(monkeypatch, 105)
    assert run(setup, DAY1_LATER) == 0
    assert [p.value for p in points(setup, "bluesky", "followers")] == [100]
    assert current(setup) == 105


# ---- the held history file may not change size during an append -------------


def test_a_history_file_truncated_during_the_scan_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    _big_history(path, 50)
    good = path.read_bytes()
    first_line = good.index(b"\n") + 1
    real = hist.os.pread
    armed = [True]

    def pread(fd: int, length: int, offset: int) -> bytes:
        if armed[0]:
            armed[0] = False
            os.truncate(path, first_line)  # same inode, cut at a newline
        return real(fd, length, offset)

    monkeypatch.setattr(hist.os, "pread", pread)
    new = rec(platform="new", as_of_utc="2026-11-09T09:00:00Z")
    with pytest.raises(HistoryError, match="changed while it was read"):
        append_records(path, [new], now=NOW)
    assert path.stat().st_size == first_line  # nothing was appended


def test_a_history_file_that_grows_before_the_write_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    _big_history(path, 50)
    real = hist._same_file
    calls = [0]

    def same(dir_fd: int, name: str, fd: int) -> bool:
        calls[0] += 1
        if calls[0] == 3:  # the check just before the write
            with open(path, "ab") as grow:
                grow.write(b"{}\n")
        return real(dir_fd, name, fd)

    monkeypatch.setattr(hist, "_same_file", same)
    size = path.stat().st_size
    new = rec(platform="new", as_of_utc="2026-11-09T09:00:00Z")
    with pytest.raises(HistoryError, match="changed while it was read"):
        append_records(path, [new], now=NOW)
    assert path.stat().st_size == size + 3  # only the foreign bytes are there


def test_an_import_retry_after_a_failed_numbers_write_corrects_the_history(
    setup: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 11, 20)
    csv = setup / "in.csv"
    csv.write_text(
        "platform,metric,value,as_of\nkick,subscribers,41,2026-11-20\n",
        encoding="utf-8",
    )
    fail_numbers_once(monkeypatch)
    assert (
        counts_cmd.run_import(setup / "site.yaml", csv, None, False, today=day)
        == counts_cmd.EXIT_WRITE
    )
    assert [p.value for p in points(setup, "kick", "subscribers")] == [41]
    csv.write_text(
        "platform,metric,value,as_of\nkick,subscribers,47,2026-11-20\n",
        encoding="utf-8",
    )
    assert counts_cmd.run_import(setup / "site.yaml", csv, None, False, today=day) == 0
    assert current(setup, "kick") == 47
    assert [p.value for p in points(setup, "kick", "subscribers")] == [47]
    assert not pending_path(setup / "numbers.json").exists()


# ---- F-77: a retention policy built by hand is checked at use ---------------

TODAY = date(2026, 11, 20)


def old(days_ago: int) -> Record:
    when = TODAY - timedelta(days=days_ago)
    return rec(platform="twitch", as_of_utc=f"{when.isoformat()}T00:00:00Z")


def policy(window: Any, warn: Any) -> RetentionPolicy:
    return RetentionPolicy({"twitch": window}, warn)


@pytest.mark.parametrize(
    ("window", "warn"),
    [
        (30, -10),
        (30, 0),
        (-5, 7),
        (0, 7),
        (7, 7),
        (3651, 7),
        (True, 7),
        (30, True),
        (30.0, 7),
        (30, 366),
        ("30", 7),
    ],
)
def test_a_bad_policy_is_refused_with_a_fixed_message(window: Any, warn: Any) -> None:
    with pytest.raises(ValueError, match="retention policy"):
        retention_warnings([old(25)], policy(window, warn), TODAY)


def test_a_bad_policy_does_not_stop_a_refresh(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    serve(monkeypatch, 100)
    bad = policy(30, -10)
    code = counts_cmd.run_refresh(
        setup / "site.yaml", None, None, False, env={}, now=lambda: DAY1
    )
    assert code == 0
    counts_cmd._retention_checks(setup / "history" / "social.jsonl", DAY1, bad)
    assert "retention check was skipped" in capsys.readouterr().err


def test_a_good_policy_still_warns() -> None:
    out = retention_warnings([old(25)], policy(30, 7), TODAY)
    assert [(w.platform, w.expiring) for w in out] == [("twitch", 1)]


# ---- a change in the gap between the last size check and the write ----------


@pytest.mark.parametrize("change", ["truncate", "grow"])
def test_a_change_between_the_last_check_and_the_write_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    path = tmp_path / "h.jsonl"
    _big_history(path, 50)

    def foreign(stage: str) -> None:
        if change == "truncate":
            os.truncate(path, 0)
        else:
            with open(path, "ab") as stream:
                stream.write(b"{}\n")

    monkeypatch.setattr(hist, "_before_mutation", foreign)
    new = rec(platform="new", as_of_utc="2026-11-09T09:00:00Z")
    with pytest.raises(HistoryError, match="changed while it was written"):
        append_records(path, [new], now=NOW)
