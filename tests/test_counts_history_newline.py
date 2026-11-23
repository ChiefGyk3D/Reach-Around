# SPDX-License-Identifier: Apache-2.0
"""A decoded value that ends in a newline is not a valid name (#624).

``$`` also matches before a final newline, so these patterns must be used as
full matches. Collected loosely (every field), asserted strictly.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from reach_around.counts.history import (
    GENESIS,
    HistoryError,
    Record,
    parse_as_of,
    read_history,
    validate,
)

NOW = datetime(2026, 12, 31, 12, 0, 0, tzinfo=UTC)

GOOD: dict[str, Any] = {
    "platform": "twitch",
    "metric": "followers",
    "value": 10,
    "as_of_utc": "2026-11-08T09:00:00Z",
    "source": "api",
    "run_id": "run-1",
}


def rec(**over: Any) -> Record:
    return Record(**{**GOOD, **over})


@pytest.mark.parametrize("field", ["platform", "metric", "run_id", "as_of_utc"])
def test_validate_refuses_a_trailing_newline(field: str) -> None:
    validate(rec(), NOW)  # control: the clean value is accepted
    with pytest.raises(HistoryError):
        validate(rec(**{field: GOOD[field] + "\n"}), NOW)


def test_parse_as_of_refuses_a_trailing_newline() -> None:
    assert parse_as_of("2026-11-08T09:00:00Z")
    with pytest.raises(HistoryError):
        parse_as_of("2026-11-08T09:00:00Z\n")


@pytest.mark.parametrize("field", ["platform", "metric", "run_id", "as_of_utc", "prev"])
def test_old_path_record_with_trailing_newline_is_refused(
    tmp_path: Path, field: str
) -> None:
    obj: dict[str, Any] = {**GOOD, "type": "record", "prev": GENESIS}
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    assert read_history(path).records  # control: the clean line reads back
    obj[field] = obj[field] + "\n"  # the JSON text holds a backslash-n
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError):
        read_history(path)
