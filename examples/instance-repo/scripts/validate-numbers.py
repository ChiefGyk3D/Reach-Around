#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a numbers.json strictly and write a clean copy of it.

Usage: validate-numbers.py --in FILE --out FILE

The counts refresh runs this twice: in the job that holds the API keys, before
the engine's result is copied into the repository, and again in the job that
may write to the repository, after the artifact is downloaded. Neither job
trusts the file; this script is the whole check.

The shape is the one the engine writes (see the engine's numbers file format):

    {"version": 1, "platforms": {"<id>": {"count": N, "as_of": "<UTC stamp>"}}}

Only that. Exactly those keys; ids are lowercase letters, digits, hyphens and
underscores (at most 40 characters); counts are whole numbers from 0 to
10**10; the stamp is a real UTC time written like 2026-10-24T12:00:00Z, from
2000 up to one day ahead; at most 1000 entries; at most 64 KiB; no duplicate
keys, no NaN. The input must be a regular file, not a link.

The output is not a copy of the input. It is written fresh in the engine's own
format (sorted ids, two-space indent) to a new temporary file next to --out and
moved over --out in one rename, so --out is either the old file or the whole
new one. A failure leaves --out untouched and exits with status 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

MAX_BYTES = 64 * 1024
MAX_ENTRIES = 1000
MAX_COUNT = 10**10
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)


class Bad(Exception):
    pass


def read_regular(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise Bad(f"cannot open the input: {exc.strerror}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Bad("the input is not a regular file")
        if info.st_size > MAX_BYTES:
            raise Bad(f"the input is larger than {MAX_BYTES} bytes")
        data = os.read(fd, MAX_BYTES + 1)
    except OSError as exc:
        raise Bad(f"cannot read the input: {exc.strerror}") from None
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise Bad(f"the input is larger than {MAX_BYTES} bytes")
    return data


def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in items:
        if key in out:
            raise Bad("duplicate key")
        out[key] = value
    return out


def refuse_constant(_name: str) -> Any:
    raise Bad("NaN and Infinity are not allowed")


def parse(data: bytes, now: datetime) -> dict[str, tuple[int, str]]:
    try:
        doc = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=refuse_constant,
        )
    except UnicodeDecodeError:
        raise Bad("not valid UTF-8") from None
    except RecursionError:
        raise Bad("nesting is too deep") from None
    except json.JSONDecodeError:
        raise Bad("not valid JSON") from None
    except ValueError as exc:
        if isinstance(exc, Bad):
            raise
        raise Bad("not valid JSON") from None
    if not isinstance(doc, dict) or set(doc) != {"version", "platforms"}:
        raise Bad("must be an object with exactly version and platforms")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise Bad("version must be 1")
    platforms = doc["platforms"]
    if not isinstance(platforms, dict):
        raise Bad("platforms must be an object")
    if len(platforms) > MAX_ENTRIES:
        raise Bad(f"more than {MAX_ENTRIES} entries")
    out: dict[str, tuple[int, str]] = {}
    for key, raw in platforms.items():
        if ID.fullmatch(key) is None:
            raise Bad("an id is not lowercase letters, digits, - or _ (max 40)")
        if not isinstance(raw, dict) or set(raw) != {"count", "as_of"}:
            raise Bad("an entry must have exactly count and as_of")
        count, stamp = raw["count"], raw["as_of"]
        if type(count) is not int or not 0 <= count <= MAX_COUNT:
            raise Bad(f"a count is not a whole number from 0 to {MAX_COUNT}")
        if type(stamp) is not str or STAMP.fullmatch(stamp) is None:
            raise Bad("an as_of is not a UTC stamp like 2026-10-24T12:00:00Z")
        try:
            moment = datetime.fromisoformat(stamp)
        except ValueError:
            raise Bad("an as_of is not a real date and time") from None
        if moment < EARLIEST or moment > now + timedelta(days=1):
            raise Bad("an as_of is outside the allowed range")
        out[key] = (count, stamp)
    return out


def write_atomic(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if os.path.islink(path):
        raise Bad("the output is a symbolic link")
    tmp = os.path.join(directory, f".validate-numbers.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--in", dest="source", required=True)
    parser.add_argument("--out", dest="dest", required=True)
    args = parser.parse_args()
    try:
        entries = parse(read_regular(args.source), datetime.now(UTC))
        platforms = {
            key: {"count": entries[key][0], "as_of": entries[key][1]}
            for key in sorted(entries)
        }
        text = json.dumps({"version": 1, "platforms": platforms}, indent=2) + "\n"
        write_atomic(args.dest, text)
    except Bad as exc:
        print(f"validate-numbers: refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            f"validate-numbers: cannot write the output: {exc.strerror}",
            file=sys.stderr,
        )
        return 1
    print(f"validate-numbers: {len(entries)} entries are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
