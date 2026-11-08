#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare a Beacons daily export with Reach Around's own daily numbers.

Usage: python3 -I scripts/check-analytics.py --beacons FILE --ours FILE
       [--since DAY] [--until DAY] [--tolerance-pct N] [--tolerance-abs N]
       [--shift-days N] [--json]

Both files are daily series, CSV or JSON, one row per UTC day:

  day     YYYY-MM-DD (required)
  views   whole number >= 0 (optional)
  clicks  whole number >= 0 (optional)

At least one of ``views`` and ``clicks`` must be present in both files; a
metric present in only one file is not compared (it is named in the output).
JSON is a list of row objects or an object with a ``days`` list. Extra columns
are ignored. Nothing else about the Beacons export is assumed: map it to this
shape first (the importer, issue #376, does that).

Read-only and offline: it reads the two named files and prints a table. It
opens no network connection, writes no file and needs no credential.

Rules: a day in the window that one side lacks is a GAP (reported, never
treated as zero). A day passes when the difference is within ``--tolerance-abs``
OR within ``--tolerance-pct`` of the larger side. When the series fail but one
matches the other shifted by one day, a hint names it (the usual cause is a
day boundary in a time zone other than UTC); ``--shift-days`` applies a shift to
the Beacons days on purpose.

Exit: 0 every compared day passes, 1 a mismatch or a gap, 2 usage or bad input.
Errors name the file, the row and the field, never the value.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import stat
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
MAX_WINDOW_DAYS = 3660
METRICS = ("views", "clicks")
MIN_DAY = date(2000, 1, 1)


class InputError(Exception):
    """Bad input; the message never repeats a value from the file."""


def _no_dupes(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in pairs:
        if k in out:
            raise InputError("JSON object repeats a key")
        out[k] = v
    return out


def _bad_const(_name: str) -> object:
    raise InputError("JSON holds NaN or Infinity")


def _read_text(path: Path, label: str) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise InputError(
            f"{label}: cannot open (missing, unreadable or a symlink)"
        ) from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise InputError(f"{label}: not a regular file")
        if st.st_size > MAX_BYTES:
            raise InputError(f"{label}: larger than {MAX_BYTES} bytes")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            raw = fh.read(MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise InputError(f"{label}: larger than {MAX_BYTES} bytes")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InputError(f"{label}: not UTF-8") from None


def _parse_day(text: object, label: str, row: int) -> date:
    if not isinstance(text, str) or len(text) != 10:
        raise InputError(f"{label}: row {row}: day is not YYYY-MM-DD")
    try:
        d = date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{label}: row {row}: day is not YYYY-MM-DD") from None
    if d < MIN_DAY or text != d.isoformat():
        raise InputError(f"{label}: row {row}: day is not YYYY-MM-DD")
    return d


def _parse_count(value: object, label: str, row: int, field: str) -> int:
    if isinstance(value, bool):
        raise InputError(f"{label}: row {row}: {field} is not a whole number")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        n = int(value.strip())
    else:
        raise InputError(f"{label}: row {row}: {field} is not a whole number")
    if n < 0 or n > 10**12:
        raise InputError(f"{label}: row {row}: {field} is out of range")
    return n


def load_series(path: Path, label: str) -> dict[date, dict[str, int]]:
    """Return {day: {metric: count}} from a CSV or JSON file."""
    text = _read_text(path, label)
    rows: list[dict[str, object]]
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            data = json.loads(
                text, object_pairs_hook=_no_dupes, parse_constant=_bad_const
            )
        except InputError as e:
            raise InputError(f"{label}: {e}") from None
        except (ValueError, RecursionError):
            raise InputError(f"{label}: not valid JSON") from None
        if isinstance(data, dict):
            data = data.get("days")
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise InputError(f"{label}: expected a list of day objects")
        rows = data
    elif suffix == ".csv":
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            rows = []
            for r in reader:
                if None in r or None in r.values():
                    raise InputError(
                        f"{label}: row {len(rows) + 1}: wrong number of columns"
                    )
                rows.append(dict(r))
            header = reader.fieldnames or []
            if len(set(header)) != len(header):
                raise InputError(f"{label}: header repeats a column")
        except csv.Error:
            raise InputError(f"{label}: not valid CSV") from None
        if "day" not in header:
            raise InputError(f"{label}: header has no day column")
    else:
        raise InputError(f"{label}: file name must end in .csv or .json")
    if len(rows) > MAX_ROWS:
        raise InputError(f"{label}: more than {MAX_ROWS} rows")
    if not rows:
        raise InputError(f"{label}: no rows")

    series: dict[date, dict[str, int]] = {}
    seen_metrics: set[str] = set()
    for i, r in enumerate(rows, 1):
        if "day" not in r:
            raise InputError(f"{label}: row {i}: missing day")
        d = _parse_day(r["day"], label, i)
        if d in series:
            raise InputError(f"{label}: row {i}: day appears twice")
        entry: dict[str, int] = {}
        for m in METRICS:
            if m in r and r[m] not in (None, ""):
                entry[m] = _parse_count(r[m], label, i, m)
        if not entry:
            raise InputError(f"{label}: row {i}: no views or clicks value")
        if seen_metrics and set(entry) != seen_metrics:
            raise InputError(f"{label}: row {i}: metrics differ from earlier rows")
        seen_metrics = set(entry)
        series[d] = entry
    return series


def _cli_day(text: str, name: str) -> date:
    try:
        d = date.fromisoformat(text)
    except ValueError:
        raise InputError(f"{name} is not YYYY-MM-DD") from None
    if d < MIN_DAY or text != d.isoformat():
        raise InputError(f"{name} is not YYYY-MM-DD")
    return d


def shift_days(
    series: dict[date, dict[str, int]], n: int, label: str
) -> dict[date, dict[str, int]]:
    """Move every day by n days; a day past the calendar's end is bad input."""
    try:
        return {d + timedelta(days=n): v for d, v in series.items()}
    except OverflowError:
        raise InputError(f"{label}: a shifted day is out of range") from None


def _passes(a: int, b: int, tol_abs: float, tol_pct: float) -> bool:
    diff = abs(a - b)
    if diff <= tol_abs:
        return True
    big = max(a, b)
    return big > 0 and diff * 100 <= tol_pct * big


def compare(
    beacons: dict[date, dict[str, int]],
    ours: dict[date, dict[str, int]],
    since: date | None,
    until: date | None,
    tol_abs: float,
    tol_pct: float,
) -> dict[str, Any]:
    lo = since or min(beacons)
    hi = until or max(beacons)
    if hi < lo:
        raise InputError("window ends before it starts")
    if (hi - lo).days + 1 > MAX_WINDOW_DAYS:
        raise InputError(f"window is longer than {MAX_WINDOW_DAYS} days")
    b_metrics = set(next(iter(beacons.values())))
    o_metrics = set(next(iter(ours.values())))
    shared = [m for m in METRICS if m in b_metrics and m in o_metrics]
    if not shared:
        raise InputError("the two files share no metric (views or clicks)")
    skipped = [m for m in METRICS if (m in b_metrics) != (m in o_metrics)]

    rows: list[dict[str, object]] = []
    gaps: list[dict[str, str]] = []
    for offset in range((hi - lo).days + 1):
        d = lo + timedelta(days=offset)
        in_b, in_o = d in beacons, d in ours
        if not (in_b and in_o):
            gaps.append(
                {"day": d.isoformat(), "missing": "beacons" if not in_b else "ours"}
                if in_b or in_o
                else {"day": d.isoformat(), "missing": "both"}
            )
        else:
            for m in shared:
                a, b = beacons[d][m], ours[d][m]
                rows.append(
                    {
                        "day": d.isoformat(),
                        "metric": m,
                        "beacons": a,
                        "ours": b,
                        "diff": b - a,
                        "pass": _passes(a, b, tol_abs, tol_pct),
                    }
                )
    return {
        "window": [lo.isoformat(), hi.isoformat()],
        "metrics": shared,
        "not_compared": skipped,
        "rows": rows,
        "gaps": gaps,
    }


def _shift_hint(
    beacons: dict[date, dict[str, int]],
    ours: dict[date, dict[str, int]],
    since: date | None,
    until: date | None,
    tol_abs: float,
    tol_pct: float,
) -> str | None:
    """Name a one-day shift that makes every overlapping day pass, if any."""
    for n in (-1, 1):
        try:
            moved = shift_days(beacons, n, "beacons file")
            res = compare(moved, ours, since, until, tol_abs, tol_pct)
        except InputError:
            continue
        rows = res["rows"]
        if len(rows) >= 2 and all(r["pass"] for r in rows):
            return (
                f"the series match when the Beacons days move {n:+d} day "
                f"(use --shift-days {n:+d} if its day boundary is not UTC)"
            )
    return None


def render(res: dict[str, Any], hint: str | None) -> tuple[str, int]:
    rows = res["rows"]
    gaps = res["gaps"]
    lines = [f"Window (UTC days): {res['window'][0]} to {res['window'][1]}"]
    lines.append(
        f"{'day':<12}{'metric':<8}{'beacons':>10}{'ours':>10}{'diff':>8}  result"
    )
    for r in rows:
        lines.append(
            f"{r['day']:<12}{r['metric']:<8}{r['beacons']:>10}{r['ours']:>10}"
            f"{r['diff']:>+8}  {'PASS' if r['pass'] else 'FAIL'}"
        )
    for g in gaps:
        lines.append(f"{g['day']:<12}{'-':<8}{'':>28}  GAP (missing in {g['missing']})")
    failed = sum(1 for r in rows if not r["pass"])
    lines.append(
        f"Summary: {len(rows) - failed} pass, {failed} fail, {len(gaps)} gap day(s)"
    )
    nc = res["not_compared"]
    if nc:
        lines.append(f"Not compared (in only one file): {', '.join(nc)}")
    if hint and (failed or gaps):
        lines.append(f"Hint: {hint}")
    return "\n".join(lines), (1 if failed or gaps else 0)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--beacons", required=True, metavar="FILE")
    ap.add_argument("--ours", required=True, metavar="FILE")
    ap.add_argument("--since", metavar="DAY")
    ap.add_argument("--until", metavar="DAY")
    ap.add_argument("--tolerance-pct", type=float, default=10.0, metavar="N")
    ap.add_argument("--tolerance-abs", type=float, default=5.0, metavar="N")
    ap.add_argument("--shift-days", type=int, default=0, metavar="N")
    ap.add_argument("--json", action="store_true")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    try:
        for name, v in (
            ("--tolerance-pct", args.tolerance_pct),
            ("--tolerance-abs", args.tolerance_abs),
        ):
            if not (0 <= v <= 1000) or v != v:
                raise InputError(f"{name} must be between 0 and 1000")
        if abs(args.shift_days) > 3:
            raise InputError("--shift-days must be between -3 and 3")
        since = _cli_day(args.since, "--since") if args.since else None
        until = _cli_day(args.until, "--until") if args.until else None
        beacons = load_series(Path(args.beacons), "beacons file")
        ours = load_series(Path(args.ours), "ours file")
        if args.shift_days:
            beacons = shift_days(beacons, args.shift_days, "beacons file")
        res = compare(
            beacons, ours, since, until, args.tolerance_abs, args.tolerance_pct
        )
        hint = None
        if not args.shift_days:
            hint = _shift_hint(
                beacons, ours, since, until, args.tolerance_abs, args.tolerance_pct
            )
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    text, code = render(res, hint)
    if args.json:
        print(json.dumps({**res, "hint": hint, "exit": code}, sort_keys=True))
    else:
        print(text)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
