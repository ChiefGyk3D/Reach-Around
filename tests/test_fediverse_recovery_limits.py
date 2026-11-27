# SPDX-License-Identifier: Apache-2.0
"""Bounded recovery reads and the quarantine of an unparsable unit note (#940).

Daybreak round 5: the digest reads of the unit check are streamed and capped, the
number of staged candidates is capped, an unparsable note is moved aside, and a
note cannot be made to settle by an ordinary path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from reach_around.config.numbers import MAX_NUMBERS_BYTES
from reach_around.counts.fediverse_commit import (
    MAX_STAGED_CANDIDATES,
    UnitFileTooLarge,
    UnitNote,
    bad_unit_path,
    file_digest,
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
    assert run_failing(root, 1) != 0
    assert unit_path(numbers).is_symlink()
    assert not bad_unit_path(numbers).exists()
    assert target.read_text() == "{"
    assert committed(root) is False
    assert "elsewhere.txt" in tree(root)
