# SPDX-License-Identifier: Apache-2.0
"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import TemplateError

from reach_around import __version__
from reach_around.config import ConfigError, load_site_config
from reach_around.preview import PreviewError, serve
from reach_around.site.build import build_site
from reach_around.site.visibility import today_in

_OVERRIDES_HELP = "directory with templates/ and custom.css that override the defaults"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reach-around",
        description="Build a static link page from a validated config.",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    commands = parser.add_subparsers(dest="command")
    build = commands.add_parser("build", help="render the static site")
    build.add_argument("--config", type=Path, required=True, help="site config file")
    build.add_argument(
        "--out", type=Path, required=True, help="output directory (emptied first)"
    )
    build.add_argument("--overrides", type=Path, help=_OVERRIDES_HELP)
    serve = commands.add_parser(
        "serve", help="preview the site locally (loopback only)"
    )
    serve.add_argument("--config", type=Path, required=True, help="site config file")
    serve.add_argument("--host", default="127.0.0.1", help="loopback address to bind")
    serve.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    serve.add_argument("--overrides", type=Path, help=_OVERRIDES_HELP)
    return parser


def _serve(config_path: Path, host: str, port: int, overrides: Path | None) -> int:
    try:
        serve(config_path, host=host, port=port, overrides=overrides)
    except (PreviewError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _build(config_path: Path, out_dir: Path, overrides: Path | None) -> int:
    try:
        config = load_site_config(config_path)
        today = today_in(config.timezone, datetime.now(UTC))
        build_site(
            config, out_dir, today=today, protect=[config_path], overrides=overrides
        )
    except (ConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except TemplateError as exc:
        # a refused or broken override template (for example a sandbox
        # violation): report it on one line, never a traceback
        message = " ".join(str(exc).split())
        print(f"error: template problem: {message}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(f"reach-around {__version__}")
        return 0
    if args.command == "build":
        return _build(args.config, args.out, args.overrides)
    if args.command == "serve":
        return _serve(args.config, args.host, args.port, args.overrides)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
