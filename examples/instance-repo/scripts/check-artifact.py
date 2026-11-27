#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Check the shape of the counts artifact the publish job downloaded.

Usage: check-artifact.py DIR

The artifact is data from the job that held the API keys. It may hold exactly
these regular files and nothing else: numbers.json (required), and optionally
numbers.fediverse.json and history/social.jsonl (the folder history holds that
one file). Any other name, any link, any other kind of file, an empty or
unexpected folder, or a missing numbers.json is refused with exit 1.

The check walks the tree without following links and compares whole relative
paths with an exact set, so a crafted name (spaces, a newline, a leading dash,
a long name) cannot match by accident. It never prints a name from the artifact
(a name could carry a workflow command); the messages are fixed words.
"""

from __future__ import annotations

import os
import stat
import sys

ALLOWED_FILES = frozenset(
    {"numbers.json", "numbers.fediverse.json", "history/social.jsonl"}
)
ALLOWED_DIRS = frozenset({"history"})
REQUIRED = "numbers.json"
MAX_ENTRIES = 50


class Bad(Exception):
    pass


def check(root: str) -> None:
    try:
        top = os.lstat(root)
    except OSError:
        raise Bad("the artifact folder cannot be read") from None
    if not stat.S_ISDIR(top.st_mode):
        raise Bad("the artifact is not a folder")
    files: set[str] = set()
    seen = 0
    stack = [""]
    while stack:
        rel = stack.pop()
        here = os.path.join(root, rel) if rel else root
        try:
            with os.scandir(here) as it:
                entries = list(it)
        except OSError:
            raise Bad("the artifact folder cannot be read") from None
        for entry in entries:
            seen += 1
            if seen > MAX_ENTRIES:
                raise Bad("the artifact holds too many entries")
            path = f"{rel}/{entry.name}" if rel else entry.name
            mode = os.lstat(entry.path).st_mode
            if stat.S_ISLNK(mode):
                raise Bad("the artifact must not hold links")
            if stat.S_ISDIR(mode):
                if path not in ALLOWED_DIRS:
                    raise Bad("the artifact holds a folder that is not allowed")
                stack.append(path)
            elif stat.S_ISREG(mode):
                if path not in ALLOWED_FILES:
                    raise Bad("the artifact holds a file that is not allowed")
                files.add(path)
            else:
                raise Bad("the artifact holds a file that is not a regular file")
    if REQUIRED not in files:
        raise Bad("the artifact must hold numbers.json")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-artifact.py DIR", file=sys.stderr)
        return 2
    try:
        check(sys.argv[1])
    except Bad as exc:
        print(f"check-artifact: refused: {exc}", file=sys.stderr)
        return 1
    print("check-artifact: the artifact holds only the expected files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
