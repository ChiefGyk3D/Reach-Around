#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check a numbers.json strictly and write a clean copy of it.

Usage: validate-numbers.py [--fediverse] --in FILE --out FILE

With --fediverse the file is the fediverse snapshot file the engine writes beside
the numbers file (numbers.fediverse.json), checked with the engine's own limits
and key rules (see "The fediverse snapshot file" at the end of this text).

The counts refresh runs this twice: in the job that holds the API keys, before
the engine's result is copied into the repository, and again in the job that
may write to the repository, after the artifact is downloaded. Neither job
trusts the file; this script is the whole check.

The shape is the one the engine writes (see the engine's numbers file format):

    {"version": 1, "platforms": {"<id>": {"count": N, "as_of": "<UTC stamp>",
                                          "source": "api"}}}

Only that. Exactly those keys, plus one optional key, ``source``, whose only
values are "api" (written by the engine's counts refresh) and "hand"; any other
key or value is refused. The media kit prints the label API only for an entry
that carries source api. A file with no ``source`` stays valid; ids are
lowercase letters, digits, hyphens and underscores (at most 40 characters);
counts are whole numbers from 0 to 10**10; the stamp is a real UTC time written
like 2026-10-24T12:00:00Z, from 2000 up to one day ahead; at most 1000 entries;
at most 64 KiB; no duplicate keys, no NaN. The input must be a regular file,
not a link.

An entry whose stamp is more than 30 days old prints a `::warning::` line naming
the id and the age (never the count) and does not fail the check.

The fediverse snapshot file (--fediverse). The same rules as the engine's own
guard over what it stores, and stricter in one way: the engine DROPS an entry
that fails, this script REFUSES the whole file (fail closed). The shape is
{"version": 1, "platforms": {"bluesky": S, "mastodon": S}} with S one anonymous,
complete snapshot: the keys platform, as_of, interactions (rows of exactly
boosts, favorites, replies) and complete (true) are REQUIRED; followers and
posts (the profile's own counts) are OPTIONAL, as in the engine (a read that
could not get them leaves them out); no other key, and no reason (that belongs
to an incomplete snapshot, which is never stored); platform equals its key;
as_of is a real day; counts are whole numbers within the engine's bounds; at
most 200 rows; at most 512 KiB; no duplicate keys, no NaN, no float, no null.
Freshness is the kit's own rule (apply_collected): an entry whose as_of is more
than 30 days before today or after today is not used by the kit, so it is
DROPPED here, not carried; a file left with no entry is still written (an empty
platforms object), so the committed copy of a removed or long-failing source
ages out instead of staying for ever. The kit also ignores a platform that has
no configured source, whatever the file holds. Every key and every string at any
depth (at most 8 levels) is checked: a person-level key name (handle, email,
user id, ...) or a string that has an identifying shape (a URL, an address, a
DID, a handle or host with dots, an email, an IP address, a channel id, any
space, over 40 characters) is refused. No account, handle, host or link is ever
part of the file; the config names the account. Only this file crosses; the
engine's unit note, its .bad copy and staged files stay behind (the refresh
script does not copy them).

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
from datetime import UTC, date, datetime, timedelta
from typing import Any

MAX_BYTES = 64 * 1024
MAX_ENTRIES = 1000
MAX_COUNT = 10**10
SOURCES = ("api", "hand")
STALE_DAYS = 30
ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
EARLIEST = datetime(2000, 1, 1, tzinfo=UTC)


# The fediverse snapshot file. These values are the engine's own (a test compares
# each one with the engine's constant, so a change there fails here).
FEDIVERSE_WINDOW_DAYS = 30
FEDIVERSE_MAX_BYTES = 512 * 1024
FEDIVERSE_MAX_POSTS = 200
FEDIVERSE_MAX_INTERACTIONS = 10**9
FEDIVERSE_MAX_PROFILE = 10**12
FEDIVERSE_MAX_WORD = 40
FEDIVERSE_MAX_DEPTH = 8
FEDIVERSE_KINDS = ("bluesky", "mastodon")
FEDIVERSE_TOP_KEYS = frozenset(
    {"platform", "as_of", "followers", "posts", "interactions", "complete", "reason"}
)
FEDIVERSE_ROW_KEYS = frozenset({"boosts", "favorites", "replies"})
FEDIVERSE_REQUIRED_KEYS = frozenset({"platform", "as_of", "complete", "interactions"})
FEDIVERSE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_PARTS = (
    "username",
    "displayname",
    "handle",
    "email",
    "mail",
    "userid",
    "channelid",
    "authorid",
    "commenter",
    "commentauthor",
    "viewerid",
    "ipaddress",
    "ipaddr",
    "latitude",
    "longitude",
    "postalcode",
    "zipcode",
    "street",
    "phone",
    "fullname",
    "firstname",
    "lastname",
    "realname",
)
NAME_WHOLE = frozenset(
    {"name", "user", "users", "ip", "ua", "lat", "lng", "lon", "city", "zip", "who"}
)
DOTTED = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
VALUE_SHAPES = (
    re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"),
    re.compile(r"(^|\s)@[A-Za-z0-9_.]{2,}"),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b"),
    re.compile(r"\bUC[0-9A-Za-z_-]{22}\b"),
)


class Bad(Exception):
    pass


def read_regular(path: str, limit: int = MAX_BYTES) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise Bad(f"cannot open the input: {exc.strerror}") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise Bad("the input is not a regular file")
        if info.st_size > limit:
            raise Bad(f"the input is larger than {limit} bytes")
        data = os.read(fd, limit + 1)
    except OSError as exc:
        raise Bad(f"cannot read the input: {exc.strerror}") from None
    finally:
        os.close(fd)
    if len(data) > limit:
        raise Bad(f"the input is larger than {limit} bytes")
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


def load_json(data: bytes) -> Any:
    try:
        return json.loads(
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


def parse(data: bytes, now: datetime) -> dict[str, tuple[int, str, str | None]]:
    doc = load_json(data)
    if not isinstance(doc, dict) or set(doc) != {"version", "platforms"}:
        raise Bad("must be an object with exactly version and platforms")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise Bad("version must be 1")
    platforms = doc["platforms"]
    if not isinstance(platforms, dict):
        raise Bad("platforms must be an object")
    if len(platforms) > MAX_ENTRIES:
        raise Bad(f"more than {MAX_ENTRIES} entries")
    out: dict[str, tuple[int, str, str | None]] = {}
    for key, raw in platforms.items():
        if ID.fullmatch(key) is None:
            raise Bad("an id is not lowercase letters, digits, - or _ (max 40)")
        if (
            not isinstance(raw, dict)
            or not {"count", "as_of"} <= set(raw)
            or set(raw) - {"count", "as_of", "source"}
        ):
            raise Bad("an entry must have count and as_of, and at most source")
        source = raw.get("source")
        if "source" in raw and (type(source) is not str or source not in SOURCES):
            raise Bad("a source is not one of: api, hand")
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
        out[key] = (count, stamp, source)
    return out


def person_name(name: str) -> bool:
    flat = re.sub(r"[^a-z0-9]", "", name.lower())
    return flat in NAME_WHOLE or any(part in flat for part in NAME_PARTS)


def walk(value: Any, depth: int) -> None:
    """Every key and every leaf, however deep: only person-free ints, bools and
    short plain words pass."""
    if depth > FEDIVERSE_MAX_DEPTH:
        raise Bad("a snapshot is nested too deep")
    if isinstance(value, dict):
        for key, inner in value.items():
            if person_name(key):
                raise Bad("a snapshot has a person-level key")
            walk(inner, depth + 1)
    elif isinstance(value, list):
        for inner in value:
            walk(inner, depth + 1)
    elif isinstance(value, str):
        if (
            len(value) > FEDIVERSE_MAX_WORD
            or any(ch.isspace() for ch in value)
            or "://" in value
            or "did:" in value.lower()
            or "@" in value
            or "/" in value
            or DOTTED.search(value)
            or any(shape.search(value) for shape in VALUE_SHAPES)
        ):
            raise Bad("a snapshot holds a value that is not an anonymous aggregate")
    elif type(value) is not int and type(value) is not bool:
        raise Bad("a snapshot holds a value that is not an anonymous aggregate")


def count_ok(value: Any, limit: int) -> bool:
    return type(value) is int and 0 <= value <= limit


def check_snapshot(kind: str, snap: Any) -> None:
    """One complete, anonymous snapshot (the engine's stored form)."""
    if not isinstance(snap, dict):
        raise Bad("a snapshot must be an object")
    walk(snap, 0)
    if (
        set(snap) - FEDIVERSE_TOP_KEYS
        or not FEDIVERSE_REQUIRED_KEYS <= set(snap)
        or snap.get("platform") != kind
    ):
        raise Bad("a snapshot has an unknown or missing key or the wrong platform")
    if snap.get("complete") is not True or "reason" in snap:
        raise Bad("only a complete snapshot is carried")
    day = snap.get("as_of")
    if type(day) is not str or FEDIVERSE_DAY.fullmatch(day) is None:
        raise Bad("a snapshot as_of is not a day like 2026-10-24")
    try:
        datetime.fromisoformat(day)
    except ValueError:
        raise Bad("a snapshot as_of is not a real day") from None
    for key in ("followers", "posts"):
        if key in snap and not count_ok(snap[key], FEDIVERSE_MAX_PROFILE):
            raise Bad("a snapshot profile count is out of range")
    rows = snap.get("interactions")
    if not isinstance(rows, list) or len(rows) > FEDIVERSE_MAX_POSTS:
        raise Bad("a snapshot must hold at most 200 interaction rows")
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != FEDIVERSE_ROW_KEYS
            or not all(count_ok(v, FEDIVERSE_MAX_INTERACTIONS) for v in row.values())
        ):
            raise Bad("an interaction row is not exactly three bounded counts")


def fresh(snap: dict[str, Any], today: date) -> bool:
    """The kit's own rule: as_of from today back to 30 days before it."""
    age = (today - date.fromisoformat(snap["as_of"])).days
    return 0 <= age <= FEDIVERSE_WINDOW_DAYS


def parse_fediverse(data: bytes, today: date) -> dict[str, dict[str, Any]]:
    doc = load_json(data)
    if not isinstance(doc, dict) or set(doc) != {"version", "platforms"}:
        raise Bad("must be an object with exactly version and platforms")
    if type(doc["version"]) is not int or doc["version"] != 1:
        raise Bad("version must be 1")
    platforms = doc["platforms"]
    if not isinstance(platforms, dict) or set(platforms) - set(FEDIVERSE_KINDS):
        raise Bad("platforms must be an object of bluesky and mastodon only")
    for kind, snap in platforms.items():
        check_snapshot(kind, snap)
    return {k: v for k, v in platforms.items() if fresh(v, today)}


def stale_warnings(
    entries: dict[str, tuple[int, str, str | None]], now: datetime
) -> list[str]:
    """One annotation per entry older than STALE_DAYS: the id and the age only.
    The id already passed the ID pattern, so it is safe to print."""
    lines = []
    for key in sorted(entries):
        moment = datetime.fromisoformat(entries[key][1])
        days = (now - moment).days
        if days > STALE_DAYS:
            lines.append(
                f"::warning::validate-numbers: {key} is {days} days old "
                f"(over {STALE_DAYS}); update it by hand or fix its source."
            )
    return lines


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
    parser.add_argument(
        "--fediverse",
        action="store_true",
        help="the input is a numbers.fediverse.json (the engine's snapshot file)",
    )
    parser.add_argument("--in", dest="source", required=True)
    parser.add_argument("--out", dest="dest", required=True)
    args = parser.parse_args()
    try:
        if args.fediverse:
            snaps = parse_fediverse(
                read_regular(args.source, FEDIVERSE_MAX_BYTES), datetime.now(UTC).date()
            )
            body = {
                "version": 1,
                "platforms": {kind: snaps[kind] for kind in sorted(snaps)},
            }
            write_atomic(args.dest, json.dumps(body, indent=2) + "\n")
            print(f"validate-numbers: {len(snaps)} fediverse snapshot(s) are valid.")
            return 0
        now = datetime.now(UTC)
        entries = parse(read_regular(args.source), now)
        platforms: dict[str, dict[str, object]] = {}
        for key in sorted(entries):
            platforms[key] = {"count": entries[key][0], "as_of": entries[key][1]}
            if entries[key][2] is not None:
                platforms[key]["source"] = entries[key][2]
        text = json.dumps({"version": 1, "platforms": platforms}, indent=2) + "\n"
        write_atomic(args.dest, text)
        for line in stale_warnings(entries, now):
            print(line)
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
