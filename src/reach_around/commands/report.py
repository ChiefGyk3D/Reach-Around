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

from reach_around.funnel.export import ExportError, load_exports
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
        export = load_exports(list(args.input))
        report = build_report(
            export,
            site=args.site,
            since=since,
            until=until,
            days=args.days,
            geo=args.geo,
            include_bots=args.include_bots,
            top=args.top,
            floor=args.floor,
        )
    except (ExportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(render_text(report))
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "report",
        help="print the funnel, geography and bot share from a local D1 export",
        description="Read an export of the Worker's aggregate tables (JSON or "
        "CSV, made by you with a read-only query; see docs/report.md) and print "
        "the funnel (page views, clicks, kit views), geography and bot share. "
        "No network call, no token. Nothing under the privacy floor of "
        f"{FLOOR} views is printed. An export that carries an IP address, a "
        "user agent or any column outside the allowed list is refused. "
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
    parser.set_defaults(func=run_report)
