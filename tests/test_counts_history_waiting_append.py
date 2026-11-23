# SPDX-License-Identifier: Apache-2.0
"""The waiting append holds the history file across the lock wait (#651)."""

import os
from pathlib import Path
from typing import Any

import pytest

import reach_around.counts.history as hist
from reach_around.counts.history import HistoryError, append_records, read_history
from test_counts_history_tombstone_race import NOW, build, rec


def _holders(path: Path) -> int:
    """How many of this process's open descriptors are the history file."""
    want = os.stat(path)
    count = 0
    for name in os.listdir("/proc/self/fd"):
        try:
            info = os.stat(f"/proc/self/fd/{name}")
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == (want.st_dev, want.st_ino):
            count += 1
    return count


def test_the_history_file_is_held_open_while_waiting_for_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    real = hist._open_sidecar
    seen: list[int] = []

    def watch(*args: Any, **kwargs: Any) -> int:
        seen.append(_holders(path))  # sampled before the wait begins
        return real(*args, **kwargs)

    monkeypatch.setattr(hist, "_open_sidecar", watch)
    baseline = _holders(path)
    append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert baseline == 0
    assert seen == [1]  # the one descriptor the append holds across the wait
    assert _holders(path) == 0  # and it is closed afterwards


def test_the_held_descriptor_is_closed_when_the_lock_wait_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise HistoryError("the history file is locked by another run")

    monkeypatch.setattr(hist, "_open_sidecar", refuse)
    with pytest.raises(HistoryError, match="locked"):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert _holders(path) == 0


def test_an_exchange_style_replacement_during_the_wait_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "h.jsonl"
    build(path)
    other = tmp_path / "other.jsonl"
    build(other, value=999)
    original = path.read_bytes()
    real = hist._open_sidecar
    armed = [True]

    def exchange_while_waiting(*args: Any, **kwargs: Any) -> int:
        fd = real(*args, **kwargs)
        if armed[0]:
            armed[0] = False
            dir_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                assert hist._exchange(dir_fd, "other.jsonl", "h.jsonl")
            finally:
                os.close(dir_fd)
        return fd

    monkeypatch.setattr(hist, "_open_sidecar", exchange_while_waiting)
    with pytest.raises(HistoryError, match="replaced"):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert b"2026-10-09" not in path.read_bytes()
    assert other.read_bytes() == original  # the exchanged-out file is untouched


def test_a_file_created_during_the_wait_is_appended_to_not_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file was absent before the wait: that is not a replacement."""
    path = tmp_path / "h.jsonl"
    real = hist._open_sidecar
    armed = [True]

    def create_while_waiting(*args: Any, **kwargs: Any) -> int:
        if armed[0]:
            armed[0] = False
            build(path)  # another writer got there first, before we hold the lock
        return real(*args, **kwargs)

    monkeypatch.setattr(hist, "_open_sidecar", create_while_waiting)
    assert not path.exists()
    append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    history = read_history(path)
    assert len(history.records) == 4  # the three it found plus its own
    assert path.read_bytes().count(b"\n") == 4


def test_a_symlink_replaced_during_the_wait_is_not_appended_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an absent name may be skipped; a symlink there must abort."""
    path = tmp_path / "h.jsonl"
    target = tmp_path / "target.jsonl"
    build(target)
    path.symlink_to(target.name)
    real = hist._open_sidecar
    armed = [True]

    def replace_while_waiting(*args: Any, **kwargs: Any) -> int:
        if armed[0]:
            armed[0] = False
            path.unlink()
            build(path)  # a regular history file now sits at the name
        return real(*args, **kwargs)

    monkeypatch.setattr(hist, "_open_sidecar", replace_while_waiting)
    before = target.read_bytes()
    with pytest.raises(HistoryError):
        append_records(path, [rec(as_of_utc="2026-10-09T09:00:00Z")], now=NOW)
    assert b"2026-10-09" not in path.read_bytes()
    assert target.read_bytes() == before
