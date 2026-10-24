# SPDX-License-Identifier: Apache-2.0
"""Command line entry point."""

from __future__ import annotations

import argparse
import importlib
import os
import pkgutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import TemplateError

from reach_around import __version__, commands, edit_cli
from reach_around.config import ConfigError, load_site_config
from reach_around.config.numbers import Numbers, load_numbers
from reach_around.preview import PreviewError, serve
from reach_around.site.build import build_site, manifest_json
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
    commands_parsers = parser.add_subparsers(dest="command")
    build = commands_parsers.add_parser("build", help="render the static site")
    build.add_argument("--config", type=Path, required=True, help="site config file")
    build.add_argument(
        "--out", type=Path, required=True, help="output directory (emptied first)"
    )
    build.add_argument("--overrides", type=Path, help=_OVERRIDES_HELP)
    build.add_argument(
        "--manifest",
        type=Path,
        help="where to write the link manifest (default: <out>.manifest.json "
        "beside --out); it is never written inside --out, which is published",
    )
    build.add_argument(
        "--numbers",
        type=Path,
        help="JSON file of platform counts (default: numbers.json beside the "
        "config, if it exists); read at build time, never published",
    )
    serve = commands_parsers.add_parser(
        "serve", help="preview the site locally (loopback only)"
    )
    serve.add_argument("--config", type=Path, required=True, help="site config file")
    serve.add_argument("--host", default="127.0.0.1", help="loopback address to bind")
    serve.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    serve.add_argument("--overrides", type=Path, help=_OVERRIDES_HELP)
    edit_cli.register(commands_parsers)
    for module in pkgutil.iter_modules(commands.__path__):
        importlib.import_module(f"{commands.__name__}.{module.name}").register(
            commands_parsers
        )
    return parser


def _serve(config_path: Path, host: str, port: int, overrides: Path | None) -> int:
    try:
        serve(config_path, host=host, port=port, overrides=overrides)
    except (PreviewError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _manifest_target(out_dir: Path, manifest: Path | None, config_path: Path) -> Path:
    """The manifest path; refuse one inside ``out_dir`` (it gets published)."""
    out = out_dir.resolve()
    target = (
        manifest if manifest is not None else out.parent / f"{out.name}.manifest.json"
    )
    resolved = target.resolve()
    if resolved == out or out in resolved.parents:
        raise ValueError(
            f"manifest path {target} is inside the output directory {out_dir}, "
            "which is published; choose a path outside it"
        )
    if resolved == config_path.resolve():
        raise ValueError(f"manifest path {target} is the config file")
    if resolved.is_dir():
        raise ValueError(f"manifest path {target} is a directory")
    return target


def _numbers_source(
    out_dir: Path, numbers: Path | None, config_path: Path
) -> Path | None:
    """The numbers file to read, or None; refuse one inside ``out_dir``."""
    explicit = numbers is not None
    path = numbers if numbers is not None else config_path.parent / "numbers.json"
    if not explicit and not os.path.lexists(path):
        return None
    out = out_dir.resolve()
    resolved = path.resolve()
    if resolved == out or out in resolved.parents:
        raise ValueError(
            f"numbers path {path} is inside the output directory {out_dir}, "
            "which is published; choose a path outside it"
        )
    if resolved == config_path.resolve():
        raise ValueError(f"numbers path {path} is the config file")
    return path


def _build(
    config_path: Path,
    out_dir: Path,
    overrides: Path | None,
    manifest_path: Path | None = None,
    numbers_path: Path | None = None,
) -> int:
    try:
        target = _manifest_target(out_dir, manifest_path, config_path)
        source = _numbers_source(out_dir, numbers_path, config_path)
        config = load_site_config(config_path)
        numbers: Numbers | None = None
        if source is not None:
            numbers = load_numbers(source)
            unknown = numbers.unknown_keys(config)
            if unknown:
                print(
                    f"warning: {len(unknown)} key(s) in {source} match no platform "
                    "and were ignored",
                    file=sys.stderr,
                )
        today = today_in(config.timezone, datetime.now(UTC))
        manifest = build_site(
            config,
            out_dir,
            today=today,
            protect=[config_path],
            overrides=overrides,
            assets_dir=config_path.parent,
            numbers=numbers,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(manifest_json(manifest), encoding="utf-8")
    except (ConfigError, ValueError, OSError) as exc:
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
        return _build(
            args.config, args.out, args.overrides, args.manifest, args.numbers
        )
    if args.command == "serve":
        return _serve(args.config, args.host, args.port, args.overrides)
    if args.command in edit_cli.COMMANDS:
        return edit_cli.run(args)
    func = getattr(args, "func", None)
    if func is not None:
        return int(func(args))
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
