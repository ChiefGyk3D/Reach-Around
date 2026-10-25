# SPDX-License-Identifier: Apache-2.0
"""The writer half of the numbers file; the reader is the build's own
``reach_around.config.numbers.load_numbers``, so these tests round-trip through it."""

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from reach_around.config.loader import ConfigError
from reach_around.config.numbers import NumbersEntry, load_numbers
from reach_around.counts.files import dump_numbers, utc_stamp, write_atomic

WHEN = datetime(2026, 10, 24, 9, 0, 0, tzinfo=UTC)


def test_utc_stamp_is_second_precision_z() -> None:
    moment = datetime(2026, 10, 24, 9, 0, 1, 999999, tzinfo=UTC)
    assert utc_stamp(moment) == "2026-10-24T09:00:01Z"


def test_what_refresh_writes_loads_through_the_builds_loader(tmp_path: Path) -> None:
    entries = {
        "socials-bluesky": NumbersEntry(0, WHEN),  # an item id key
        "github": NumbersEntry(5, WHEN),
    }
    text = dump_numbers(entries)
    assert text.endswith("\n")
    assert list(json.loads(text)["platforms"]) == ["github", "socials-bluesky"]
    path = tmp_path / "numbers.json"
    path.write_text(text)
    loaded = load_numbers(path, now=WHEN)
    assert dict(loaded.entries) == entries


@pytest.mark.parametrize("count", [-1, 10**10 + 1, 1.5, True, "5", None])
def test_dump_refuses_counts_the_builds_loader_would_refuse(count: object) -> None:
    bad = {"github": NumbersEntry(cast(int, count), WHEN)}
    with pytest.raises(ConfigError):
        dump_numbers(bad)


@pytest.mark.parametrize("key", ["", "x" * 65])
def test_dump_refuses_keys_the_builds_loader_would_refuse(key: str) -> None:
    with pytest.raises(ConfigError):
        dump_numbers({key: NumbersEntry(1, WHEN)})


def test_write_atomic_mode_content_and_no_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "numbers.json"
    target.write_text("old")
    os.chmod(target, 0o644)
    write_atomic(target, "new\n")
    assert target.read_text() == "new\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["numbers.json"]


def test_write_atomic_failure_keeps_the_old_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "numbers.json"
    target.write_text("old")

    def boom(src: object, dst: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        write_atomic(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["numbers.json"]


def test_write_atomic_fsyncs_file_before_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def spy_replace(src: str, dst: str, **kwargs: int) -> None:
        events.append("replace")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    write_atomic(tmp_path / "n.json", "x")
    assert events[0] == "fsync" and events.index("replace") == 1
