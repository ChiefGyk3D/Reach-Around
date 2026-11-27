# SPDX-License-Identifier: Apache-2.0
"""Crash recovery of the fediverse refresh (#940, Daybreak round 2).

A run that read a snapshot has one commit point, the rename of the staged
snapshot (design and crash table: ``reach_around.counts.fediverse_commit``).
Every test here either injects a crash (``Crash`` is a ``BaseException``, so no
cleanup of the interrupted run executes, as when a process dies) at one step
and then runs again, or builds a state a crash leaves and runs again.

Hermetic: the fake transport stands in for the network, every name is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import reach_around.commands.counts as counts_cmd
import reach_around.counts.files as files_mod
import reach_around.counts.history as history_mod
from fakenet import FakeTransport, reply
from reach_around.counts.fediverse_commit import (
    UnitNote,
    day_conflicts,
    read_unit,
    snapshot_rows,
    status_committed,
    sweep_stale,
    unit_path,
    unit_pending,
    write_unit,
)
from reach_around.counts.fediverse_store import load_snapshots, snapshot_path
from reach_around.counts.files import plan_paths, write_atomic
from reach_around.counts.history import HistoryError, read_history
from test_fediverse_wiring import (
    CONFIG,
    DID,
    FEED,
    NOW,
    PROFILE,
    bsky_post,
    committed,
    frozen_clock,
    tree,
    write_config,
)

# The history clock is frozen by the shared ``frozen_clock`` fixture (fixed day
# after every scenario), never the machine's date. Day one of every scenario is
# three days before the shared test clock.
SHARED_FIXTURES = (frozen_clock,)  # imported so that pytest finds it in this module
pytestmark = pytest.mark.usefixtures("frozen_clock")
BASE = NOW - timedelta(days=3)
DAY_TWO = (BASE + timedelta(days=1)).strftime("%Y-%m-%d")


def run_at(root: Path, days: int, likes: int) -> int:
    """A run ``days`` UTC days after day one, with ``likes`` on the one post. The
    Mastodon source is down (503), so a finished run exits 3; the follower count
    follows ``likes`` so that the numbers file changes from run to run."""
    moment = BASE + timedelta(days=days)
    header = {"date": moment.strftime("%a, %d %b %Y %H:%M:%S GMT")}
    page = {"feed": [bsky_post(0, moment - timedelta(days=1), likes)]}
    profile = {"did": DID, "followersCount": 300 + likes, "postsCount": 77}
    transport = FakeTransport(
        {
            PROFILE: reply(profile, headers=header),
            FEED: reply(page, headers=header),
            "https://social.example/": reply({"error": "down"}, status=503),
        }
    )
    return counts_cmd.run_refresh(
        root / "site.yaml",
        None,
        None,
        False,
        env={},
        transport=transport,
        now=lambda: moment,
    )


def day_one(root: Path) -> int:
    return run_at(root, 0, 9)


def second_day(root: Path, likes: int, days: int = 1) -> int:
    return run_at(root, days, likes)


# The ordinary Bluesky follower source next to the fediverse block: it makes the
# numbers file change from run to run, so a half commit is visible.
CONFIG_BOTH = CONFIG.replace(
    "numbers:\n",
    "numbers:\n  sources:\n"
    "    - {platform: bluesky, kind: bluesky, actor: alice.example.com}\n",
).replace(
    '          - {platform: github, handle: "@example", url: "https://example.com/github"}\n',
    '          - {platform: github, handle: "@example", url: "https://example.com/github"}\n'
    '          - {platform: bluesky, handle: "@example", url: "https://example.com/bsky"}\n',
)


class Crash(BaseException):
    """The process died here: nothing after this step runs, nothing is cleaned up."""


STEPS = (
    "stage_write",  # while the staged snapshot is being written
    "staged",  # the staged snapshot is complete, nothing else happened
    "ordinary_rows_before",  # the pending note is written, no row yet
    "ordinary_rows_after",  # the ordinary rows landed, status still the old one
    "status_false",  # before the status says committed: false
    "rename_before",  # before the commit point
    "rename_after",  # right after the commit point
    "fed_rows_before",  # committed, the fediverse rows not yet written
    "fed_rows_after",  # the fediverse rows landed, numbers file still old
    "numbers_before",
    "numbers_after",
    "settle_before",  # numbers landed, the pending note not yet settled
    "status_true",  # everything landed, the closing status not yet written
)


def inject(m: pytest.MonkeyPatch, step: str) -> None:
    """Make the run die at ``step`` (see ``STEPS``)."""
    real_stage, real_append = files_mod.stage_file, history_mod.append_records
    real_replace = os.replace
    status_writes = [0]

    def write(path: Path, text: str, **kwargs: Any) -> None:
        name = path.name
        if step == "stage_write" and name.endswith(".staged"):
            raise Crash
        if name == "numbers.status.json":
            status_writes[0] += 1
            if (step, status_writes[0]) in (("status_false", 1), ("status_true", 2)):
                raise Crash
        if name == "numbers.json" and step == "numbers_before":
            raise Crash
        write_atomic(path, text, **kwargs)
        if name == "numbers.json" and step == "numbers_after":
            raise Crash

    def stage(*args: Any, **kwargs: Any) -> Any:
        real_stage(*args, **kwargs)
        raise Crash

    def append(path: Path, rows: Any, **kwargs: Any) -> int:
        ordinary = rows[0].metric == "followers"
        fed_new = not ordinary and rows[0].as_of_utc.startswith(DAY_TWO)
        if (step, ordinary) == ("ordinary_rows_before", True):
            raise Crash
        if (step, fed_new) == ("fed_rows_before", True):
            raise Crash
        done = real_append(path, rows, **kwargs)
        if (step, ordinary) == ("ordinary_rows_after", True):
            raise Crash
        if (step, fed_new) == ("fed_rows_after", True):
            raise Crash
        return done

    def replace(src: str, dst: str, **kwargs: Any) -> None:
        target = dst == "numbers.fediverse.json" and src.endswith(".staged")
        if target and step == "rename_before":
            raise Crash
        real_replace(src, dst, **kwargs)
        if target and step == "rename_after":
            raise Crash

    def settle(*args: Any, **kwargs: Any) -> None:
        raise Crash

    m.setattr(counts_cmd, "write_atomic", write)
    m.setattr(counts_cmd, "stage_file", stage if step == "staged" else real_stage)
    m.setattr(counts_cmd, "append_records", append)
    m.setattr("reach_around.counts.files.os.replace", replace)
    if step == "settle_before":
        m.setattr(counts_cmd, "_settle_history", settle)


def make(root: Path, config: str = CONFIG_BOTH) -> Path:
    root.mkdir()
    write_config(root, config)
    return root


def state(root: Path) -> dict[str, object]:
    """Everything a reader sees: files by name (history by meaning, run ids and
    line order aside), so an interrupted-then-repaired run can be compared with
    an uninterrupted one."""
    files = tree(root)
    history = files.pop("history/social.jsonl", b"")
    rows = sorted(
        (r.platform, r.metric, r.as_of_utc[:10], r.value, r.type)
        for r in read_history(root / "history" / "social.jsonl").records
    )
    return {"files": files, "rows": rows, "history_bytes_present": bool(history)}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(counts_cmd, "default_sleep", lambda seconds: None)


@pytest.mark.parametrize("step", STEPS)
def test_a_crash_at_every_step_is_repaired_by_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    oracle, real = make(tmp_path / "oracle"), make(tmp_path / "real")
    for root in (oracle, real):
        assert day_one(root) == 3  # day one, uninterrupted
    assert second_day(oracle, likes=77) == 3  # the uninterrupted day two
    with monkeypatch.context() as m:
        inject(m, step)
        with pytest.raises(Crash):
            second_day(real, likes=77)
    assert second_day(real, likes=77) == 3  # the next run, no injection
    after, expected = state(real), state(oracle)
    assert after == expected  # snapshot, numbers, status, rows: the same state
    names = set(tree(real))
    assert not [
        n for n in names if n.endswith((".staged", ".tmp", ".pending", ".unit"))
    ]
    rows = after["rows"]
    assert isinstance(rows, list) and len(rows) == len(set(rows))  # none doubled
    assert committed(real) is True


def test_a_rename_failure_never_commits_new_numbers_without_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak M1: the numbers file changes with every run here (the ordinary
    source), so a numbers write ahead of the rename shows as a half commit."""
    root = make(tmp_path / "root")
    assert day_one(root) == 3
    snap, numbers = root / "numbers.fediverse.json", root / "numbers.json"
    snap_before, numbers_before = snap.read_bytes(), numbers.read_bytes()
    real_replace = os.replace

    def replace(src: str, dst: str, **kwargs: Any) -> None:
        if dst == snap.name and src.endswith(".staged"):
            raise OSError(5, "I/O error")
        real_replace(src, dst, **kwargs)

    with monkeypatch.context() as m:
        m.setattr("reach_around.counts.files.os.replace", replace)
        assert second_day(root, likes=77) == 1
    assert snap.read_bytes() == snap_before
    assert numbers.read_bytes() == numbers_before  # one generation, not two
    assert not [n for n in tree(root) if n.endswith((".staged", ".tmp"))]
    assert committed(root) is False
    assert second_day(root, likes=77) == 3  # and the next run settles it
    assert committed(root) is True


