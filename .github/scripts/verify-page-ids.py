#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the built site's tab ids with the PAGE_IDS variable.

Run as `python3 -I .github/scripts/verify-page-ids.py <site-dir>` with PAGE_IDS
in the environment (a JSON array). The Worker ignores a beacon for a page id it
was not told about, so a tab in the build but not in PAGE_IDS is an error: its
views would be silently lost. An id in PAGE_IDS that the build lacks is a
warning. Every id must fit the closed grammar the Worker uses; a value outside
it is refused with a fixed message that never echoes it, so a hostile name in a
downloaded artifact cannot forge a workflow command.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")  # used with fullmatch: `$` would allow "\n"
ATTR = re.compile(r'data-page-id="([^"]{1,80})"')
MAX_PAGES = 200
MAX_BYTES = 2_000_000


def refuse(reason: str) -> int:
    print(f"::error::{reason}")
    return 1


def built_ids(site: Path) -> set[str] | None:
    """The page ids on the site's html pages, or None if any is outside the grammar."""
    found: set[str] = set()
    for path in sorted(site.rglob("*.html"))[:MAX_PAGES]:
        if path.is_symlink() or not path.is_file():
            continue
        with path.open(encoding="utf-8", errors="replace") as handle:
            text = handle.read(MAX_BYTES)
        for value in ATTR.findall(text):
            if ID.fullmatch(value) is None:
                return None
            found.add(value)
    return found


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return refuse("usage: verify-page-ids.py <site-dir>")
    try:
        allowed_raw = json.loads(os.environ["PAGE_IDS"])
    except (ValueError, KeyError):
        return refuse("PAGE_IDS could not be read")
    if not isinstance(allowed_raw, list) or not all(
        isinstance(n, str) and ID.fullmatch(n) for n in allowed_raw
    ):
        return refuse("PAGE_IDS holds a value outside the allowed grammar")
    site = Path(argv[1])
    if not site.is_dir():
        return refuse("the site directory is missing")
    built = built_ids(site)
    if built is None:
        return refuse("the build holds a page id outside the allowed grammar")
    if not built:
        return refuse("the build carries no page id: page views are not on in the build")
    allowed = set(allowed_raw)
    missing = sorted(built - allowed)
    for name in sorted(allowed - built):
        print(f"::warning::PAGE_IDS lists {name}, which is not in the build")
    if missing:
        return refuse(
            "the build has tab ids missing from PAGE_IDS (their views would be "
            "ignored): " + ", ".join(missing)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
