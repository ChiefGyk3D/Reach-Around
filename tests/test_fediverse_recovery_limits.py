# SPDX-License-Identifier: Apache-2.0
"""Bounded recovery reads and the quarantine of an unparsable unit note (#940).

Daybreak round 5: the digest reads of the unit check are streamed and capped, the
number of staged candidates is capped, an unparsable note is moved aside, and a
note cannot be made to settle by an ordinary path.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts.fediverse_commit import (
    MAX_DIR_ENTRIES,
    MAX_STAGED_CANDIDATES,
    UnitFileTooLarge,
    UnitNote,
    bad_unit_path,
    file_digest,
    find_stale,
    quarantine_unit,
    unit_path,
    unit_pending,
    write_unit,
)
from reach_around.counts.fediverse_store import MAX_SNAPSHOT_BYTES
from test_fediverse_commit import (
    CONFIG_FED_ONLY,
    Crash,
    day_one,
    inject,
    make,
    run_failing,
    second_day,
)
from test_fediverse_wiring import committed, frozen_clock, tree

SHARED_FIXTURES = (frozen_clock,)
pytestmark = pytest.mark.usefixtures("frozen_clock")

WORD = "UnitFileTooLarge"


def crashed_before_rename(root: Path, m: pytest.MonkeyPatch) -> None:
    """Day two dies after the unit note and before the rename: a note and a staged
    copy are on disk."""
    inject(m, "rename_before")
    with pytest.raises(Crash):
        second_day(root, likes=77)


def staged_of(root: Path) -> list[Path]:
    return [p for p in root.iterdir() if p.name.endswith(".staged")]


def test_file_digest_streams_and_refuses_a_file_over_its_limit(
    tmp_path: Path,
) -> None:
    ok, big = tmp_path / "ok", tmp_path / "big"
    ok.write_bytes(b"a" * 200_000)  # several chunks
    big.write_bytes(b"a" * 1001)
    assert file_digest(ok, 200_000) == hashlib.sha256(b"a" * 200_000).hexdigest()
    with pytest.raises(UnitFileTooLarge):
        file_digest(big, 1000)
    with pytest.raises(UnitFileTooLarge):
        file_digest(tmp_path, 1000)  # not a regular file
    assert file_digest(tmp_path / "absent", 1000) is None


def test_an_oversized_snapshot_leaves_the_unit_pending_in_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        crashed_before_rename(root, m)
    note = unit_path(root / "numbers.json")
    assert note.exists()
    snap = root / "numbers.fediverse.json"
    snap.write_bytes(snap.read_bytes() + b" " * (MAX_SNAPSHOT_BYTES + 1))
    capsys.readouterr()
    assert run_failing(root, 1) == 1
    assert WORD in capsys.readouterr().err
    assert note.exists()  # nothing was decided about the transaction
    assert committed(root) is False
    assert unit_pending(root / "numbers.json", snap) is True


def test_an_oversized_numbers_file_is_never_settled(tmp_path: Path) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers, snap = root / "numbers.json", root / "numbers.fediverse.json"
    gen = "0123456789abcdef"
    write_unit(
        numbers,
        UnitNote(
            hashlib.sha256(snap.read_bytes()).hexdigest(),
            hashlib.sha256(numbers.read_bytes()).hexdigest(),
            gen,
            gen,
        ),
    )
    numbers.write_bytes(numbers.read_bytes() + b" " * (MAX_NUMBERS_BYTES + 1))
    with pytest.raises(UnitFileTooLarge):  # refused before any byte is hashed
        file_digest(numbers, MAX_NUMBERS_BYTES)
    assert unit_pending(numbers, snap) is True  # a controlled answer, no crash
    assert unit_path(numbers).exists()


def test_an_oversized_staged_file_leaves_the_unit_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers, snap = root / "numbers.json", root / "numbers.fediverse.json"
    # A note that proposes the COMMITTED snapshot makes recovery look at the
    # staged copies (one equal to the proposal would mean it never committed).
    write_unit(
        numbers,
        UnitNote(
            hashlib.sha256(snap.read_bytes()).hexdigest(),
            hashlib.sha256(numbers.read_bytes()).hexdigest(),
            "0123456789abcdef",
        ),
    )
    staged = root / ".numbers.fediverse.json.0123456789abcdef.staged"
    staged.write_bytes(b" " * (MAX_SNAPSHOT_BYTES + 1))
    capsys.readouterr()
    assert run_failing(root, 1) == 1
    assert WORD in capsys.readouterr().err
    assert unit_path(numbers).exists()
    assert staged.exists()
    assert committed(root) is False
    staged.unlink()  # the documented manual step
    assert run_failing(root, 2) != 0
    assert unit_path(numbers).exists()  # landed was never set: still unsettled
    assert committed(root) is False


def test_many_staged_candidates_leave_the_unit_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        crashed_before_rename(root, m)
    extra = [
        root / f".numbers.fediverse.json.{i:016x}.staged"
        for i in range(MAX_STAGED_CANDIDATES + 1)
    ]
    for path in extra:
        path.write_text("x")
    capsys.readouterr()
    assert run_failing(root, 1) == 1
    assert WORD in capsys.readouterr().err
    assert unit_path(root / "numbers.json").exists()
    assert all(p.exists() for p in extra)
    assert committed(root) is False
    for path in staged_of(root):
        path.unlink()
    assert run_failing(root, 2) != 0
    assert committed(root) is True


def test_a_note_cannot_be_settled_by_an_ordinary_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever number of all-failing retries run, a note whose numbers write never
    landed stays unsettled: only a run that writes the numbers settles it."""
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    with monkeypatch.context() as m:
        inject(m, "numbers_before")
        with pytest.raises(Crash):
            second_day(root, likes=77)
    note = unit_path(root / "numbers.json")
    before = note.read_bytes()
    for day in (1, 2, 3):
        assert run_failing(root, day) != 0
        assert note.read_bytes() == before
        assert committed(root) is False
    assert second_day(root, likes=77, days=4) == 3
    assert not note.exists()
    assert committed(root) is True


