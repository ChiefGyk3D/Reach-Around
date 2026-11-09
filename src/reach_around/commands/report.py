# SPDX-License-Identifier: Apache-2.0
"""``reach-around report --input EXPORT [--input EXPORT ...]``.

Reads a local D1 export (JSON or CSV) made by the owner and prints the funnel,
geography and bot share. It makes no network call and needs no token.

Exit codes: 0 report printed; 2 usage problem or an export that was refused.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from reach_around.funnel.csvout import csv_files, utc_now, write_csv_dir
from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.ledger import LedgerError, init_ledger, reanchor_ledger
from reach_around.funnel.release import release_report
from reach_around.funnel.report import FLOOR, build_report, render_text

EXIT_OK = 0
EXIT_USAGE = 2
_SITE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _day(text: str | None, name: str) -> str | None:
    if text is None:
        return None
    if not _DAY.fullmatch(text):
        raise ValueError(f"{name} must be a date like 2026-11-01")
    try:
        date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"{name} is not a real date") from None
    return text


def run_report(args: argparse.Namespace) -> int:
    try:
        since = _day(args.since, "--since")
        until = _day(args.until, "--until")
        if since and until and since > until:
            raise ValueError("--since is after --until")
        if args.days is not None:
            if args.since:
                raise ValueError("--days cannot be combined with --since")
            if not 1 <= args.days <= 366:
                raise ValueError("--days must be between 1 and 366")
        if not _SITE.fullmatch(args.site):
            raise ValueError("--site must be a short lowercase slug")
        if not 1 <= args.top <= 100:
            raise ValueError("--top must be between 1 and 100")
        if args.floor < FLOOR:
            raise ValueError(f"--floor cannot be below {FLOOR}")
        if args.period is None and args.offset:
            raise ValueError("--offset needs --period")
        if args.period is not None and (
            args.since or args.until or args.days is not None
        ):
            raise ValueError(
                "--period cannot be combined with --since, --until or --days: "
                "the source and country tables are released for one fixed "
                "calendar week or month only"
            )
        if args.period is not None and args.ledger is None:
            raise ValueError(
                "the source and country tables need --ledger FILE: every cell "
                "release is recorded in a release ledger (create it once with "
                "'reach-around funnel-ledger-init FILE' and keep it committed "
                "in your private instance repo)"
            )
        if args.ledger is not None and args.period is None:
            raise ValueError("--ledger needs --period")
        if args.supersede and args.period is None:
            raise ValueError("--supersede needs --period")
        if args.csv is not None and args.json:
            raise ValueError("--csv and --json cannot be combined")
        export = load_exports(list(args.input))
        options = dict(
            site=args.site,
            geo=args.geo,
            include_bots=args.include_bots,
            top=args.top,
            floor=args.floor,
        )
        if args.period is not None:
            # Recorded in the ledger BEFORE anything is shown: no record, no cells.
            report, status = release_report(
                export,
                args.ledger,
                period=args.period,
                offset=args.offset,
                as_of=utc_now().date(),
                supersede=args.supersede,
                **options,
            )
            if status == "supersede":
                print(
                    "warning: a correction release was recorded; two releases of "
                    "one period can let a reader difference them",
                    file=sys.stderr,
                )
        else:
            report = build_report(
                export, since=since, until=until, days=args.days, **options
            )
    except (
        ExportError,
        ValueError,
        LedgerError,
    ) as exc:  # IncompletePeriod is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.csv is not None:
        files = csv_files(
            report,
            generated_at=utc_now(),
            requested={"since": since, "until": until, "days": args.days},
        )
        try:
            names = write_csv_dir(args.csv, files)
        except (OSError, ValueError) as exc:
            print(f"error: cannot write CSV: {exc.__class__.__name__}", file=sys.stderr)
            return EXIT_USAGE
        print(f"wrote {len(names)} CSV files to {args.csv} (UTC dates)")
        return EXIT_OK
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_text(report))
    return EXIT_OK


def run_ledger_init(args: argparse.Namespace) -> int:
    try:
        init_ledger(args.file)
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(
        "created an empty release ledger; commit it in your private instance "
        "repo so its history is kept"
    )
    return EXIT_OK


def run_ledger_reanchor(args: argparse.Namespace) -> int:
    try:
        count = reanchor_ledger(args.file)
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"rewrote the .head file for {count} record(s); commit both files together")
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "report",
        help="print the funnel, geography and bot share from a local D1 export",
        description="Read an export of the Worker's aggregate tables (JSON or "
        "CSV, made by you with a read-only query; see docs/report.md) and print "
        "the funnel (page views, clicks, kit views), geography and bot share. "
        "No network call, no token. No row under the privacy floor of "
        f"{FLOOR} views is printed (label suppression, not k-anonymity; see "
        "docs/report.md for what it does not cover). An export that carries an "
        "IP address, a user agent or any column outside the allowed list is refused. "
        "Exit 0 on success, 2 for a usage problem or a refused export.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        required=True,
        metavar="PATH",
        help="export file, JSON or CSV (repeatable; one file per table for CSV)",
    )
    parser.add_argument("--site", default="links", help="site id (default links)")
    parser.add_argument("--since", help="first UTC day to count, YYYY-MM-DD")
    parser.add_argument("--until", help="last UTC day to count, YYYY-MM-DD")
    parser.add_argument(
        "--days",
        type=int,
        help="count the last N days of data, ending at the latest day or --until",
    )
    parser.add_argument(
        "--period",
        choices=("week", "month"),
        help="release the source and country by destination tables (and every "
        "other section) for ONE complete UTC calendar week (Monday start; only "
        "only while all its day cells exist) or month, recorded in --ledger, "
        "with the floor applied to that period alone; these "
        "tables are not released for a free --since/--until/--days window",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="with --period: complete periods back (0 is the latest complete one)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        metavar="FILE",
        help="release ledger, REQUIRED with --period (create it with "
        "funnel-ledger-init; keep it committed in your private instance repo). "
        "Every cell release is recorded before anything is printed, and a "
        "release that shares a UTC day with a released period of the other kind "
        "is refused",
    )
    parser.add_argument(
        "--supersede",
        action="store_true",
        help="with --period: record a correction when the data behind an "
        "already released period changed (it is recorded too, and two releases "
        "of one period can let a reader difference them)",
    )
    parser.add_argument(
        "--geo",
        choices=("country", "region", "city"),
        default="country",
        help="geography level (default country); finer levels only where the "
        "Worker kept them",
    )
    parser.add_argument(
        "--include-bots",
        action="store_true",
        help="count every bot class in pages, sources and geography "
        "(default: likely-human only)",
    )
    parser.add_argument(
        "--top", type=int, default=10, help="rows per table (default 10, at most 100)"
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=FLOOR,
        help=f"privacy floor in views (default and minimum {FLOOR}); raise only",
    )
    parser.add_argument("--json", action="store_true", help="print JSON, not text")
    parser.add_argument(
        "--csv",
        type=Path,
        metavar="DIR",
        help="write one CSV file per section into DIR (same floor and "
        "suppression; dates are UTC; metadata.csv holds generated_at_utc and "
        "the window); cannot be combined with --json",
    )
    parser.set_defaults(func=run_report)
    init = subparsers.add_parser(
        "funnel-ledger-init",
        help="create the empty release ledger that 'report --period' needs",
        description="Create an empty, owner-only release ledger file (refuses to "
        "touch an existing file). Every release of the source and country by "
        "destination tables is recorded in it; keep it in your private instance "
        "repo and commit it so the history is not lost.",
    )
    init.add_argument("file", type=Path, help="where to create the ledger")
    init.set_defaults(func=run_ledger_init)
    anchor = subparsers.add_parser(
        "funnel-ledger-reanchor",
        help="rewrite the ledger's .head file (only if the ledger is known whole)",
        description="Rewrite the .head file next to a release ledger from the "
        "ledger itself, after the .head file was lost. It cannot detect a ledger "
        "that was already cut short: restore both files together from git first, "
        "and use this only when you know the ledger is whole.",
    )
    anchor.add_argument("file", type=Path, help="the ledger")
    anchor.set_defaults(func=run_ledger_reanchor)
