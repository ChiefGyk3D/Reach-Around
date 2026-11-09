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
from functools import partial
from pathlib import Path

from reach_around.config import ConfigError, SiteConfig, load_site_config
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
    utc_stamp,
    write_atomic,
)
from reach_around.counts.history import (
    HistoryError,
    Record,
    append_records,
    new_run_id,
    read_history,
)
from reach_around.counts.importer import (
    MAX_CSV_BYTES,
    ImportFailed,
    merge,
    parse_import,
)
from reach_around.counts.insights import Figure, InsightFailure, collect
from reach_around.counts.refresh import (
    SUSPECT_KINDS,
    Outcome,
    Source,
    host_of,
    load_status,
    refresh,
    render_status,
    select,
)
from reach_around.net.egress import Transport
from reach_around.net.fetch import fetch_json

# What each kind of source counts, as named in the history file.
HISTORY_METRIC = {"youtube": "subscribers", "discord": "members", "matrix": "members"}

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
        history = history_path(config, config_path, paths)
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
                history,
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


def _insights(
    sources: list[Source],
    outcomes: list[Outcome],
    environment: Mapping[str, str],
    transport: Transport | None,
    moment: datetime,
    history: Path | None,
) -> tuple[list[Figure], list[InsightFailure]]:
    """Opt-in views and engagement numbers; only stored in the history file."""
    if history is None:
        return [], []
    fetch = partial(
        fetch_json,
        transport=transport if transport is not None else default_transport(),
        sleep=default_sleep,
    )
    ok = frozenset(o.platform for o in outcomes if o.status == "ok")
    return collect(list(sources), ok, environment, fetch, moment)