def test_the_next_run_says_false_when_the_status_lags_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak M2: history changed, the status still says the old run committed.
    The repair step (first thing in a run) puts the truth in the status."""
    root = make(tmp_path / "root")
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "ordinary_rows_after")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert committed(root) is True  # the lag the crash left
    paths = plan_paths(root / "site.yaml", None)
    stamp = BASE + timedelta(days=1)
    history = root / "history" / "social.jsonl"
    assert (
        counts_cmd._recover(paths, history, snapshot_path(paths.target), stamp, "r1")
        is None
    )
    assert committed(root) is False  # a live note proves the run did not finish


def test_a_leftover_staged_file_makes_the_status_false_and_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make(tmp_path / "root")
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "staged")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert [n for n in tree(root) if n.endswith(".staged")]
    paths = plan_paths(root / "site.yaml", None)
    history = root / "history" / "social.jsonl"
    stamp = BASE + timedelta(days=1)
    snap = snapshot_path(paths.target)
    before = snap.read_bytes()
    assert counts_cmd._recover(paths, history, snap, stamp, "r1") is None
    assert not [n for n in tree(root) if n.endswith(".staged")]
    assert snap.read_bytes() == before  # a committed snapshot is never touched
    assert committed(root) is False


# --- the same-day rule (Daybreak M3) --------------------------------------------


def test_a_same_day_run_with_other_values_keeps_snapshot_and_history_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    snap, history = root / "numbers.fediverse.json", root / "history" / "social.jsonl"
    snap_before, history_before = snap.read_bytes(), history.read_bytes()
    capsys.readouterr()
    assert second_day(root, likes=77, days=0) == 3  # the Mastodon source is down
    shown = capsys.readouterr()
    assert "fediverse-bluesky: skipped (same_day_kept)" in shown.out
    assert snap.read_bytes() == snap_before  # the new snapshot is NOT committed
    assert history.read_bytes() == history_before  # history is never rewritten
    status = json.loads((root / "numbers.status.json").read_text())
    assert status["sources"]["fediverse-bluesky"]["status"] == "skipped"
    assert status["sources"]["fediverse-bluesky"]["detail"] == "same_day_kept"
    assert committed(root) is True  # nothing was left half done
    # the next UTC day commits the new values, and each day's row matches the
    # snapshot committed that day
    assert second_day(root, likes=77) == 3
    rows = read_history(history).records
    assert sorted(r.value for r in rows if r.metric == "favorites_30d") == [9, 77]


def test_a_same_day_run_with_the_same_values_commits(tmp_path: Path) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    assert second_day(root, likes=9, days=0) == 3
    rows = read_history(root / "history" / "social.jsonl").records
    assert len([r for r in rows if r.metric == "favorites_30d"]) == 1
    assert committed(root) is True


def test_an_aborted_run_leaves_no_row_so_its_values_never_reach_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "rename_before")  # the run read 77 and died before its commit
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert second_day(root, likes=55) == 3  # a later run the same day reads 55
    snap = load_snapshots(root / "numbers.fediverse.json")
    assert snap["bluesky"]["interactions"] == [
        {"boosts": 1, "favorites": 55, "replies": 2}
    ]
    rows = read_history(root / "history" / "social.jsonl").records
    assert sorted(r.value for r in rows if r.metric == "favorites_30d") == [9, 55]


def test_the_rows_are_derived_from_the_snapshot_and_conflicts_are_named(
    tmp_path: Path,
) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    snapshots = load_snapshots(root / "numbers.fediverse.json")
    rows = snapshot_rows(snapshots, "r1", BASE.date())
    assert {(r.platform, r.metric, r.value) for r in rows} == {
        ("bluesky", "posts_30d", 1),
        ("bluesky", "boosts_30d", 1),
        ("bluesky", "favorites_30d", 9),
        ("bluesky", "replies_30d", 2),
    }
    assert all(r.as_of_utc == f"{BASE:%Y-%m-%d}T00:00:00Z" for r in rows)
    # Daybreak round 3 M2: the age of a snapshot never stops its rows (the old
    # one-day limit is gone); only a snapshot dated after today is left out.
    assert snapshot_rows(snapshots, "r1", BASE.date() + timedelta(days=3)) == rows
    assert snapshot_rows(snapshots, "r1", BASE.date() - timedelta(days=1)) == []
    history = read_history(root / "history" / "social.jsonl")
    assert day_conflicts(history, rows) == set()
    changed = [r for r in rows if r.metric != "favorites_30d"] + [
        type(r)(**{**r.__dict__, "value": 12})
        for r in rows
        if r.metric == "favorites_30d"
    ]
    assert day_conflicts(history, changed) == {"bluesky"}


# --- stale staged files (Daybreak L1) -------------------------------------------


def test_stale_staged_and_temp_files_are_removed_and_nothing_else(
    tmp_path: Path,
) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    snap = root / "numbers.fediverse.json"
    stale = [
        root / ".numbers.fediverse.json.0123456789abcdef.staged",
        root / ".numbers.fediverse.json.0123456789abcdef.tmp",
        root / "..numbers.fediverse.json.0123456789abcdef.staged.fedcba9876543210.tmp",
    ]
    for item in stale:
        item.write_text("{}")
    keep = [
        root / ".other.json.0123456789abcdef.staged",  # not this snapshot's
        root / ".numbers.fediverse.json.zzzz.staged",  # not a name we make
        root / "numbers.fediverse.json.staged",
    ]
    for item in keep:
        item.write_text("x")
    folder = root / ".numbers.fediverse.json.aaaaaaaaaaaaaaaa.staged"
    folder.mkdir()  # a folder with a matching name is not removed
    link = root / ".numbers.fediverse.json.bbbbbbbbbbbbbbbb.staged"
    link.symlink_to(keep[0])  # nor a link, and its target stays
    before = snap.read_bytes()
    assert sweep_stale(snap) == 3
    assert not any(item.exists() for item in stale)
    assert all(item.exists() for item in keep) and folder.is_dir()
    assert link.is_symlink() and keep[0].read_text() == "x"
    assert snap.read_bytes() == before


def test_repeated_crashes_do_not_pile_up_staged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    for _ in range(3):
        with monkeypatch.context() as m:
            inject(m, "staged")
            with pytest.raises(Crash):
                second_day(root, likes=77)
    # each run reclaims the leftover of the one before: never more than one stays
    assert len([n for n in tree(root) if n.endswith(".staged")]) == 1
    assert second_day(root, likes=77) == 3  # and any later run reclaims that one
    assert not [n for n in tree(root) if n.endswith((".staged", ".tmp"))]
    assert committed(root) is True


def test_a_status_with_a_note_still_live_is_not_blessed_by_a_run_that_changed_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak M1 step 6: an unfinished attempt must not turn ``true`` just
    because a later run found nothing new to write."""
    root = make(tmp_path / "root")
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "ordinary_rows_after")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    transport = FakeTransport(
        {
            "https://public.api.bsky.app/": reply({"error": "down"}, status=503),
            "https://social.example/": reply({"error": "down"}, status=503),
        }
    )
    code = counts_cmd.run_refresh(
        root / "site.yaml",
        None,
        None,
        False,
        env={},
        transport=transport,
        now=lambda: BASE + timedelta(days=1),
    )
    assert code != 0
    assert status_committed(root / "numbers.status.json") is False


