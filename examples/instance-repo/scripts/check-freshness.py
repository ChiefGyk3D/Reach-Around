#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""List the hand-kept measures that are past their stale limit.

Usage: check-freshness.py [--file hand-kept.json] [--limit-days 30]
                          [--today YYYY-MM-DD] [--body FILE]

Reads the hand-kept file of the instance repository (see the engine's
docs/kit.md, "Hand-kept entries and imports") and prints one line with counts.
Only the newest entry of each platform and key is judged, in whole UTC days, the
same rule as the engine's own check. It prints and writes platform names, keys
and ages, never a value or a note.

--body FILE  write a short Markdown list of the stale measures there (used as
             the issue text). Not written when nothing is stale.

When $GITHUB_OUTPUT is set, appends ``stale=true`` or ``stale=false`` to it.

A missing file is not an error: nothing is kept by hand, so nothing is stale.
The file is read strictly (a regular file, never a link, at most 1 MiB, exactly
the keys the engine writes, no duplicate keys, no NaN); a file that fails exits 2
and prints a fixed message. Exit codes: 0 the check ran (fresh or stale; read
the output), 2 the file or an argument is bad.

Standard library only; no network; no secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import UTC, date, datetime
from typing import Any

MAX_BYTES = 1024 * 1024
MAX_ENTRIES = 2000
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
KEY = re.compile(r"^[a-z0-9_]{1,64}$")


class Bad(Exception):
    pass


def read_regular(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise Bad(f"cannot open the file: {exc.strerror}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Bad("the file is not a regular file")
        if info.st_size > MAX_BYTES:
            raise Bad(f"the file is larger than {MAX_BYTES} bytes")
        data = os.read(fd, MAX_BYTES + 1)
    except OSError as exc:
        raise Bad(f"cannot read the file: {exc.strerror}") from None
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise Bad(f"the file is larger than {MAX_BYTES} bytes")
    return data


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise Bad("the file repeats a key")
        out[key] = value
    return out


def refuse_constant(_name: str) -> Any:
    raise Bad("NaN and Infinity are not allowed")


def parse_day(value: Any) -> date:
    if not isinstance(value, str) or not DAY.match(value):
        raise Bad("as_of must be a day written YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Bad("as_of must be a real day") from None


def parse(data: bytes) -> list[tuple[str, str, date]]:
    try:
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=refuse_constant,
        )
    except UnicodeDecodeError:
        raise Bad("the file is not valid UTF-8") from None
    except RecursionError:
        raise Bad("the file is nested too deeply") from None
    except json.JSONDecodeError:
        raise Bad("the file is not valid JSON") from None
    except ValueError as exc:
        if isinstance(exc, Bad):
            raise
        raise Bad("the file is not valid JSON") from None
    if not isinstance(doc, dict) or set(doc) != {"version", "entries"}:
        raise Bad("the file must hold exactly version and entries")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise Bad("version must be 1")
    entries = doc["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise Bad(f"entries must be a list of at most {MAX_ENTRIES}")
    found = []
    for entry in entries:
        if not isinstance(entry, dict) or not {
            "platform",
            "key",
            "value",
            "as_of",
        } <= set(entry):
            raise Bad("an entry is missing a key")
        platform, key = entry["platform"], entry["key"]
        if not isinstance(platform, str) or not ID.match(platform):
            raise Bad("an entry has a bad platform")
        if not isinstance(key, str) or not KEY.match(key):
            raise Bad("an entry has a bad key")
        found.append((platform, key, parse_day(entry["as_of"])))
    return found


def stale(
    entries: list[tuple[str, str, date]], today: date, limit: int
) -> list[tuple[str, str, int]]:
    newest: dict[tuple[str, str], date] = {}
    for platform, key, day in entries:
        slot = (platform, key)
        if slot not in newest or day > newest[slot]:
            newest[slot] = day
    out = [
        (platform, key, (today - day).days)
        for (platform, key), day in newest.items()
        if (today - day).days > limit
    ]
    return sorted(out, key=lambda item: (-item[2], item[0], item[1]))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", default="hand-kept.json")
    ap.add_argument("--limit-days", type=int, default=30)
    ap.add_argument("--today", help="UTC day to judge against (default: today)")
    ap.add_argument("--body", help="write the issue text here when something is stale")
    args = ap.parse_args(argv)
    if not 0 <= args.limit_days <= 3650:
        print("error: --limit-days must be 0 to 3650", file=sys.stderr)
        return 2
    try:
        today = parse_day(args.today) if args.today else datetime.now(UTC).date()
    except Bad:
        print("error: --today must be a day written YYYY-MM-DD", file=sys.stderr)
        return 2
    try:
        try:
            entries = parse(read_regular(args.file))
        except FileNotFoundError:
            entries = []
    except Bad as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    old = stale(entries, today, args.limit_days)
    print(
        f"hand-kept measures: {len(entries)} entries, {len(old)} past "
        f"{args.limit_days} days"
    )
    for platform, key, days in old:
        print(f"stale: {platform} {key} {days} days")
    if old and args.body:
        lines = [
            f"These hand-kept measures are more than {args.limit_days} days old "
            "(checked against the newest entry of each).",
            "",
            *(f"- {platform} `{key}`: {days} days" for platform, key, days in old),
            "",
            "Update them with `reach-around kit measure add` or a new export "
            "import, commit `hand-kept.json`, and this issue closes itself on "
            "the next run.",
        ]
        with open(args.body, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"stale={'true' if old else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
