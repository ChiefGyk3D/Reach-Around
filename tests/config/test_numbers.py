# SPDX-License-Identifier: Apache-2.0
"""The optional numbers file: strict loading, unknown keys and precedence."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.config import SiteConfig
from reach_around.config.loader import ConfigError
from reach_around.config.numbers import (
    MAX_NUMBERS_BYTES,
    NumbersEntry,
    load_numbers,
    resolve_count,
)

NOW = datetime(2027, 3, 10, 12, 0, tzinfo=UTC)
GOOD = {"count": 123, "as_of": "2027-03-09T12:00:00Z"}


def write(tmp_path: Path, doc: Any, name: str = "numbers.json") -> Path:
    path = tmp_path / name
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), "utf-8")
    return path


def wrap(platforms: Any, **top: Any) -> dict[str, Any]:
    return {"version": 1, "platforms": platforms, **top}


def test_a_good_file_loads(tmp_path: Path) -> None:
    numbers = load_numbers(write(tmp_path, wrap({"twitch": GOOD})), now=NOW)
    entry = numbers.entries["twitch"]
    assert entry.count == 123
    assert entry.as_of == datetime(2027, 3, 9, 12, 0, tzinfo=UTC)
    assert entry.as_of_date == date(2027, 3, 9)


def test_offsets_are_converted_to_utc(tmp_path: Path) -> None:
    doc = wrap({"twitch": {"count": 1, "as_of": "2027-03-09T23:30:00-05:00"}})
    entry = load_numbers(write(tmp_path, doc), now=NOW).entries["twitch"]
    assert entry.as_of_date == date(2027, 3, 10)


def test_an_empty_platforms_map_is_fine(tmp_path: Path) -> None:
    assert load_numbers(write(tmp_path, wrap({})), now=NOW).entries == {}


@pytest.mark.parametrize(
    "doc",
    [
        [],
        "null",
        "7",
        {"platforms": {}},
        {"version": 2, "platforms": {}},
        {"version": True, "platforms": {}},
        {"version": 1},
        {"version": 1, "platforms": []},
        {"version": 1, "platforms": {}, "extra": 1},
        wrap({"twitch": 5}),
        wrap({"twitch": {"count": 1}}),
        wrap({"twitch": {"as_of": "2027-03-09T12:00:00Z"}}),
        wrap({"twitch": {**GOOD, "extra": 1}}),
        wrap({"": GOOD}),
        wrap({"k" * 65: GOOD}),
    ],
)
def test_wrong_shapes_are_refused(tmp_path: Path, doc: Any) -> None:
    path = write(tmp_path, doc)
    with pytest.raises(ConfigError) as caught:
        load_numbers(path, now=NOW)
    assert str(path) in str(caught.value)


@pytest.mark.parametrize(
    "count",
    [-1, 10**10 + 1, 1.5, 3.0, "5", True, None, [1], 10**400],
)
def test_counts_must_be_ints_in_range(tmp_path: Path, count: Any) -> None:
    doc = wrap({"twitch": {"count": count, "as_of": GOOD["as_of"]}})
    with pytest.raises(ConfigError, match="count"):
        load_numbers(write(tmp_path, json.dumps(doc)), now=NOW)


def test_float_spelling_of_an_int_is_refused(tmp_path: Path) -> None:
    text = (
        '{"version":1,"platforms":{"twitch":'
        '{"count":3.0,"as_of":"2027-03-09T12:00:00Z"}}}'
    )
    with pytest.raises(ConfigError, match="count"):
        load_numbers(write(tmp_path, text), now=NOW)


@pytest.mark.parametrize("count", [0, 10**10])
def test_count_bounds_are_inclusive(tmp_path: Path, count: int) -> None:
    doc = wrap({"twitch": {"count": count, "as_of": GOOD["as_of"]}})
    assert load_numbers(write(tmp_path, doc), now=NOW).entries["twitch"].count == count


@pytest.mark.parametrize(
    "as_of",
    [
        "yesterday",
        "2027-03-09",
        "2027-03-09T12:00:00",
        "2027-03-09 12:00:00Z",
        "2027-13-09T12:00:00Z",
        "2027-03-32T12:00:00Z",
        "1999-12-31T23:59:59Z",
        "2027-03-11T12:00:01Z",
        "2027-03-09T12:00:00Z\n",
        20270309,
        None,
        True,
    ],
)
def test_as_of_must_be_a_recent_utc_or_offset_timestamp(
    tmp_path: Path, as_of: Any
) -> None:
    doc = wrap({"twitch": {"count": 1, "as_of": as_of}})
    with pytest.raises(ConfigError, match="as_of"):
        load_numbers(write(tmp_path, doc), now=NOW)


def test_one_day_of_clock_skew_is_allowed(tmp_path: Path) -> None:
    doc = wrap({"twitch": {"count": 1, "as_of": "2027-03-11T12:00:00Z"}})
    load_numbers(write(tmp_path, doc), now=NOW)


def test_duplicate_keys_are_refused(tmp_path: Path) -> None:
    text = (
        '{"version":1,"platforms":{"twitch":{"count":1,"as_of":"2027-03-09T12:00:00Z"},'
        '"twitch":{"count":2,"as_of":"2027-03-09T12:00:00Z"}}}'
    )
    with pytest.raises(ConfigError, match="duplicate"):
        load_numbers(write(tmp_path, text), now=NOW)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_constants_are_refused(tmp_path: Path, constant: str) -> None:
    text = (
        '{"version":1,"platforms":{"twitch":{"count":'
        + constant
        + ',"as_of":"2027-03-09T12:00:00Z"}}}'
    )
    with pytest.raises(ConfigError):
        load_numbers(write(tmp_path, text), now=NOW)


def test_hostile_nesting_is_an_error_not_a_crash(tmp_path: Path) -> None:
    text = "[" * 100_000 + "]" * 100_000
    assert len(text) < MAX_NUMBERS_BYTES
    with pytest.raises(ConfigError, match="deep|nest"):
        load_numbers(write(tmp_path, text), now=NOW)


def test_invalid_json_and_bad_encoding_name_the_file(tmp_path: Path) -> None:
    for payload in (b"{not json", b"\xff\xfe\x00", b"", b'{"a":1} trailing'):
        path = tmp_path / "numbers.json"
        path.write_bytes(payload)
        with pytest.raises(ConfigError) as caught:
            load_numbers(path, now=NOW)
        assert str(path) in str(caught.value)


def test_size_cap(tmp_path: Path) -> None:
    pad = " " * (MAX_NUMBERS_BYTES - 40)
    ok = '{"version":1,"platforms":{}}' + pad
    assert len(ok.encode()) <= MAX_NUMBERS_BYTES
    load_numbers(write(tmp_path, ok), now=NOW)
    with pytest.raises(ConfigError, match="too large"):
        load_numbers(write(tmp_path, ok + " " * 100), now=NOW)


def test_too_many_entries_are_refused(tmp_path: Path) -> None:
    many = {f"p{i}": GOOD for i in range(1001)}
    with pytest.raises(ConfigError, match="entries"):
        load_numbers(write(tmp_path, wrap(many)), now=NOW)


def test_missing_symlink_directory_and_fifo_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        load_numbers(tmp_path / "nope.json", now=NOW)
    assert "nope.json" in str(caught.value)
    real = write(tmp_path, wrap({}))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(ConfigError, match="symlink"):
        load_numbers(link, now=NOW)
    with pytest.raises(ConfigError):
        load_numbers(tmp_path, now=NOW)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    with pytest.raises(ConfigError, match="regular"):
        load_numbers(fifo, now=NOW)


# --- unknown keys -------------------------------------------------------------


def config_with(*ids: str) -> SiteConfig:
    items = [
        {"platform": "twitch", "handle": f"h{i}", "url": "https://example.com/x"}
        for i, _ in enumerate(ids)
    ]
    return SiteConfig.model_validate(
        {
            "version": 1,
            "creator": {"name": "X"},
            "tabs": [
                {"id": "home", "title": "Home", "blocks": [{"id": "s", "items": items}]}
            ],
        }
    )


def test_unknown_keys_are_counted_not_fatal(tmp_path: Path) -> None:
    numbers = load_numbers(
        write(
            tmp_path,
            wrap(
                {
                    "twitch": GOOD,
                    "s-twitch-2": GOOD,
                    "kick": GOOD,
                    "nonsense": GOOD,
                    "s-ghost": GOOD,
                }
            ),
        ),
        now=NOW,
    )
    config = config_with("a", "b")
    assert numbers.unknown_keys(config) == ["nonsense", "s-ghost"]


# --- precedence ---------------------------------------------------------------


def entry(stamp: str) -> NumbersEntry:
    return NumbersEntry(count=500, as_of=datetime.fromisoformat(stamp).astimezone(UTC))


def test_no_numbers_entry_keeps_the_manual_value() -> None:
    assert resolve_count(7, date(2027, 1, 1), None) == (7, date(2027, 1, 1))
    assert resolve_count(None, None, None) == (None, None)


def test_a_numbers_entry_fills_an_empty_count() -> None:
    assert resolve_count(None, None, entry("2027-03-09T12:00:00+00:00")) == (
        500,
        date(2027, 3, 9),
    )


def test_a_manual_value_without_a_date_loses() -> None:
    assert resolve_count(7, None, entry("2001-01-01T00:00:00+00:00")) == (
        500,
        date(2001, 1, 1),
    )


def test_a_newer_numbers_date_wins() -> None:
    assert (
        resolve_count(7, date(2027, 3, 1), entry("2027-03-02T00:00:00+00:00"))[0] == 500
    )


def test_an_older_numbers_date_loses() -> None:
    assert resolve_count(7, date(2027, 3, 2), entry("2027-03-01T23:59:59+00:00")) == (
        7,
        date(2027, 3, 2),
    )


def test_the_same_day_goes_to_the_numbers_file() -> None:
    assert (
        resolve_count(7, date(2027, 3, 2), entry("2027-03-02T00:00:00+00:00"))[0] == 500
    )