# --- Daybreak round 3 ------------------------------------------------------------

CONFIG_NO_HISTORY = CONFIG_BOTH.replace("  history_file: history/social.jsonl\n", "")


def run_failing(root: Path, days: int) -> int:
    """A run ``days`` UTC days after day one in which every source is down."""
    moment = BASE + timedelta(days=days)
    transport = FakeTransport(
        {
            "https://public.api.bsky.app/": reply({"error": "down"}, status=503),
            "https://social.example/": reply({"error": "down"}, status=503),
        }
    )
    return counts_cmd.run_refresh(
        root / "site.yaml",
        None,
        None,
        False,
        env={},
        transport=transport,
        now=lambda: moment,
    )


def force_status(root: Path, value: bool) -> None:
    """Put the lag a crash can leave into the status file: a claim of ``value``."""
    path = root / "numbers.status.json"
    data = json.loads(path.read_text())
    data["committed"] = value
    path.write_text(json.dumps(data))


def favorites(root: Path) -> list[int]:
    rows = read_history(root / "history" / "social.jsonl").records
    return sorted(r.value for r in rows if r.metric == "favorites_30d")


def test_a_crash_inside_recovery_cannot_keep_a_stale_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak round 3 M1: recovery wrote the status after it deleted the only
    evidence. Here the process dies right after the evidence is removed."""
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "staged")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert committed(root) is True  # the lag the crash left
    assert [n for n in tree(root) if n.endswith(".staged")]

    def die(found: object) -> int:
        raise Crash

    with monkeypatch.context() as m:
        m.setattr(counts_cmd, "remove_stale", die)
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert committed(root) is False  # said before the first removal
    assert run_failing(root, 1) != 0  # a later run with every source down
    assert not [n for n in tree(root) if n.endswith((".staged", ".tmp"))]
    assert second_day(root, likes=77) == 3
    assert committed(root) is True


def test_recovery_recomputes_the_status_from_the_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A status that says true while a row of the committed snapshot is missing is
    corrected by recovery, never preserved."""
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "rename_after")  # committed, the fediverse rows not written
        with pytest.raises(Crash):
            second_day(root, likes=77)
    force_status(root, True)  # a stale true
    assert favorites(root) == [9]
    paths = plan_paths(root / "site.yaml", None)
    history = root / "history" / "social.jsonl"
    stamp = BASE + timedelta(days=1)
    snap = snapshot_path(paths.target)
    assert counts_cmd._recover(paths, history, snap, stamp, "r1") is None
    assert favorites(root) == [9, 77]  # the rows are repaired
    assert committed(root) is False  # and recovery itself never says true