def test_an_unparsable_note_is_quarantined_and_the_next_run_proceeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers = root / "numbers.json"
    note, bad = unit_path(numbers), bad_unit_path(numbers)
    bad.write_text("older evidence")
    note.write_text("{")
    capsys.readouterr()
    assert run_failing(root, 1) != 0
    assert "unit_note_quarantined" in capsys.readouterr().err
    assert not note.exists()
    assert bad.read_text() == "{"  # replaced the older file
    assert committed(root) is False  # that run keeps it false
    assert second_day(root, likes=77) == 3  # the next run proceeds normally
    assert committed(root) is True
    assert bad.exists()  # evidence stays until the owner deletes it


def test_a_note_that_is_a_link_is_not_moved(tmp_path: Path) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    numbers = root / "numbers.json"
    target = root / "elsewhere.txt"
    target.write_text("{")
    unit_path(numbers).symlink_to(target)
    assert quarantine_unit(numbers) is False  # documented: a link is left alone
    assert run_failing(root, 1) != 0
    assert unit_path(numbers).is_symlink()
    assert unit_path(numbers).readlink() == target
    assert not bad_unit_path(numbers).exists()
    assert not bad_unit_path(numbers).is_symlink()
    assert target.read_text() == "{"
    assert committed(root) is False
    assert "elsewhere.txt" in tree(root)
    # The same bytes as a plain file ARE moved: the function does act, so the
    # link case above is a real refusal and not an absent feature.
    unit_path(numbers).unlink()
    unit_path(numbers).write_text("{")
    assert quarantine_unit(numbers) is True
    assert bad_unit_path(numbers).read_text() == "{"
    assert not unit_path(numbers).exists()


