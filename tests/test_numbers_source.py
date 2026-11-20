# SPDX-License-Identifier: Apache-2.0
"""The optional ``source`` of a numbers-file entry (#626): the engine reads and
writes it, the instance validator passes exactly that key with exactly the two
closed values, and an old file without it keeps working."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reach_around.config import ConfigError
from reach_around.config.numbers import NumbersEntry, parse_numbers
from reach_around.counts.files import dump_numbers

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "examples" / "instance-repo" / "scripts" / "validate-numbers.py"
STAMP = "2026-10-24T12:00:00Z"
WHEN = datetime(2026, 10, 24, 12, tzinfo=UTC)


def doc(**entry: object) -> bytes:
    body = {"count": 5, "as_of": STAMP, **entry}
    return json.dumps({"version": 1, "platforms": {"twitch": body}}).encode()


def test_an_old_file_without_the_key_is_read_with_no_source() -> None:
    numbers = parse_numbers(doc())
    assert numbers.entries["twitch"].source is None


@pytest.mark.parametrize("value", ["api", "hand"])
def test_the_two_closed_values_are_read(value: str) -> None:
    assert parse_numbers(doc(source=value)).entries["twitch"].source == value


@pytest.mark.parametrize(
    "value", ["manual", "API", "", None, True, 1, ["api"], "imported", "api "]
)
def test_any_other_value_is_refused(value: object) -> None:
    with pytest.raises(ConfigError):
        parse_numbers(doc(source=value))


def test_any_other_key_is_still_refused() -> None:
    with pytest.raises(ConfigError):
        parse_numbers(doc(note="x"))
    with pytest.raises(ConfigError):
        parse_numbers(
            json.dumps({"version": 1, "platforms": {"x": {"count": 1}}}).encode()
        )


def test_the_writer_keeps_the_key_only_when_it_is_set() -> None:
    text = dump_numbers(
        {
            "a": NumbersEntry(1, WHEN, "api"),
            "b": NumbersEntry(2, WHEN, "hand"),
            "c": NumbersEntry(3, WHEN),
        }
    )
    platforms = json.loads(text)["platforms"]
    assert platforms["a"]["source"] == "api"
    assert platforms["b"]["source"] == "hand"
    assert "source" not in platforms["c"]
    # and the engine reads back exactly what it wrote
    back = parse_numbers(text.encode()).entries
    assert [back[k].source for k in "abc"] == ["api", "hand", None]


def run_validator(tmp_path: Path, data: bytes) -> tuple[int, str]:
    src, out = tmp_path / "in.json", tmp_path / "out.json"
    src.write_bytes(data)
    done = subprocess.run(  # noqa: S603 - fixed argv, the template's script
        [sys.executable, "-I", str(VALIDATOR), "--in", str(src), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, out.read_text(encoding="utf-8") if out.exists() else ""


@pytest.mark.parametrize("value", ["api", "hand"])
def test_the_instance_validator_passes_and_keeps_the_key(
    tmp_path: Path, value: str
) -> None:
    code, written = run_validator(tmp_path, doc(source=value))
    assert code == 0
    assert json.loads(written)["platforms"]["twitch"]["source"] == value
    # the validator's output is what the engine itself would write
    assert written == dump_numbers({"twitch": NumbersEntry(5, WHEN, value)})


def test_the_instance_validator_accepts_an_old_file_unchanged_in_shape(
    tmp_path: Path,
) -> None:
    code, written = run_validator(tmp_path, doc())
    assert code == 0 and "source" not in written


@pytest.mark.parametrize(
    "extra",
    [
        {"source": "manual"},
        {"source": True},
        {"source": None},
        {"source": ["api"]},
        {"note": "x"},
        {"source": "api", "url": "http://x"},
    ],
)
def test_the_instance_validator_stays_strict(
    tmp_path: Path, extra: dict[str, object]
) -> None:
    code, written = run_validator(tmp_path, doc(**extra))
    assert code != 0 and written == ""
