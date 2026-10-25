# SPDX-License-Identifier: Apache-2.0
"""``reach-around deploy-purge-list --previous OLD --current NEW``.

Prints, one per line, the URL paths a host cache may still hold in an old state
after a deploy: files the new build dropped, stable-name files whose bytes
changed, and the entry points (every page, ``/404.html``, ``/robots.txt``). The
two inputs are the ``<out>.assets.json`` files the build writes. Hashed files
that still exist need no purge: a changed file has a new name.

The output is a backstop for the deploy workflow's optional purge step (see
docs/deploy.md); correctness does not depend on it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reach_around.site.inventory import InventoryError, load_inventory, purge_paths


def _run(args: argparse.Namespace) -> int:
    try:
        previous = load_inventory(args.previous)
        current = load_inventory(args.current)
    except InventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in purge_paths(previous, current):
        print(path)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    purge = subparsers.add_parser(
        "deploy-purge-list",
        help="list the URL paths to purge from the host cache after a deploy",
    )
    purge.add_argument(
        "--previous",
        type=Path,
        required=True,
        help="assets inventory of the previous deployment",
    )
    purge.add_argument(
        "--current",
        type=Path,
        required=True,
        help="assets inventory of this build (<out>.assets.json)",
    )
    purge.set_defaults(func=_run)
