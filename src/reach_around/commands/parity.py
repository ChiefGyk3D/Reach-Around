# SPDX-License-Identifier: Apache-2.0
"""``reach-around parity --site DIR --expected EXPORT``.

Compares the links in a built site with a link list the owner exported from the
old page. It makes no network call. Output is counts and ids only.

Exit codes: 0 parity; 1 differences; 2 cannot tell (bad input, empty site).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reach_around.linkparity import (
    ParityError,
    compare,
    load_expected,
    load_redirects,
    read_site,
    render,
)

EXIT_PARITY = 0
EXIT_DIFFERENCES = 1
EXIT_CANNOT_TELL = 2


def run_parity(args: argparse.Namespace) -> int:
    try:
        expected = load_expected(args.expected)
        redirects = load_redirects(args.redirects) if args.redirects else None
        site = read_site(args.site)
    except ParityError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CANNOT_TELL
    report = compare(site, expected, redirects, check_order=not args.ignore_order)
    sys.stdout.write(render(report, accept_redirects=args.accept_redirects))
    diffs = report.differences(accept_redirects=args.accept_redirects)
    return EXIT_PARITY if diffs == 0 else EXIT_DIFFERENCES


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "parity",
        help="compare the built site's links with an exported link list",
        description="Compare the links in a built site (the --out directory of "
        "'build') with a link list exported from the old page (CSV or JSON "
        "with url and label; see docs/parity.md). No network call. Output is "
        "counts and ids only. Exit 0 parity, 1 differences, 2 cannot tell.",
    )
    parser.add_argument("--site", type=Path, required=True, help="built site directory")
    parser.add_argument(
        "--expected", type=Path, required=True, help="export file, .csv or .json"
    )
    parser.add_argument(
        "--redirects",
        type=Path,
        help="owner-listed redirects (from,to) as .csv or .json; lets a link "
        "that only differs by a known redirect be named 'redirect only'",
    )
    parser.add_argument(
        "--accept-redirects",
        action="store_true",
        help="do not count a redirect-only match as a difference",
    )
    parser.add_argument(
        "--ignore-order",
        action="store_true",
        help="do not report links that are in a different order",
    )
    parser.set_defaults(func=run_parity)
