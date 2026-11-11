#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Refuse unreviewed or changed assets in the public example asset sets."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _inside(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = (root / value).resolve()
    return path if path.is_relative_to(root.resolve()) else None


def check(root: Path, inventory_path: Path) -> list[str]:
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"cannot read public asset inventory: {exc}"]
    if not isinstance(inventory, dict):
        return ["public asset inventory must be a JSON object"]
    roots = inventory.get("roots")
    files = inventory.get("files")
    if not isinstance(roots, list) or not isinstance(files, dict):
        return ["public asset inventory needs roots and files"]

    problems: list[str] = []
    reviewed: dict[Path, str] = {}
    for relative, digest in files.items():
        path = _inside(root, relative)
        if path is None or not isinstance(digest, str):
            problems.append("inventory file paths and hashes must be strings")
            continue
        reviewed[path] = digest
        if not path.is_file():
            problems.append(f"{relative}: reviewed public placeholder is missing")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            problems.append(
                f"{relative}: does not match its reviewed public placeholder"
            )

    found: set[Path] = set()
    for relative in roots:
        directory = _inside(root, relative)
        if directory is None or not directory.is_dir():
            problems.append(f"{relative}: public asset root is missing or invalid")
            continue
        found.update(
            path.resolve()
            for path in directory.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )
    reviewed_paths = set(reviewed)
    for path in sorted(found - reviewed_paths):
        problems.append(f"{path.relative_to(root)}: unreviewed public asset")
    for path in sorted(reviewed_paths - found):
        problems.append(f"{path.relative_to(root)}: outside the public asset roots")
    return problems


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parent.parent
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--inventory", type=Path)
    args = parser.parse_args(argv)
    inventory = args.inventory or args.root / "public-assets.json"
    problems = check(args.root, inventory)
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print("ok   reviewed public placeholder assets match")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
