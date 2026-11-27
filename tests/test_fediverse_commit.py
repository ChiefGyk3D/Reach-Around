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
    day_conflicts,
    snapshot_rows,
    status_committed,
    sweep_stale,
)
from reach_around.counts.fediverse_store import load_snapshots, snapshot_path
from reach_around.counts.files import plan_paths, write_atomic
from reach_around.counts.history import read_history
from test_fediverse_wiring import (
    CONFIG,
    DID,
    FEED,
    NOW,
    PROFILE,
    bsky_post,
    committed,
    tree,
    write_config,
)

# Day one of every scenario here is three days before the shared test clock: a
# history file is read against the real clock, so a run dated after it would
# leave rows that read as "in the future".
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
    assert not [n for n in names if n.endswith((".staged", ".tmp", ".pending"))]
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
    assert snapshot_rows(snapshots, "r1", BASE.date() + timedelta(days=3)) == []
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
