# SPDX-License-Identifier: Apache-2.0
import json
import os
import stat
from pathlib import Path
from typing import cast

import pytest

from reach_around.counts.files import write_atomic
from reach_around.numbers import (
    NumbersError,
    PlatformCount,
    dump_numbers,
    load_numbers,
    parse_numbers,
)

GOOD = {
    "version": 1,
    "platforms": {"bluesky": {"count": 12, "as_of": "2026-10-24T09:00:00Z"}},
}


def text(**over: object) -> str:
    data = json.loads(json.dumps(GOOD))
    data.update(over)
    return json.dumps(data)


def test_round_trip() -> None:
    numbers = {
        "github": PlatformCount(5, "2026-10-24T09:00:00Z"),
        "bluesky": PlatformCount(0, "2026-10-24T09:00:01Z"),
    }
    dumped = dump_numbers(numbers)
    assert dumped.endswith("\n")
    assert list(json.loads(dumped)["platforms"]) == ["bluesky", "github"]
    assert parse_numbers(dumped) == numbers


@pytest.mark.parametrize(
    "count", [-1, 10**10 + 1, 1.5, 5.0, "12", True, None, [1], {"n": 1}, 10**30]
)
def test_bad_counts_refused_on_load_and_dump(count: object) -> None:
    raw = text(platforms={"bluesky": {"count": count, "as_of": "2026-10-24T09:00:00Z"}})
    with pytest.raises(NumbersError):
        parse_numbers(raw)
    with pytest.raises(NumbersError):
        dump_numbers(
            {"bluesky": PlatformCount(cast(int, count), "2026-10-24T09:00:00Z")}
        )


@pytest.mark.parametrize("count", [0, 1, 10**10])
def test_count_bounds_inclusive(count: int) -> None:
    dump_numbers({"a": PlatformCount(count, "2026-10-24T09:00:00Z")})


@pytest.mark.parametrize(
    "as_of",
    [
        "2026-10-24",
        "2026-10-24T09:00:00",
        "2026-10-24T09:00:00+00:00",
        "2026-13-40T99:00:00Z",
        "x",
        5,
        None,
    ],
)
def test_bad_as_of_refused(as_of: object) -> None:
    raw = text(platforms={"bluesky": {"count": 1, "as_of": as_of}})
    with pytest.raises(NumbersError):
        parse_numbers(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "[]",
        "null",
        "{",
        '{"version": 1}',
        '{"platforms": {}}',
        '{"version": 2, "platforms": {}}',
        '{"version": true, "platforms": {}}',
        '{"version": 1, "platforms": [], "extra": 1}',
        '{"version": 1, "platforms": []}',
        '{"version": 1, "platforms": {"Bad Slug": {"count": 1,'
        ' "as_of": "2026-10-24T09:00:00Z"}}}',
        '{"version": 1, "platforms": {"a": {"count": 1}}}',
        '{"version": 1, "platforms": {"a": {"count": 1,'
        ' "as_of": "2026-10-24T09:00:00Z", "x": 1}}}',
        '{"version": 1, "version": 1, "platforms": {}}',
        '{"version": 1, "platforms": {"a": 5}}',
    ],
)
def test_malformed_files_raise_numbers_error(raw: str) -> None:
    with pytest.raises(NumbersError):
        parse_numbers(raw)


def test_deep_nesting_is_a_numbers_error() -> None:
    with pytest.raises(NumbersError):
        parse_numbers('{"version":1,"platforms":' + "[" * 100_000 + "]" * 100_000 + "}")


def test_too_many_platforms_refused() -> None:
    platforms = {
        f"p{i}": {"count": 1, "as_of": "2026-10-24T09:00:00Z"} for i in range(201)
    }
    with pytest.raises(NumbersError):
        parse_numbers(json.dumps({"version": 1, "platforms": platforms}))


def test_load_refuses_symlink_fifo_big_and_non_utf8(tmp_path: Path) -> None:
    real = tmp_path / "real.json"
    real.write_text(dump_numbers({}), encoding="utf-8")
    assert load_numbers(real) == {}
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(NumbersError):
        load_numbers(link)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises(NumbersError):
        load_numbers(fifo)
    big = tmp_path / "big.json"
    big.write_bytes(b" " * 2_000_000)
    with pytest.raises(NumbersError):
        load_numbers(big)
    padded = tmp_path / "padded.json"
    padded.write_text(dump_numbers({}) + " " * 2_000_000)
    with pytest.raises(NumbersError, match="larger than"):
        load_numbers(padded)
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"\xff\xfe")
    with pytest.raises(NumbersError):
        load_numbers(bad)
    with pytest.raises(NumbersError):
        load_numbers(tmp_path / "missing.json")


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
