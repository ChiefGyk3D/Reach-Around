# SPDX-License-Identifier: Apache-2.0
"""Command line wiring for ``schema`` and the safe edit commands."""

from __future__ import annotations

import argparse
import difflib
import os
import sys
import tempfile
from functools import partial
from pathlib import Path
from typing import Any

from reach_around import editing
from reach_around.config import ConfigError
from reach_around.schema import render_schema

COMMANDS = (
    "schema",
    "add-link",
    "remove-link",
    "move-link",
    "set",
    "set-link-field",
)


def register(commands: Any) -> None:
    """Add the subcommands to the ``argparse`` sub-parser group ``commands``."""
    schema = commands.add_parser(
        "schema", help="print the JSON Schema of the site config"
    )
    schema.add_argument("--out", type=Path, help="write to this file, not stdout")

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--config", type=Path, required=True, help="site config file"
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="show the diff, write nothing"
        )
        parser.add_argument(
            "--no-backup", action="store_true", help="do not keep a .bak copy"
        )
        parser.add_argument(
            "--force-path",
            action="store_true",
            help="allow a symlinked config or one outside the working tree",
        )

    add = commands.add_parser("add-link", help="add a link to a tab")
    common(add)
    add.add_argument("--tab", required=True, help="id of the tab")
    add.add_argument("--title", required=True)
    add.add_argument("--url", required=True, help="https URL")
    add.add_argument("--subtitle")
    add.add_argument("--icon")
    add.add_argument("--affiliate", action="store_true")
    add.add_argument("--position", type=int, help="1-based place among the blocks")
    add.add_argument("--id", help="link id (default: made from the title)")
    remove = commands.add_parser("remove-link", help="remove a link by id")
    common(remove)
    remove.add_argument("id")
    move = commands.add_parser("move-link", help="move a link within its list")
    common(move)
    move.add_argument("id")
    move.add_argument("--position", type=int, required=True, help="1-based place")
    setter = commands.add_parser(
        "set", help="set one safe scalar key (see docs/customizing.md)"
    )
    common(setter)
    setter.add_argument("key", help="dotted key, for example theme.mode")
    setter.add_argument("value")
    field = commands.add_parser(
        "set-link-field",
        help="set one field of a link (an empty subtitle or icon clears it)",
    )
    common(field)
    field.add_argument("id", help="link id")
    field.add_argument("field", help=", ".join(editing.LINK_FIELDS))
    field.add_argument("value")


def _write_schema(out: Path | None) -> int:
    text = render_schema()
    if out is None:
        sys.stdout.write(text)
        return 0
    try:
        fd, name = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.chmod(name, 0o644)
            os.replace(name, out)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        print(f"error: {out}: cannot write: {exc.strerror}", file=sys.stderr)
        return 1
    return 0


def _show(result: editing.Result) -> None:
    diff = difflib.unified_diff(
        result.old.splitlines(),
        result.new.splitlines(),
        fromfile=str(result.path),
        tofile=f"{result.path} (edited)",
        lineterm="",
    )
    for line in diff:
        print(line)
    if not result.written:
        print("dry run: no file written")
    else:
        kept = f" (backup: {result.backup})" if result.backup else ""
        print(f"wrote {result.path}{kept}")


def _add_link_args(args: argparse.Namespace, raw: dict[str, Any]) -> dict[str, Any]:
    for field in ("title", "url", "subtitle", "icon", "id"):
        value = getattr(args, field)
        if value is not None:
            editing.check_text(value, f"--{field}")
    link: dict[str, Any] = {
        "id": args.id or editing.make_slug(args.title, editing.all_ids(raw)),
        "title": args.title,
        "url": args.url,
    }
    if args.subtitle is not None:
        link["subtitle"] = args.subtitle
    if args.icon is not None:
        link["icon"] = args.icon
    if args.affiliate:
        link["affiliate"] = True
    return link


def run(args: argparse.Namespace) -> int:
    """Run one of ``COMMANDS``; returns the exit status."""
    if args.command == "schema":
        return _write_schema(args.out)
    compute: editing.Compute
    if args.command == "add-link":

        def compute(text: str, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            return editing.add_link(
                text,
                raw,
                tab=args.tab,
                link=_add_link_args(args, raw),
                position=args.position,
            )

    elif args.command == "remove-link":
        compute = partial(_with_raw, editing.remove_link, link_id=args.id)
    elif args.command == "move-link":
        compute = partial(
            _with_raw, editing.move_link, link_id=args.id, position=args.position
        )
    elif args.command == "set-link-field":
        compute = partial(
            _with_raw,
            editing.set_link_field,
            link_id=args.id,
            field=args.field,
            value=args.value,
        )
    else:
        compute = partial(_with_raw, editing.set_value, key=args.key, value=args.value)
    try:
        result = editing.apply(
            args.config,
            compute,
            dry_run=args.dry_run,
            backup=not args.no_backup,
            force_path=args.force_path,
        )
    except (editing.EditError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _show(result)
    return 0


def _with_raw(
    func: Any, text: str, raw: dict[str, Any], **kwargs: Any
) -> tuple[str, dict[str, Any]]:
    result: tuple[str, dict[str, Any]] = func(text, raw, **kwargs)
    return result


__all__ = ["COMMANDS", "register", "run"]
