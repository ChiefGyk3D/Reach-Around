# SPDX-License-Identifier: Apache-2.0
"""scripts/validate-numbers.py in the instance template: the one check both
jobs of the counts refresh run on numbers.json before it is trusted."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from reach_around.config.blocks import MAX_COUNT
from reach_around.config.numbers import NumbersEntry
from reach_around.counts.files import dump_numbers

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = ROOT / "examples" / "instance-repo" / "scripts" / "validate-numbers.py"
STAMP = "2026-10-24T12:00:00Z"
GOOD = {"version": 1, "platforms": {"twitch": {"count": 12, "as_of": STAMP}}}


def run(source: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, the template's script
        [sys.executable, "-I", str(VALIDATOR), "--in", str(source), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


def put(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "in.json"
    path.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
    return path


def test_a_good_file_is_written_in_the_engines_own_format(tmp_path: Path) -> None:
    out = tmp_path / "out" / "numbers.json"
    out.parent.mkdir()
    done = run(put(tmp_path, GOOD), out)
    assert done.returncode == 0, done.stderr
    expected = dump_numbers(
        {"twitch": NumbersEntry(12, datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC))}
    )
    assert out.read_text(encoding="utf-8") == expected
    assert [p.name for p in out.parent.iterdir()] == ["numbers.json"]


def test_a_real_engine_file_passes(tmp_path: Path) -> None:
    text = dump_numbers(
        {
            "youtube": NumbersEntry(5, datetime(2026, 10, 1, tzinfo=UTC)),
            "my-link": NumbersEntry(MAX_COUNT, datetime(2026, 10, 2, tzinfo=UTC)),
        }
    )
    out = tmp_path / "numbers.json"
    assert run(put(tmp_path, text.encode()), out).returncode == 0


def _mut(**platforms: object) -> dict[str, object]:
    return {"version": 1, "platforms": platforms}


BAD: dict[str, object] = {
    "not json": b"{nope",
    "not utf8": b"\xff\xfe",
    "array": [],
    "extra top key": {**GOOD, "note": "x"},
    "missing version": {"platforms": {}},
    "wrong version": {"version": 2, "platforms": {}},
    "bool version": {"version": True, "platforms": {}},
    "platforms not object": {"version": 1, "platforms": []},
    "bad key": _mut(**{"Twitch!": {"count": 1, "as_of": STAMP}}),
    "long key": _mut(**{"a" * 41: {"count": 1, "as_of": STAMP}}),
    "entry extra field": _mut(x={"count": 1, "as_of": STAMP, "url": "http://x"}),
    "entry missing as_of": _mut(x={"count": 1}),
    "string count": _mut(x={"count": "1", "as_of": STAMP}),
    "float count": _mut(x={"count": 1.5, "as_of": STAMP}),
    "bool count": _mut(x={"count": True, "as_of": STAMP}),
    "negative count": _mut(x={"count": -1, "as_of": STAMP}),
    "huge count": _mut(x={"count": MAX_COUNT + 1, "as_of": STAMP}),
    "offset stamp": _mut(x={"count": 1, "as_of": "2026-10-24T12:00:00+02:00"}),
    "text stamp": _mut(x={"count": 1, "as_of": "<script>"}),
    "impossible date": _mut(x={"count": 1, "as_of": "2026-02-30T12:00:00Z"}),
    "future stamp": _mut(x={"count": 1, "as_of": "2999-01-01T00:00:00Z"}),
    "ancient stamp": _mut(x={"count": 1, "as_of": "1999-01-01T00:00:00Z"}),
    "duplicate key": (
        b'{"version":1,"platforms":{"x":{"count":1,"count":2,'
        b'"as_of":"2026-10-24T12:00:00Z"}}}'
    ),
    "nan": (
        b'{"version":1,"platforms":{"x":{"count":NaN,"as_of":"2026-10-24T12:00:00Z"}}}'
    ),
    "too many entries": _mut(
        **{f"p{i}": {"count": 1, "as_of": STAMP} for i in range(1001)}
    ),
    "too big": b" " * (64 * 1024 + 1) + b"{}",
    "deep": b"[" * 5000 + b"]" * 5000,
}


@pytest.mark.parametrize("label", sorted(BAD))
def test_a_bad_file_is_refused_and_nothing_is_written(
    tmp_path: Path, label: str
) -> None:
    out = tmp_path / "numbers.json"
    out.write_text("old", encoding="utf-8")
    done = run(put(tmp_path, BAD[label]), out)
    assert done.returncode != 0, label
    assert out.read_text(encoding="utf-8") == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["in.json", "numbers.json"]


def test_a_symlink_input_or_output_is_refused(tmp_path: Path) -> None:
    real = put(tmp_path, GOOD)
    link = tmp_path / "link.json"
    link.symlink_to(real)
    assert run(link, tmp_path / "o1.json").returncode != 0
    target = tmp_path / "target.json"
    target.write_text("keep", encoding="utf-8")
    out = tmp_path / "o2.json"
    out.symlink_to(target)
    assert run(real, out).returncode != 0
    assert target.read_text(encoding="utf-8") == "keep"


def test_the_guard_bites() -> None:
    # The table above must hold real cases, and a good file must not be in it.
    assert len(BAD) > 20
    assert GOOD not in BAD.values()
