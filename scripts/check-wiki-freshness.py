#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail when a wiki page's source files changed after the page was last verified.

The registry `docs/wiki/FRESHNESS.json` maps each wiki page to the source files it
describes and the date (YYYY-MM-DD, UTC) a person last checked the page against them:

    {"pages": {"Costs.md": {"verified": "2026-11-24", "sources": ["a.py"]}}}

A source is stale when its newest commit falls on a UTC date after `verified`. A
source may be a file or a directory. The check also fails for a page that is not
in the registry, a registry page that does not exist, and a missing source path.

To clear a failure, re-read the page against the changed source, fix the page if it
drifted, and set `verified` to today's date in the same pull request.

Usage: python3 -I scripts/check-wiki-freshness.py
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

REGISTRY = "docs/wiki/FRESHNESS.json"
WIKI_DIR = "docs/wiki"


def _last_commit_date(root: Path, source: str) -> datetime.date | None:
    """UTC date of the newest commit touching `source`, or None if it has none."""
    result = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "-C", str(root), "log", "-1", "--format=%ct", "--", source],  # noqa: S607 - git on PATH
        capture_output=True,
        text=True,
        check=False,
    )
    stamp = result.stdout.strip()
    if result.returncode != 0 or not stamp.isdigit():
        return None
    return datetime.datetime.fromtimestamp(int(stamp), datetime.UTC).date()


def _inside(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = (root / value).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def check(root: Path) -> list[str]:
    try:
        data = json.loads((root / REGISTRY).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read {REGISTRY}: {exc}"]
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, dict):
        return [f"{REGISTRY} must be an object with a 'pages' object"]

    problems: list[str] = []
    wiki = root / WIKI_DIR
    on_disk = {p.name for p in wiki.glob("*.md")} if wiki.is_dir() else set()
    for name in sorted(on_disk - set(pages)):
        problems.append(f"{name}: wiki page has no entry in {REGISTRY}")

    for name, entry in sorted(pages.items()):
        if name not in on_disk:
            problems.append(f"{name}: registered but not found in {WIKI_DIR}")
        if not isinstance(entry, dict):
            problems.append(f"{name}: entry must be an object")
            continue
        try:
            verified = datetime.date.fromisoformat(str(entry.get("verified")))
        except ValueError:
            problems.append(f"{name}: 'verified' must be a YYYY-MM-DD date")
            continue
        sources = entry.get("sources")
        if not isinstance(sources, list) or not sources:
            problems.append(f"{name}: 'sources' must be a non-empty list")
            continue
        for source in sources:
            path = _inside(root, source)
            if path is None or not path.exists():
                problems.append(
                    f"{name}: source {source!r} is missing or outside the repo"
                )
                continue
            changed = _last_commit_date(root, source)
            if changed is not None and changed > verified:
                problems.append(
                    f"{name}: {source} changed on {changed}, after the page was "
                    f"verified on {verified}; re-check the page and update 'verified'"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args(argv)
    problems = check(args.root)
    for problem in problems:
        print(f"wiki-freshness: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("wiki-freshness: every wiki page is current against its sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
