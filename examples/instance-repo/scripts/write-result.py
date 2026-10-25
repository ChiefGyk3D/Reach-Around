#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bundle the preview job's results into the one file the comment job reads.

Usage: write-result.py --check-status N [--build-status N] [--check FILE]
                       --out FILE

The file holds the two exit statuses and the engine's `check --format json`
output, as data. It is NOT trusted by the comment job: scripts/render-comment.py
(checked out from the pull request's base commit) validates it against a strict
schema and builds the comment from its own fixed text. The output file is
created new, never over an existing file or through a symlink.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from typing import Any

MAX_CHECK = 65536


def read_check(path: str | None) -> Any:
    """The engine's JSON, or None if there is none or it is not JSON."""
    if not path:
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CHECK:
            return None
        data = os.read(fd, MAX_CHECK)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None


def read_names(path: str | None) -> list[str]:
    """File names from check-repo.sh's output: the lines it indents."""
    if not path:
        return []
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return []
        data = os.read(fd, MAX_CHECK)
    except OSError:
        return []
    finally:
        os.close(fd)
    names: list[str] = []
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith("  ") and line.strip():
            base = os.path.basename(line.strip())
            if base and len(base) <= 200:
                names.append(base)
    return names[:10]


def status(text: str | None, default: int | None) -> int | None:
    if text is None or text == "":
        return default
    try:
        value = int(text)
    except ValueError:
        return 3
    # The renderer accepts a closed set: 0 good, 2 invalid, 1 or 3 failed to run.
    return value if value in (0, 1, 2, 3) else 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check-status")
    parser.add_argument("--build-status")
    parser.add_argument("--check")
    parser.add_argument("--repo-status")
    parser.add_argument("--repo-files")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    repo_status = status(args.repo_status, 3)
    if repo_status == 2:
        repo_status = 3
    result = {
        "check_status": status(args.check_status, 3),
        "build_status": status(args.build_status, None),
        "check": read_check(args.check),
        "repo_status": repo_status,
        "repo_files": read_names(args.repo_files) if repo_status == 1 else [],
    }
    parent = os.path.dirname(os.path.abspath(args.out))
    if os.path.realpath(parent) != parent:
        print(
            "write-result: the output folder is, or sits under, a symlink",
            file=sys.stderr,
        )
        return 2
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(args.out, flags, 0o600)
    except OSError:
        print(
            "write-result: cannot create the output file (it must not exist)",
            file=sys.stderr,
        )
        return 2
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(result, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
