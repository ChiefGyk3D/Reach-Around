# SPDX-License-Identifier: Apache-2.0
"""``reach-around counts refresh --config PATH [--out numbers.json] ...``.

Exit codes: 0 every source succeeded or was intentionally skipped; 3 at least
one source failed (its previous number is kept); 2 usage or config problem;
1 the result files could not be written.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from reach_around.config import ConfigError, load_site_config
from reach_around.counts.refresh import (
    host_of,
    load_status,
    refresh,
    render_status,
    select,
)
from reach_around.net.egress import Transport
from reach_around.numbers import (
    NumbersError,
    dump_numbers,
    load_numbers,
    write_atomic,
)

EXIT_OK = 0
EXIT_WRITE = 1
EXIT_USAGE = 2
EXIT_SOURCE_FAILED = 3


def default_transport() -> Transport | None:
    """The real pinned HTTPS transport; ``None`` lets ``fetch_json`` build it."""
    return None


def default_sleep(seconds: float) -> None:
    """Retry back-off; a module attribute so tests can replace it."""
    time.sleep(seconds)


def status_path(out: Path) -> Path:
    return out.with_name(f"{out.stem}.status.json")


def run_refresh(
    config_path: Path,
    out: Path | None,
    only: list[str] | None,
    dry_run: bool,
    *,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    try:
        config = load_site_config(config_path)
        if config.numbers is None:
            raise ValueError("the config has no numbers.sources to refresh")
        sources = select(config.numbers, only)
        target = (
            out if out is not None else config_path.resolve().parent / "numbers.json"
        )
        if target.resolve() == config_path.resolve():
            raise ValueError("--out is the config file")
        if target.is_dir() or not target.resolve().parent.is_dir():
            raise ValueError(f"cannot write the numbers file at {target}")
        previous = load_numbers(target) if target.exists() else {}
    except (ConfigError, ValueError, NumbersError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if dry_run:
        for source in sources:
            print(
                f"would fetch {source.platform}: {source.kind} from {host_of(source)}"
            )
        print("dry run: nothing fetched, nothing written")
        return EXIT_OK

    environment = os.environ if env is None else env
    stamp = now()
    numbers, outcomes = refresh(
        sources,
        previous,
        env=environment,
        transport=transport if transport is not None else default_transport(),
        now=lambda: stamp,
        sleep=default_sleep,
    )
    for outcome in outcomes:
        if outcome.status == "ok":
            print(f"{outcome.platform}: ok {outcome.count}")
        elif outcome.status == "skipped":
            print(f"{outcome.platform}: skipped ({outcome.detail})")
        else:
            print(f"{outcome.platform}: error ({outcome.detail})", file=sys.stderr)
    try:
        if any(outcome.status == "ok" for outcome in outcomes):
            write_atomic(target, dump_numbers(numbers))
        write_atomic(
            status_path(target),
            render_status(load_status(status_path(target)), outcomes, stamp),
        )
    except (OSError, NumbersError) as exc:
        reason = exc.strerror if isinstance(exc, OSError) else str(exc)
        print(f"error: cannot write the result files: {reason}", file=sys.stderr)
        return EXIT_WRITE
    failed = any(outcome.status == "error" for outcome in outcomes)
    return EXIT_SOURCE_FAILED if failed else EXIT_OK


def _run(args: argparse.Namespace) -> int:
    return run_refresh(args.config, args.out, args.only, args.dry_run)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    counts = subparsers.add_parser(
        "counts", help="pull follower counts into a numbers file"
    )
    actions = counts.add_subparsers(dest="counts_command", required=True)
    refresh_parser = actions.add_parser(
        "refresh",
        help="fetch every numbers source and update the numbers file",
        description="Exit 0 when every source succeeded or was skipped, 3 when "
        "any source failed (its previous number is kept), 2 for usage or "
        "config errors.",
    )
    refresh_parser.add_argument(
        "--config", type=Path, required=True, help="site config file"
    )
    refresh_parser.add_argument(
        "--out",
        type=Path,
        help="numbers file to update (default: numbers.json beside the config); "
        "the status file is written beside it as <name>.status.json",
    )
    refresh_parser.add_argument(
        "--only",
        nargs="+",
        action="extend",
        metavar="SLUG",
        help="refresh only this platform slug (repeatable)",
    )
    refresh_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print hosts and kinds that would be fetched; fetch and write nothing",
    )
    counts.set_defaults(func=_run)
