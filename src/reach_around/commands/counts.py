# SPDX-License-Identifier: Apache-2.0
"""``reach-around counts refresh --config PATH [--out numbers.json] ...``.

Also ``counts import`` (hand-kept numbers from a CSV file) and ``counts stale``
(hand-kept numbers due for an update). ``counts stale`` exits 0 when nothing is
due, 4 when something is, 2 for usage or config problems.

Exit codes of ``refresh``: 0 every source succeeded or was intentionally
skipped; 3 at least one source failed (its previous number is kept); 2 usage or
config problem; 1 the result files could not be written or another refresh
holds the lock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from reach_around.config import ConfigError, load_site_config
from reach_around.config.cutover import utc_today
from reach_around.config.loader import _read
from reach_around.config.numbers import load_numbers
from reach_around.config.stale import REMIND_DAYS, due_numbers
from reach_around.counts.files import (
    LOCK_WAIT_SECONDS,
    FileLock,
    LockBusy,
    LockLost,
    Paths,
    dump_numbers,
    plan_paths,
    write_atomic,
)
from reach_around.counts.importer import (
    MAX_CSV_BYTES,
    ImportFailed,
    merge,
    parse_import,
)
from reach_around.counts.refresh import (
    SUSPECT_KINDS,
    Source,
    host_of,
    load_status,
    refresh,
    render_status,
    select,
)
from reach_around.net.egress import Transport

EXIT_OK = 0
EXIT_WRITE = 1
EXIT_USAGE = 2
EXIT_SOURCE_FAILED = 3
EXIT_DUE = 4


def default_transport() -> Transport | None:
    """The real pinned HTTPS transport; ``None`` lets ``fetch_json`` build it."""
    return None


def default_sleep(seconds: float) -> None:
    """Retry back-off; a module attribute so tests can replace it."""
    time.sleep(seconds)


def run_refresh(
    config_path: Path,
    out: Path | None,
    only: list[str] | None,
    dry_run: bool,
    *,
    accept_drop: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    lock_wait: float = LOCK_WAIT_SECONDS,
) -> int:
    try:
        config = load_site_config(config_path)
        if config.numbers is None or not config.numbers.sources:
            raise ValueError("the config has no numbers.sources to refresh")
        sources = select(config.numbers, only)
        known = {source.platform for source in config.numbers.sources}
        unknown = sorted(set(accept_drop or []) - known)
        if unknown:
            raise ValueError(
                f"--accept-drop names no configured source: {', '.join(unknown)}"
            )
        paths = plan_paths(config_path, out)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if dry_run:
        for source in sources:
            print(
                f"would fetch {source.platform}: {source.kind} from {host_of(source)}"
            )
        print("dry run: nothing fetched, nothing written")
        return EXIT_OK

    try:
        # One lock for the whole load, fetch, merge and write cycle: a second run
        # started meanwhile waits, then reads what this one wrote, so a stale copy
        # can never replace a newer number.
        with FileLock(paths.lock, wait=lock_wait):
            return _cycle(
                paths,
                sources,
                frozenset(accept_drop or []),
                os.environ if env is None else env,
                transport,
                now,
            )
    except LockBusy:
        print(
            "error: another counts refresh holds the lock; try again shortly",
            file=sys.stderr,
        )
        return EXIT_WRITE
    except LockLost as exc:
        print(f"error: {exc}; nothing was written", file=sys.stderr)
        return EXIT_WRITE
    except OSError as exc:
        print(f"error: cannot use the output folder: {exc.strerror}", file=sys.stderr)
        return EXIT_WRITE


def _write_failed(exc: Exception) -> None:
    reason = exc.strerror if isinstance(exc, OSError) else str(exc)
    print(f"error: cannot write the result files: {reason}", file=sys.stderr)


def _cycle(
    paths: Paths,
    sources: list[Source],
    accept: frozenset[str],
    environment: Mapping[str, str],
    transport: Transport | None,
    now: Callable[[], datetime],
) -> int:
    try:
        previous = (
            dict(load_numbers(paths.target).entries) if paths.target.exists() else {}
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    stamp = now()
    numbers, outcomes = refresh(
        sources,
        previous,
        env=environment,
        transport=transport if transport is not None else default_transport(),
        now=lambda: stamp,
        sleep=default_sleep,
        accept_drop=accept,
    )
    for outcome in outcomes:
        if outcome.status == "ok":
            print(f"{outcome.platform}: ok {outcome.count}")
        elif outcome.status == "skipped":
            print(f"{outcome.platform}: skipped ({outcome.detail})")
        else:
            print(f"{outcome.platform}: error ({outcome.detail})", file=sys.stderr)
            if outcome.detail in SUSPECT_KINDS:
                print(
                    f"  the previous number was kept; if the change is real, rerun "
                    f"with --accept-drop {outcome.platform}",
                    file=sys.stderr,
                )
    # The status file goes first and the numbers file last, so a failure can never
    # leave new numbers with no record of the run. If the numbers write fails, the
    # status is rewritten with ``committed: false`` (best effort) and the exit is 1.
    changed = any(outcome.status == "ok" for outcome in outcomes)
    previous_status = load_status(paths.status)
    try:
        write_atomic(
            paths.status, render_status(previous_status, outcomes, stamp, True)
        )
    except (OSError, ConfigError) as exc:
        _write_failed(exc)
        return EXIT_WRITE
    if changed:
        try:
            write_atomic(paths.target, dump_numbers(numbers))
        except (OSError, ConfigError) as exc:
            try:
                write_atomic(
                    paths.status,
                    render_status(previous_status, outcomes, stamp, False),
                )
            except (OSError, ConfigError):
                pass  # the numbers file is untouched either way
            _write_failed(exc)
            return EXIT_WRITE
    failed = any(outcome.status == "error" for outcome in outcomes)
    return EXIT_SOURCE_FAILED if failed else EXIT_OK


def _run(args: argparse.Namespace) -> int:
    return run_refresh(
        args.config,
        args.out,
        args.only,
        args.dry_run,
        accept_drop=args.accept_drop,
    )


def run_import(
    config_path: Path,
    csv_path: Path,
    out: Path | None,
    dry_run: bool,
    *,
    today: date | None = None,
    lock_wait: float = LOCK_WAIT_SECONDS,
) -> int:
    """Import hand-kept numbers from ``csv_path`` into the numbers file.

    Exit 0 done (or a dry run that would succeed), 2 the file or config is
    refused (nothing written), 1 the file could not be written.
    """
    try:
        config = load_site_config(config_path)
        paths = plan_paths(config_path, out)
        data = _read(csv_path, MAX_CSV_BYTES)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    day = today if today is not None else utc_today()
    try:
        with FileLock(paths.lock, wait=lock_wait):
            previous = (
                dict(load_numbers(paths.target).entries)
                if paths.target.exists()
                else {}
            )
            rows = parse_import(data, config, previous, day)
            if dry_run:
                print(
                    f"dry run: {len(rows)} number(s) would be imported; nothing written"
                )
                return EXIT_OK
            write_atomic(paths.target, dump_numbers(merge(previous, rows)))
    except ImportFailed as exc:
        print("error: the file was not imported; nothing was written", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return EXIT_USAGE
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except LockBusy:
        print(
            "error: another counts run holds the lock; try again shortly",
            file=sys.stderr,
        )
        return EXIT_WRITE
    except LockLost as exc:
        print(f"error: {exc}; nothing was written", file=sys.stderr)
        return EXIT_WRITE
    except OSError as exc:
        print(f"error: cannot write the numbers file: {exc.strerror}", file=sys.stderr)
        return EXIT_WRITE
    for row in rows:
        print(f"{row.key}: imported (as of {row.as_of.isoformat()})")
    # TODO(#437): also append to the history store as source=hand_kept once the
    # store (PR #417) has merged.
    return EXIT_OK


def run_stale(
    config_path: Path,
    numbers_path: Path | None,
    remind_days: int,
    as_json: bool,
    *,
    today: date | None = None,
) -> int:
    """List hand-kept numbers due for an update. Values are never printed.

    Exit 0 nothing is due, 4 something is, 2 usage or config problem.
    """
    try:
        config = load_site_config(config_path)
        path = numbers_path or config_path.parent / "numbers.json"
        numbers = load_numbers(path) if numbers_path or path.exists() else None
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    day = today if today is not None else utc_today()
    notes = due_numbers(config, numbers, day, remind_days)
    items = [
        {
            "scope": note.scope,
            "platform": note.platform,
            "id": note.item_id,
            "as_of": note.as_of.isoformat() if note.as_of else None,
            "days": note.days,
            "limit": note.limit,
            "status": "stale" if note.stale else "reminder",
            "days_over": max(0, note.days - note.limit),
        }
        for note in sorted(
            notes, key=lambda n: (-n.days, n.scope, n.platform, n.item_id)
        )
    ]
    if as_json:
        report = {
            "date": day.isoformat(),
            "remind_days": remind_days,
            "due": items,
            "stale_count": sum(1 for i in items if i["status"] == "stale"),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
    elif not items:
        print("no hand-kept number is due")
    else:
        for item in items:
            print(
                f"{item['status']}: {item['platform']} ({item['scope']} {item['id']}) "
                f"as of {item['as_of']}, {item['days']} days old, limit {item['limit']}"
            )
    return EXIT_DUE if items else EXIT_OK


def _run_import(args: argparse.Namespace) -> int:
    return run_import(args.config, args.csv, args.out, args.dry_run)


def _run_stale(args: argparse.Namespace) -> int:
    return run_stale(args.config, args.numbers, args.remind_days, args.json)


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
        "--accept-drop",
        nargs="+",
        action="extend",
        metavar="PLATFORM",
        help="accept a zero or a fall of more than 90 percent (from 1000 or "
        "more) for this platform on purpose; otherwise such a value is kept out "
        "as zero_suspect or drop_suspect",
    )
    refresh_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print hosts and kinds that would be fetched; fetch and write nothing",
    )
    refresh_parser.set_defaults(func=_run)
    import_parser = actions.add_parser(
        "import",
        help="import hand-kept numbers from a CSV file (platform,metric,value,as_of)",
        description="Strict batch import: all rows are checked first and one bad "
        "row refuses the file. as_of is a UTC day and may not be in the future. "
        "Exit 0 done, 2 refused (nothing written), 1 write failed.",
    )
    import_parser.add_argument("--config", type=Path, required=True)
    import_parser.add_argument("--csv", type=Path, required=True, help="the CSV file")
    import_parser.add_argument(
        "--out", type=Path, help="numbers file to update (default: beside the config)"
    )
    import_parser.add_argument("--dry-run", action="store_true")
    import_parser.set_defaults(func=_run_import)
    stale_parser = actions.add_parser(
        "stale",
        help="list hand-kept numbers due for an update",
        description="Prints the hand-kept numbers that are stale or old enough for "
        "a reminder, never their values. Exit 0 nothing due, 4 something due.",
    )
    stale_parser.add_argument("--config", type=Path, required=True)
    stale_parser.add_argument("--numbers", type=Path, help="numbers file to read")
    stale_parser.add_argument(
        "--remind-days",
        type=int,
        default=REMIND_DAYS,
        help=f"remind when a number is this many days old (default {REMIND_DAYS})",
    )
    stale_parser.add_argument("--json", action="store_true", help="machine-readable")
    stale_parser.set_defaults(func=_run_stale)