def _cycle(
    paths: Paths,
    sources: list[Source],
    accept: frozenset[str],
    environment: Mapping[str, str],
    transport: Transport | None,
    now: Callable[[], datetime],
    history: Path | None = None,
) -> int:
    try:
        previous = (
            dict(load_numbers(paths.target).entries) if paths.target.exists() else {}
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    stamp = now()
    run_id = new_run_id(stamp)
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
    figures, insight_failures = _insights(
        sources, outcomes, environment, transport, now(), history
    )
    for failure in insight_failures:
        print(f"{failure.platform}: insights error ({failure.word})", file=sys.stderr)
    if history is not None and changed:
        # History goes first: if it cannot be written, the status and numbers
        # files are left exactly as they were. Only values that were fetched and
        # accepted are recorded; a failed or skipped source leaves a gap (the
        # status file holds its result), never a zero. One value per platform,
        # metric and UTC day: a repeated run the same day keeps the first, so a
        # retry after a later failure is safe.
        fetched = [
            Record(
                platform=outcome.platform,
                metric=HISTORY_METRIC.get(outcome.kind, "followers"),
                value=outcome.count,
                as_of_utc=utc_stamp(stamp),
                source="api",
                run_id=run_id,
            )
            for outcome in outcomes
            if outcome.status == "ok" and outcome.count is not None
        ] + [
            Record(
                platform=figure.platform,
                metric=figure.metric,
                value=figure.value,
                as_of_utc=utc_stamp(stamp),
                source="api",
                run_id=run_id,
                note=figure.note,
            )
            for figure in figures
        ]
        try:
            kept = append_records(history, fetched, now=stamp, skip_days=True)
        except HistoryError as exc:
            print(
                f"error: history not written, nothing else was changed: {exc}",
                file=sys.stderr,
            )
            return EXIT_WRITE
        if kept < len(fetched):
            print("history: today's value was already recorded; kept as it is")
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
    failed = any(outcome.status == "error" for outcome in outcomes) or bool(
        insight_failures
    )
    return EXIT_SOURCE_FAILED if failed else EXIT_OK


def history_path(
    config: SiteConfig, config_path: Path, paths: Paths | None = None
) -> Path | None:
    """The history file named in the config, or ``None`` when the store is off.

    The path is relative to the config file's folder and must stay inside it
    once symlinks in its folders are resolved. It may not be a symlink, nor the
    config, numbers, status or lock file.
    """
    if config.numbers is None or config.numbers.history_file is None:
        return None
    named = Path(config.numbers.history_file)
    root = config_path.resolve(strict=True).parent
    try:
        parent = (root / named).parent.resolve(strict=True)
    except OSError:
        raise ValueError("the folder for numbers.history_file does not exist") from None
    if not parent.is_relative_to(root):
        raise ValueError("numbers.history_file leaves the config's folder")
    target = parent / named.name
    if target.is_symlink():
        raise ValueError("numbers.history_file must not be a symbolic link")
    if paths is not None:
        for other in (paths.config, paths.target, paths.status, paths.lock):
            if target == other or (
                target.exists() and other.exists() and target.samefile(other)
            ):
                raise ValueError(
                    "numbers.history_file must not be the config, numbers, "
                    "status or lock file"
                )
    return target


def _history_target(args: argparse.Namespace) -> Path:
    if args.file is not None:
        return Path(args.file)
    config = load_site_config(args.config)
    target = history_path(config, args.config)
    if target is None:
        raise ValueError("the config has no numbers.history_file; the store is off")
    return target


def _as_of(text: str) -> str:
    return f"{text}T00:00:00Z" if len(text) == 10 else text


def run_history_add(args: argparse.Namespace) -> int:
    try:
        target = _history_target(args)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    record = Record(
        platform=args.platform,
        metric=args.metric,
        value=args.value,
        as_of_utc=_as_of(args.as_of),
        source=args.source,
        run_id=new_run_id(datetime.now(UTC)),
        note=args.note,
        era=args.era,
        type="correction" if args.correction else "record",
    )
    try:
        append_records(target, [record])
    except HistoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_WRITE
    print(f"{args.platform} {args.metric}: appended {args.value}")
    return EXIT_OK


def run_history_verify(args: argparse.Namespace) -> int:
    try:
        target = _history_target(args)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        history = read_history(target)
    except HistoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_WRITE
    print(f"ok: {len(history.records)} records, hash chain intact")
    return EXIT_OK


def _dispatch(args: argparse.Namespace) -> int:
    if args.counts_command == "history-add":
        return run_history_add(args)
    if args.counts_command == "history-verify":
        return run_history_verify(args)
    if args.counts_command == "import":
        return _run_import(args)
    if args.counts_command == "stale":
        return _run_stale(args)
    return _run(args)


def _where(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--config", type=Path, help="site config naming numbers.history_file"
    )
    group.add_argument("--file", type=Path, help="the history file itself")


def _register_history(
    actions: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    add = actions.add_parser(
        "history-add",
        help="append one hand-kept value to the history file",
        description="Append a value you read yourself (a LinkedIn or Kick follower "
        "count, say). Exit 0 appended, 1 refused (duplicate, damaged file, ...), "
        "2 usage or config error. Earlier lines are never changed.",
    )
    _where(add)
    add.add_argument("--platform", required=True)
    add.add_argument("--metric", default="followers")
    add.add_argument("--value", type=int, required=True)
    add.add_argument(
        "--as-of",
        required=True,
        help="UTC time YYYY-MM-DDTHH:MM:SSZ, or a UTC date YYYY-MM-DD",
    )
    add.add_argument(
        "--source", choices=("hand_kept", "api", "imported"), default="hand_kept"
    )
    add.add_argument("--note")
    add.add_argument("--era", help="label a definition change, e.g. a vendor rule")
    add.add_argument(
        "--correction",
        action="store_true",
        help="correct an earlier record with the same platform, metric, time and "
        "source (needs --note); the original line stays",
    )
    verify = actions.add_parser(
        "history-verify",
        help="check the whole history file",
        description="Exit 0 when every line parses and the hash chain is intact, "
        "1 when the file is damaged (including a truncated last line).",
    )
    _where(verify)


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
        history = history_path(config, config_path, paths)
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
            if history is not None:
                # History first, as in refresh: if it cannot be written, the
                # numbers file is left as it was. One value per platform, metric
                # and UTC day; a retry the same day keeps the first.
                stamp = datetime.now(UTC)
                run_id = new_run_id(stamp)
                append_records(
                    history,
                    [
                        Record(
                            platform=row.key,
                            metric=row.metric,
                            value=row.count,
                            as_of_utc=f"{row.as_of.isoformat()}T00:00:00Z",
                            source="hand_kept",
                            run_id=run_id,
                        )
                        for row in rows
                    ],
                    now=stamp,
                    skip_days=True,
                )
            write_atomic(paths.target, dump_numbers(merge(previous, rows)))
    except ImportFailed as exc:
        print("error: the file was not imported; nothing was written", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return EXIT_USAGE
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except HistoryError as exc:
        print(
            f"error: history not written, nothing else was changed: {exc}",
            file=sys.stderr,
        )
        return EXIT_WRITE
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
    _register_history(actions)
    counts.set_defaults(func=_dispatch)
