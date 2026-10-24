# SPDX-License-Identifier: Apache-2.0
"""``reach-around store status|migrate --config PATH``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from reach_around.config import ConfigError, load_site_config
from reach_around.store import SqliteStore, StoreError, open_store


def _label(version: int) -> str:
    return f"{version:04d}"


def _run(args: argparse.Namespace) -> int:
    try:
        config = load_site_config(args.config)
        if args.store_command == "status":
            if config.store.kind != "sqlite":
                raise StoreError("the postgres store is not available in this build")
            path = Path(config.store.path)
            if not path.exists():
                print("applied: none (no database yet)")
                pending = SqliteStore(":memory:").migration_status().pending
                print("pending: " + (", ".join(map(_label, pending)) or "none"))
                return 0
            state = SqliteStore(path).migration_status()
            print("applied: " + (", ".join(map(_label, state.applied)) or "none"))
            print("pending: " + (", ".join(map(_label, state.pending)) or "none"))
            return 0
        store = open_store(config.store, os.environ, migrate=False)
        before = store.migration_status()
        highest = store.migrate()
        print(
            f"applied {len(before.pending)} migration(s); schema at {_label(highest)}"
        )
    except (ConfigError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    store = subparsers.add_parser("store", help="inspect or migrate the snapshot store")
    actions = store.add_subparsers(dest="store_command", required=True)
    for name, text in (
        ("status", "show applied and pending migrations"),
        ("migrate", "apply pending migrations (a backup is taken first)"),
    ):
        action = actions.add_parser(name, help=text)
        action.add_argument("--config", type=Path, required=True, help="site config")
    store.set_defaults(func=_run)
