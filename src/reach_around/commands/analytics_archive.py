# SPDX-License-Identifier: Apache-2.0
"""``reach-around analytics-archive --input EXPORT --archive DIR``.

Keeps the last 25 closed UTC months of the click funnel's monthly rollups,
floored like the report, one file per month, in a directory of the private
instance repo (see docs/report.md). No network call and no token.

Exit codes: 0 done; 2 usage problem, a refused export, or an archive that
cannot be used or would change.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from reach_around.config.cutover import utc_today
from reach_around.funnel.archive import ArchiveError, build_records, write_records
from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.report import FLOOR

EXIT_OK = 0
EXIT_USAGE = 2
_SITE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")


def run_analytics_archive(args: argparse.Namespace) -> int:
    try:
        if not _SITE.fullmatch(args.site):
            raise ValueError("--site must be a short lowercase slug")
        if args.floor < FLOOR:
            raise ValueError(f"--floor cannot be below {FLOOR}")
        today = utc_today()
        export = load_exports(list(args.input))
        records = build_records(export, site=args.site, floor=args.floor, today=today)
        result = write_records(args.archive, records, today=today)
    except (ExportError, ArchiveError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError:
        print("error: could not write the archive", file=sys.stderr)
        return EXIT_USAGE
    print(
        f"archive {args.archive.name}: site {args.site}, "
        f"{len(result.written)} month(s) added, {len(result.unchanged)} unchanged, "
        f"{len(result.dropped)} dropped past 25 months"
    )
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "analytics-archive",
        help="archive the last 25 closed months of floored click rollups",
        description="Read an export that holds click_src_monthly, "
        "click_country_monthly and click_month_log, and write one floored "
        "JSON file per closed UTC month into the archive directory. A month "
        "is written once and never changed; months past 25 are removed. "
        "Exit 0 on success, 2 for a usage problem or a refused change.",
    )
    parser.add_argument(
        "--input", type=Path, action="append", required=True, metavar="PATH"
    )
    parser.add_argument(
        "--archive",
        type=Path,
        required=True,
        metavar="DIR",
        help="existing directory in the private instance repo (not a symbolic link)",
    )
    parser.add_argument("--site", default="links", help="site id (default links)")
    parser.add_argument(
        "--floor",
        type=int,
        default=FLOOR,
        help=f"privacy floor (default and minimum {FLOOR})",
    )
    parser.set_defaults(func=run_analytics_archive)
