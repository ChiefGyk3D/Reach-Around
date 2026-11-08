# SPDX-License-Identifier: Apache-2.0
"""scripts/check-analytics.py: Beacons export versus Reach Around daily numbers.

Fixtures are synthetic daily series written to a temp directory.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-analytics.py"

Rows = list[tuple[str, int, int]]


def write_csv(path: Path, rows: Rows) -> Path:
    path.write_text(
        "day,views,clicks\n" + "".join(f"{d},{v},{c}\n" for d, v, c in rows)
    )
    return path


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, synthetic input
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        check=False,
    )


BASE: Rows = [("2026-11-01", 100, 20), ("2026-11-02", 120, 30), ("2026-11-03", 90, 10)]


def pair(tmp_path: Path, b: Rows, o: Rows) -> list[str]:
    return [
        "--beacons",
        str(write_csv(tmp_path / "b.csv", b)),
        "--ours",
        str(write_csv(tmp_path / "o.csv", o)),
    ]


def test_match_within_tolerance_passes(tmp_path: Path) -> None:
    ours: Rows = [
        ("2026-11-01", 97, 20),
        ("2026-11-02", 118, 28),
        ("2026-11-03", 90, 10),
    ]
    r = run(tmp_path, *pair(tmp_path, BASE, ours))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS" in r.stdout and "FAIL" not in r.stdout
    assert "0 gap day(s)" in r.stdout


def test_mismatch_fails_and_names_day(tmp_path: Path) -> None:
    ours: Rows = [
        ("2026-11-01", 100, 20),
        ("2026-11-02", 40, 30),
        ("2026-11-03", 90, 10),
    ]
    r = run(tmp_path, *pair(tmp_path, BASE, ours))
    assert r.returncode == 1
    assert re.search(r"2026-11-02\s+views\s+120\s+40\s+-80\s+FAIL", r.stdout)


def test_tolerance_flags_are_honored(tmp_path: Path) -> None:
    ours: Rows = [
        ("2026-11-01", 60, 20),
        ("2026-11-02", 120, 30),
        ("2026-11-03", 90, 10),
    ]
    args = pair(tmp_path, BASE, ours)
    assert run(tmp_path, *args).returncode == 1
    assert run(tmp_path, *args, "--tolerance-pct", "50").returncode == 0


def test_missing_day_is_a_gap_not_zero(tmp_path: Path) -> None:
    ours: Rows = [("2026-11-01", 100, 20), ("2026-11-03", 90, 10)]
    r = run(tmp_path, *pair(tmp_path, BASE, ours))
    assert r.returncode == 1
    assert re.search(r"2026-11-02.*GAP \(missing in ours\)", r.stdout)
    assert "FAIL" not in r.stdout


def test_missing_in_beacons_and_both(tmp_path: Path) -> None:
    b: Rows = [("2026-11-01", 100, 20), ("2026-11-04", 5, 1)]
    o: Rows = [("2026-11-01", 100, 20), ("2026-11-02", 5, 1), ("2026-11-04", 5, 1)]
    r = run(tmp_path, *pair(tmp_path, b, o))
    assert r.returncode == 1
    assert "2026-11-02" in r.stdout and "missing in beacons" in r.stdout
    assert "2026-11-03" in r.stdout and "missing in both" in r.stdout


def test_window_limits_the_comparison(tmp_path: Path) -> None:
    ours: Rows = [("2026-11-01", 100, 20), ("2026-11-02", 1, 1), ("2026-11-03", 90, 10)]
    args = pair(tmp_path, BASE, ours)
    assert run(tmp_path, *args).returncode == 1
    r = run(tmp_path, *args, "--since", "2026-11-03", "--until", "2026-11-03")
    assert r.returncode == 0 and "2026-11-02" not in r.stdout


def test_utc_off_by_one_is_detected_and_fixable(tmp_path: Path) -> None:
    # Beacons counted days in a local zone: its numbers sit one day late.
    b: Rows = [
        ("2026-11-02", 100, 20),
        ("2026-11-03", 300, 50),
        ("2026-11-04", 200, 40),
    ]
    o: Rows = [
        ("2026-11-01", 100, 20),
        ("2026-11-02", 300, 50),
        ("2026-11-03", 200, 40),
    ]
    args = pair(tmp_path, b, o)
    r = run(tmp_path, *args, "--since", "2026-11-02", "--until", "2026-11-03")
    assert r.returncode == 1
    assert "--shift-days -1" in r.stdout
    fixed = run(tmp_path, *args, "--shift-days", "-1")
    assert fixed.returncode == 0, fixed.stdout


def test_json_inputs_and_json_output(tmp_path: Path) -> None:
    b = tmp_path / "b.json"
    o = tmp_path / "o.json"
    b.write_text(
        json.dumps({"days": [{"day": "2026-11-01", "views": 10, "extra": "x"}]})
    )
    o.write_text(json.dumps([{"day": "2026-11-01", "views": "11"}]))
    r = run(tmp_path, "--beacons", str(b), "--ours", str(o), "--json")
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["rows"][0]["diff"] == 1 and out["exit"] == 0


def test_metric_in_one_file_only_is_named_not_compared(tmp_path: Path) -> None:
    b = tmp_path / "b.csv"
    b.write_text("day,views\n2026-11-01,10\n")
    o = write_csv(tmp_path / "o.csv", [("2026-11-01", 10, 4)])
    r = run(tmp_path, "--beacons", str(b), "--ours", str(o))
    assert r.returncode == 0
    assert "Not compared (in only one file): clicks" in r.stdout


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("b.csv", "day,views\n2026-13-01,5\n"),
        ("b.csv", "day,views\n2026-11-1,5\n"),
        ("b.csv", "day,views\n2026-11-01,-5\n"),
        ("b.csv", "day,views\n2026-11-01,1.5\n"),
        ("b.csv", "day,views\n2026-11-01,5\n2026-11-01,6\n"),
        ("b.csv", "views\n5\n"),
        ("b.csv", "day,views,views\n2026-11-01,5,6\n"),
        ("b.csv", "day,views\n2026-11-01,5,9\n"),
        ("b.csv", "day,views\n"),
        ("b.csv", ""),
        ("b.json", "{not json"),
        ("b.json", '{"days": [{"day": "2026-11-01", "views": 1, "views": 2}]}'),
        ("b.json", '{"days": [{"day": "2026-11-01", "views": NaN}]}'),
        ("b.json", '{"days": [{"day": "2026-11-01", "views": true}]}'),
        ("b.json", '"just a string"'),
        ("b.txt", "day,views\n2026-11-01,5\n"),
    ],
)
def test_malformed_input_exits_2(tmp_path: Path, name: str, content: str) -> None:
    b = tmp_path / name
    b.write_text(content)
    o = write_csv(tmp_path / "o.csv", [("2026-11-01", 5, 1)])
    r = run(tmp_path, "--beacons", str(b), "--ours", str(o))
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stdout == ""
    assert r.stderr.startswith("error: beacons file")


def test_bad_value_is_not_echoed(tmp_path: Path) -> None:
    b = tmp_path / "b.csv"
    b.write_text("day,views\n2026-11-01,zzmarkerzz\n")
    r = run(
        tmp_path,
        "--beacons",
        str(b),
        "--ours",
        str(write_csv(tmp_path / "o.csv", BASE)),
    )
    assert r.returncode == 2 and "zzmarkerzz" not in r.stderr + r.stdout


def test_binary_missing_symlink_and_directory_refused(tmp_path: Path) -> None:
    o = write_csv(tmp_path / "o.csv", BASE)
    bad = tmp_path / "bin.csv"
    bad.write_bytes(b"day,views\n\xff\xfe\n")
    assert run(tmp_path, "--beacons", str(bad), "--ours", str(o)).returncode == 2
    assert (
        run(
            tmp_path, "--beacons", str(tmp_path / "nope.csv"), "--ours", str(o)
        ).returncode
        == 2
    )
    link = tmp_path / "link.csv"
    link.symlink_to(o)
    assert run(tmp_path, "--beacons", str(link), "--ours", str(o)).returncode == 2
    d = tmp_path / "dir.csv"
    d.mkdir()
    assert run(tmp_path, "--beacons", str(d), "--ours", str(o)).returncode == 2


def test_bad_options_exit_2(tmp_path: Path) -> None:
    args = pair(tmp_path, BASE, BASE)
    assert run(tmp_path, *args, "--since", "yesterday").returncode == 2
    assert run(tmp_path, *args, "--tolerance-pct", "nan").returncode == 2
    assert run(tmp_path, *args, "--shift-days", "9").returncode == 2
    assert (
        run(
            tmp_path, *args, "--since", "2026-11-03", "--until", "2026-11-01"
        ).returncode
        == 2
    )
    assert run(tmp_path, "--beacons", args[1]).returncode == 2


def test_read_only_and_offline(tmp_path: Path) -> None:
    args = pair(tmp_path, BASE, BASE)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert run(tmp_path, *args).returncode == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    src = SCRIPT.read_text()
    for banned in ("socket", "urllib", "http", "requests", "subprocess", "sqlite3"):
        assert not re.search(rf"^\s*(import|from)\s+{banned}\b", src, re.M), banned
    assert "write_text" not in src and ".write(" not in src


def test_shift_past_the_calendar_end_exits_2_not_a_traceback(tmp_path: Path) -> None:
    rows: Rows = [("9999-12-31", 5, 1)]
    args = pair(tmp_path, rows, rows)
    r = run(tmp_path, *args, "--shift-days", "3")
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert r.stderr.startswith("error: ")
    # No shift: the last calendar day still compares (and the hint path is safe).
    ok = run(tmp_path, *args)
    assert ok.returncode == 0, ok.stderr
    other: Rows = [("9999-12-31", 500, 1)]
    bad = run(tmp_path, *pair(tmp_path, rows, other))
    assert bad.returncode == 1 and "Traceback" not in bad.stderr