# --- history recovery has no age limit (Daybreak round 3 M2) ----------------------


def test_a_crash_on_day_n_is_repaired_by_a_run_on_day_n_plus_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "rename_after")  # day two committed, no row yet
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert favorites(root) == [9]
    assert run_at(root, 3, likes=55) == 3  # nothing ran on day three of the crash
    assert favorites(root) == [9, 55, 77]  # day two's rows were not lost
    days = {
        r.as_of_utc[:10]
        for r in read_history(root / "history" / "social.jsonl").records
        if r.metric == "favorites_30d"
    }
    assert days == {(BASE + timedelta(days=n)).strftime("%Y-%m-%d") for n in (0, 1, 3)}
    assert committed(root) is True


def test_a_snapshot_dated_after_today_is_reported_and_never_blessed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The clock went backward: the rows cannot be appended yet (history rejects a
    future time). The run says so with a fixed word and the status stays false."""
    root = make(tmp_path / "root", CONFIG)
    assert day_one(root) == 3
    capsys.readouterr()
    assert run_failing(root, -2) != 0
    shown = capsys.readouterr()
    assert "snapshot_in_future" in shown.err
    assert committed(root) is False
    assert run_at(root, 1, likes=77) == 3  # the day arrives: nothing is lost
    assert committed(root) is True


# --- a failed retry never blesses an incomplete unit (Daybreak round 3 M3) --------


def test_a_failed_retry_after_a_lost_numbers_write_stays_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No history file: the snapshot is the only commit marker. The process dies
    after the rename and before the numbers file; the retry has every source down."""
    root = make(tmp_path / "root", CONFIG_NO_HISTORY)
    assert day_one(root) == 3
    numbers_before = (root / "numbers.json").read_bytes()
    with monkeypatch.context() as m:
        inject(m, "numbers_before")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert unit_path(root / "numbers.json").exists()  # the trace of the unit
    assert run_failing(root, 1) != 0
    assert (root / "numbers.json").read_bytes() == numbers_before
    assert committed(root) is False  # the numbers of that run never landed
    assert second_day(root, likes=77) == 3  # a run that writes the numbers file
    assert committed(root) is True
    assert not unit_path(root / "numbers.json").exists()


