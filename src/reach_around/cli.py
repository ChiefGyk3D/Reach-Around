# SPDX-License-Identifier: Apache-2.0
"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from reach_around import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reach-around",
        description="Build a static link page from a validated config.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    # Subcommands (build) are added in a later task.
    parser.add_subparsers(dest="command")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"reach-around {__version__}")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
