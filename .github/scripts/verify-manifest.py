#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare the build manifest's link ids with the LINK_IDS variable.

Run as `python3 -I .github/scripts/verify-manifest.py <manifest>` with LINK_IDS
in the environment (a JSON array). A difference is a workflow warning: clicks on
unlisted ids are rejected by the Worker anyway. Any id outside the closed
grammar ^[a-z0-9-]{1,40}$ (the grammar the workflow enforces for LINK_IDS) is
refused with a fixed error that never echoes the value, so a hostile name in a
downloaded artifact cannot forge a workflow command (a newline plus `::...`).
"""

from __future__ import annotations

import json
import os
import re
import sys

ID = re.compile(r"[a-z0-9-]{1,40}")  # used with fullmatch: `$` would allow "\n"


def escape(text: str) -> str:
    """Escape text emitted after a workflow command (defense in depth: ids are
    already validated, so this should be a no-op)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def refuse(reason: str) -> int:
    print(f"::error::{reason}")
    return 1


def valid_ids(value: object) -> set[str] | None:
    """The ids in a JSON array or object, or None if any is not a valid id."""
    if not isinstance(value, list | dict):
        return None
    names = list(value)
    if not all(isinstance(n, str) and ID.fullmatch(n) for n in names):
        return None
    return set(names)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return refuse("usage: verify-manifest.py <manifest>")
    try:
        with open(argv[1], encoding="utf-8") as handle:
            built = valid_ids(json.load(handle)["links"])
        allowed = valid_ids(json.loads(os.environ["LINK_IDS"]))
    except (OSError, ValueError, KeyError, TypeError):
        return refuse("the manifest or LINK_IDS could not be read")
    if built is None:
        return refuse("the manifest holds a link id outside the allowed grammar")
    if allowed is None:
        return refuse("LINK_IDS holds a value outside the allowed grammar")
    for name in sorted(built - allowed):
        print(f"::warning::link id {escape(name)} is in the build but not in LINK_IDS")
    for name in sorted(allowed - built):
        print(f"::warning::LINK_IDS lists {escape(name)}, which is not in the build")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
