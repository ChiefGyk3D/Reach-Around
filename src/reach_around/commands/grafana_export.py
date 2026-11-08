# SPDX-License-Identifier: Apache-2.0
"""``reach-around grafana-export --input EXPORT --output STORE``.

Turns a local D1 export into the small SQLite file Grafana reads (see
docs/grafana.md). No network call and no token: the export is made by the owner
with a read-only query, and only floored daily aggregates reach the file.

Exit codes: 0 store written; 2 usage problem, a refused export, or an output
path that cannot be used.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

from reach_around.funnel.export import ExportError, load_exports
from reach_around.funnel.grafana import (
    GrafanaExportError,
    build_subset,
    write_store,
)
from reach_around.funnel.report import FLOOR

EXIT_OK = 0
EXIT_USAGE = 2
_SITE = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")


def run_grafana_export(args: argparse.Namespace) -> int:
    try:
        if not _SITE.fullmatch(args.site):
            raise ValueError("--site must be a short lowercase slug")
        if args.floor < FLOOR:
            raise ValueError(f"--floor cannot be below {FLOOR}")
        export = load_exports(list(args.input))
        subset = build_subset(export, site=args.site, floor=args.floor)
        write_store(args.output, subset)
    except (ExportError, GrafanaExportError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except (OSError, sqlite3.Error):
        print("error: could not write the output file", file=sys.stderr)
        return EXIT_USAGE
    counts = subset.counts()
    detail = ", ".join(f"{name} {n}" for name, n in counts.items())
    print(
        f"wrote {args.output.name}: site {subset.site}, floor {subset.floor}, "
        f"{sum(counts.values())} rows ({detail})"
    )
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "grafana-export",
        help="write the floored daily aggregates Grafana reads, from a local D1 export",
        description="Read an export of the Worker's aggregate tables (the same "
        "files `reach-around report` reads) and write one SQLite file with the "
        "documented Grafana subset: daily views by bot class, and human-likely "
        "views by page, source and country, plus link clicks per day. No row "
        f"under the privacy floor of {FLOOR} views is written alone. No network "
        "call, no token, no raw or per-visitor data. See docs/grafana.md. "
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
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        metavar="PATH",
        help="SQLite file to write (replaced whole; only a file this job made)",
    )
    parser.add_argument("--site", default="links", help="site id (default links)")
    parser.add_argument(
        "--floor",
        type=int,
        default=FLOOR,
        help=f"privacy floor in views (default and minimum {FLOOR}); raise only",
    )
    parser.set_defaults(func=run_grafana_export)