class _Scan:
    """Counts the directory entries a scan yields."""

    def __init__(self, real: Any, counter: list[int]) -> None:
        self._real, self._counter = real, counter

    def __enter__(self) -> _Scan:
        self._real.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        self._real.__exit__(*args)

    def __iter__(self) -> _Scan:
        return self

    def __next__(self) -> Any:
        entry = next(self._real)
        self._counter[0] += 1
        return entry


def counted_scan(m: pytest.MonkeyPatch) -> list[int]:
    counter = [0]
    real = os.scandir

    def scandir(path: Any) -> _Scan:
        return _Scan(real(path), counter)

    m.setattr("reach_around.counts.fediverse_commit.os.scandir", scandir)
    return counter


def test_discovery_stops_early_at_the_staged_candidate_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = tmp_path / "numbers.fediverse.json"
    snap.write_text("{}")
    for i in range(5000):
        (tmp_path / f".numbers.fediverse.json.{i:016x}.staged").write_text("x")
    others = 1  # the snapshot itself
    with monkeypatch.context() as m:
        seen = counted_scan(m)
        with pytest.raises(UnitFileTooLarge):
            find_stale(snap)
    # At most the cap plus one matches, and nothing beyond what was needed.
    assert seen[0] <= MAX_STAGED_CANDIDATES + 1 + others
    assert len(list(tmp_path.iterdir())) == 5001  # nothing was removed


def test_discovery_gives_up_after_the_entry_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snap = tmp_path / "numbers.fediverse.json"
    snap.write_text("{}")
    for i in range(MAX_DIR_ENTRIES + 100):
        (tmp_path / f"n{i}").write_text("")
    with monkeypatch.context() as m:
        seen = counted_scan(m)
        with pytest.raises(UnitFileTooLarge):
            find_stale(snap)
    assert seen[0] == MAX_DIR_ENTRIES + 1  # stopped at the cap, not the end


def test_a_discovery_overflow_demotes_a_true_status_and_keeps_every_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make(tmp_path / "root", CONFIG_FED_ONLY)
    assert day_one(root) == 3
    assert committed(root) is True
    extra = [
        root / f".numbers.fediverse.json.{i:016x}.tmp"
        for i in range(MAX_STAGED_CANDIDATES + 1)
    ]
    for path in extra:
        path.write_text("x")
    capsys.readouterr()
    assert run_failing(root, 1) == 1
    assert WORD in capsys.readouterr().err
    assert committed(root) is False  # fail closed
    assert all(p.exists() for p in extra)  # evidence kept


def test_a_normal_folder_still_lists_its_stale_files(tmp_path: Path) -> None:
    snap = tmp_path / "numbers.fediverse.json"
    snap.write_text("{}")
    names = [f".numbers.fediverse.json.{i:016x}.staged" for i in range(8)]
    for name in names:
        (tmp_path / name).write_text("x")
    (tmp_path / "other.txt").write_text("x")
    assert sorted(p.name for p in find_stale(snap)) == sorted(names)


def test_growth_during_hashing_is_caught_and_never_read_past_limit_plus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size looked fine at fstat time (50 bytes) but the file is bigger when
    read: the read asks for no more than ``limit + 1`` bytes in total."""
    path = tmp_path / "grows"
    path.write_bytes(b"a" * 5000)
    real_fstat, real_read = os.fstat, os.read
    asked: list[int] = []

    class Small:
        def __init__(self, info: os.stat_result) -> None:
            self.st_mode, self.st_size = info.st_mode, 50

    def fstat(fd: int) -> Any:
        return Small(real_fstat(fd))

    def read(fd: int, n: int) -> bytes:
        asked.append(n)
        return real_read(fd, n)

    with monkeypatch.context() as m:
        m.setattr("reach_around.counts.fediverse_commit.os.fstat", fstat)
        m.setattr("reach_around.counts.fediverse_commit.os.read", read)
        with pytest.raises(UnitFileTooLarge):
            file_digest(path, 100)
    assert sum(asked) <= 101
