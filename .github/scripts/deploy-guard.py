#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Small, tokenless helpers for the Worker deploy workflows (issue #768).

Run as `python3 -I .github/scripts/deploy-guard.py <command> ...`. They read
JSON that `wrangler ... --json` printed to a file and print one fixed-shape
value. Nothing here touches the network or a secret, and no input is echoed
back into a workflow command (every refusal is a fixed message).

  active-version FILE        print the version id that holds the most traffic in
                             `wrangler deployments status --json` output, or
                             nothing when the output has none (a first deploy).
  link-ids FILE              print the LINK_IDS plain-text binding found in
                             `wrangler versions view <id> --json` output as a
                             compact JSON array, or nothing when it has none.
  union OLD_JSON NEW_JSON    print the compact JSON array holding every id of
                             both lists (old ones first), or exit 3 when the
                             union would pass the Worker's cap of 200 ids.
  owner-check FILE REPO      exit 4 when a recent deployment message names a
                             different repository as the Worker's owner.

The shape of wrangler's JSON is INFERRED, not documented
(https://developers.cloudflare.com/workers/wrangler/commands/workers/ , read
2026-11-24, lists the flags but not the fields). The readers therefore search
the whole document for the field they need and print nothing when it is not
there; the workflow treats "nothing" as "unknown" and says so in the log.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
LINK_ID = re.compile(r"[a-z0-9-]{1,40}")
MAX_LINK_IDS = (
    200  # worker/src/sites.ts MAX_LINKS, the grammar deploy-test.yml enforces
)
OWNER = re.compile(r"reach-around-owner=([A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100})")
REPO = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
MAX_BYTES = 2_000_000


def load(path: str) -> Any:
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_BYTES:
        return None
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def walk(node: Any) -> Iterator[Any]:
    """Every value in the document, depth first, in document order."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def active_version(doc: Any) -> str:
    best = ""
    best_pct = -1.0
    for node in walk(doc):
        if not isinstance(node, dict):
            continue
        version = node.get("version_id", node.get("versionId"))
        if not isinstance(version, str) or not UUID.fullmatch(version):
            continue
        pct = node.get("percentage", 100)
        pct = (
            float(pct)
            if isinstance(pct, (int, float)) and not isinstance(pct, bool)
            else 0.0
        )
        if pct > best_pct:
            best, best_pct = version, pct
    return best


def parse_ids(text: object) -> list[str] | None:
    if not isinstance(text, str):
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, list) or len(value) > MAX_LINK_IDS:
        return None
    if not all(isinstance(item, str) and LINK_ID.fullmatch(item) for item in value):
        return None
    return list(value)


def link_ids(doc: Any) -> list[str] | None:
    for node in walk(doc):
        if isinstance(node, dict) and node.get("name") == "LINK_IDS":
            ids = parse_ids(node.get("text"))
            if ids is not None:
                return ids
    return None


def union(old: list[str], new: list[str]) -> list[str] | None:
    merged = list(dict.fromkeys([*old, *new]))
    return merged if len(merged) <= MAX_LINK_IDS else None


def compact(ids: list[str]) -> str:
    return json.dumps(ids, separators=(",", ":"))


def owner_conflict(doc: Any, repo: str) -> bool:
    for node in walk(doc):
        if isinstance(node, str):
            for match in OWNER.finditer(node):
                if match.group(1).lower() != repo.lower():
                    return True
    return False


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "active-version" and len(argv) == 3:
        print(active_version(load(argv[2])))
        return 0
    if cmd == "link-ids" and len(argv) == 3:
        ids = link_ids(load(argv[2]))
        print("" if ids is None else compact(ids))
        return 0
    if cmd == "union" and len(argv) == 4:
        old, new = parse_ids(argv[2]), parse_ids(argv[3])
        if old is None or new is None:
            print("::error::union needs two JSON arrays of valid link ids")
            return 2
        merged = union(old, new)
        if merged is None:
            print(
                "::notice::old and new link ids together pass the cap of 200; "
                "deploying the new list only"
            )
            return 3
        print(compact(merged))
        return 0
    if cmd == "owner-check" and len(argv) == 4:
        if not REPO.fullmatch(argv[3]):
            print("::error::owner-check needs an owner/name repository")
            return 2
        if owner_conflict(load(argv[2]), argv[3]):
            print(
                "::error::a recent deployment of this Worker was made for a "
                "different repository. One Worker has one deploying repository "
                "(docs/deploy.md, Single owner)."
            )
            return 4
        return 0
    print(
        "usage: deploy-guard.py active-version|link-ids|union|owner-check ...",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