def test_a_recovery_that_fails_after_the_rename_never_leaves_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make(tmp_path / "root")
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "rename_after")  # committed; rows and numbers not written
        with pytest.raises(Crash):
            second_day(root, likes=77)
    real = history_mod.append_records

    def refuse(path: Path, rows: Any, **kwargs: Any) -> int:
        if rows[0].metric != "followers":
            raise HistoryError("disk")  # the derived rows fail in recovery
        return real(path, rows, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(counts_cmd, "append_records", refuse)
        assert second_day(root, likes=77) == 1
    assert committed(root) is False
    assert run_failing(root, 1) != 0  # the retry repairs the rows, not the numbers
    assert committed(root) is False
    assert second_day(root, likes=77) == 3
    assert committed(root) is True
    rows = read_history(root / "history" / "social.jsonl").records
    keys = [r.day_key for r in rows]
    assert len(keys) == len(set(keys))  # none doubled


def test_a_unit_note_whose_numbers_landed_is_settled(tmp_path: Path) -> None:
    """A crash after the numbers write but before the note's removal: the note
    proposes the committed snapshot, carries its own generation id and the digest
    matches the numbers file, so the note counts as done."""
    root = make(tmp_path / "root", CONFIG_NO_HISTORY)
    assert day_one(root) == 3
    numbers = root / "numbers.json"
    gen = "0123456789abcdef"
    write_unit(
        numbers,
        UnitNote(
            hashlib.sha256((root / "numbers.fediverse.json").read_bytes()).hexdigest(),
            hashlib.sha256(numbers.read_bytes()).hexdigest(),
            gen,
            gen,
        ),
    )
    assert run_failing(root, 1) != 0
    assert committed(root) is True
    assert not unit_path(numbers).exists()


# --- the unit note is a transaction record (Daybreak round 4) -----------------------

# Fediverse only, no ordinary source and no history: the numbers file has the same
# bytes every day, so only the transaction's own generation id can prove a write.
CONFIG_FED_ONLY = CONFIG.replace("  history_file: history/social.jsonl\n", "")


def note_of(root: Path) -> UnitNote | None:
    return read_unit(root / "numbers.json")


def test_identical_numbers_bytes_do_not_settle_a_lost_numbers_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak M4-1: the numbers bytes are the same on both days. The process
    dies after the rename and before the numbers write; every source fails on the
    retry. The digest alone would bless the unit; the generation id does not."""
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers_before = (root / "numbers.json").read_bytes()
    with monkeypatch.context() as m:
        inject(m, "numbers_before")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    record = note_of(root)
    assert record is not None and record.landed is None
    assert record.numbers == hashlib.sha256(numbers_before).hexdigest()  # identical
    assert run_failing(root, 1) != 0
    assert (root / "numbers.json").read_bytes() == numbers_before
    assert unit_path(root / "numbers.json").exists()
    assert committed(root) is False  # the numbers write of that run never landed
    assert second_day(root, likes=77) == 3  # a run that writes the numbers file
    assert committed(root) is True
    assert not unit_path(root / "numbers.json").exists()


def test_a_note_from_another_transaction_cannot_prove_a_landing(
    tmp_path: Path,
) -> None:
    """A note whose ``landed`` id differs from its own generation is not a record
    this module wrote: it is unreadable, so it blocks and never settles."""
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers = root / "numbers.json"
    snap = root / "numbers.fediverse.json"
    digest = hashlib.sha256(numbers.read_bytes()).hexdigest()
    shot = hashlib.sha256(snap.read_bytes()).hexdigest()
    forged = UnitNote(shot, digest, "0123456789abcdef", "fedcba9876543210")
    unit_path(numbers).write_text(forged.text())
    assert read_unit(numbers) is None
    assert unit_pending(numbers, snap) is True
    unit_path(numbers).write_text(UnitNote(shot, digest, "0123456789abcdef").text())
    assert unit_pending(numbers, snap) is True  # landed is still None
    assert run_failing(root, 1) != 0
    assert committed(root) is False


def test_a_failed_rename_leaves_no_unit_note_and_an_all_failing_retry_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak M4-2: the rename fails after the note was written. The note is
    removed before the run returns, and a retry in which every source fails finds
    the old snapshot, the old numbers and no note. (No history file, so no
    pending note of the ordinary rows muddies the status.)"""
    root = make(tmp_path / "root", CONFIG_NO_HISTORY)
    assert day_one(root) == 3
    snap, numbers = root / "numbers.fediverse.json", root / "numbers.json"
    snap_before, numbers_before = snap.read_bytes(), numbers.read_bytes()
    real_replace = os.replace

    def replace(src: str, dst: str, **kwargs: Any) -> None:
        if dst == snap.name and src.endswith(".staged"):
            raise OSError(5, "I/O error")
        real_replace(src, dst, **kwargs)

    with monkeypatch.context() as m:
        m.setattr("reach_around.counts.files.os.replace", replace)
        assert second_day(root, likes=77) == 1
    assert not unit_path(numbers).exists()  # removed before the run returned
    assert run_failing(root, 1) != 0
    assert snap.read_bytes() == snap_before
    assert numbers.read_bytes() == numbers_before
    assert not unit_path(numbers).exists()
    assert committed(root) is True  # old snapshot, old numbers: all agree
    assert second_day(root, likes=77) == 3
    assert committed(root) is True


def test_a_crash_between_the_note_and_the_rename_is_abandoned_by_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Daybreak M4-2: the process dies after the note and before the rename. The
    next run (every source down) removes the staged copy AND the note: that
    transaction never crossed the commit point."""
    root = make(tmp_path / "root", CONFIG_NO_HISTORY)
    assert day_one(root) == 3
    snap, numbers = root / "numbers.fediverse.json", root / "numbers.json"
    snap_before = snap.read_bytes()
    with monkeypatch.context() as m:
        inject(m, "rename_before")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert unit_path(numbers).exists()
    assert [n for n in tree(root) if n.endswith(".staged")]
    assert run_failing(root, 1) != 0
    assert not unit_path(numbers).exists()
    assert not [n for n in tree(root) if n.endswith((".staged", ".tmp"))]
    assert snap.read_bytes() == snap_before
    assert committed(root) is True  # old snapshot, old numbers, rows agree
    assert second_day(root, likes=77) == 3
    assert committed(root) is True


def test_a_note_for_a_snapshot_that_was_never_committed_is_removed(
    tmp_path: Path,
) -> None:
    """No staged copy is left either: the proposed snapshot is not the committed
    one, so recovery removes the note on its own."""
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers = root / "numbers.json"
    digest = hashlib.sha256(numbers.read_bytes()).hexdigest()
    write_unit(numbers, UnitNote("a" * 64, digest, "0123456789abcdef"))
    assert run_failing(root, 1) != 0
    assert not unit_path(numbers).exists()
    assert committed(root) is True


FED_ONLY_STEPS = ("rename_after", "numbers_before", "numbers_after", "status_true")


def _agrees(root: Path, old: tuple[bytes, bytes], new: tuple[bytes, bytes]) -> None:
    """A status of true must describe one whole generation: the snapshot and the
    numbers of day one, or both of day two, no note, and a history row for every
    row the snapshot implies."""
    snap = root / "numbers.fediverse.json"
    assert (snap.read_bytes(), (root / "numbers.json").read_bytes()) in (old, new)
    leftovers = (".staged", ".tmp", ".unit")
    assert not [n for n in tree(root) if n.endswith(leftovers)]
    history = root / "history" / "social.jsonl"
    if history.exists():
        held = {r.day_key for r in read_history(history).records}
        rows = snapshot_rows(
            load_snapshots(snap), "r", (BASE + timedelta(days=5)).date()
        )
        assert {r.day_key for r in rows} <= held


@pytest.mark.parametrize("step", STEPS)
def test_a_crash_then_an_all_failing_retry_never_says_a_false_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """Daybreak M4: after a crash at ``step`` a retry in which every source fails
    ends either honestly false, with a note only for a snapshot that DID commit,
    or true for one whole generation. A later good run always settles it."""
    oracle, real = make(tmp_path / "oracle"), make(tmp_path / "real")
    for root in (oracle, real):
        assert day_one(root) == 3
    old = (
        (real / "numbers.fediverse.json").read_bytes(),
        (real / "numbers.json").read_bytes(),
    )
    assert second_day(oracle, likes=77) == 3
    new = (
        (oracle / "numbers.fediverse.json").read_bytes(),
        (oracle / "numbers.json").read_bytes(),
    )
    with monkeypatch.context() as m:
        inject(m, step)
        with pytest.raises(Crash):
            second_day(real, likes=77)
    assert run_failing(real, 1) != 0
    snap, numbers = real / "numbers.fediverse.json", real / "numbers.json"
    assert not [n for n in tree(real) if n.endswith((".staged", ".tmp"))]
    if unit_path(numbers).exists():
        # a note that stays belongs to a snapshot that really is the committed one
        record = note_of(real)
        assert record is not None
        assert record.snapshot == hashlib.sha256(snap.read_bytes()).hexdigest()
        assert committed(real) is False
    if committed(real) is True:
        _agrees(real, old, new)
    assert second_day(real, likes=77) == 3
    assert committed(real) is True
    assert not unit_path(numbers).exists()


@pytest.mark.parametrize("step", FED_ONLY_STEPS)
def test_identical_numbers_never_say_true_after_a_lost_numbers_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    """The numbers bytes are the same every day (no ordinary source, no history):
    after a crash at or past the commit point, an all-failing retry says true only
    when the unit note is gone, which only a landed numbers write removes."""
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, step)
        with pytest.raises(Crash):
            second_day(root, likes=77)
    assert run_failing(root, 1) != 0
    numbers = root / "numbers.json"
    if step == "status_true":
        assert not unit_path(numbers).exists()  # everything landed
    else:
        assert unit_path(numbers).exists()
        assert committed(root) is False
    assert second_day(root, likes=77) == 3
    assert committed(root) is True
    assert not unit_path(numbers).exists()
