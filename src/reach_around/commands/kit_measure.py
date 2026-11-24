# SPDX-License-Identifier: Apache-2.0
"""``reach-around kit measure``: add a hand-kept measure, or import a platform
export as hand-kept measures (kit v2, slice 6, #631).

Both write ``hand-kept.json`` (see ``reach_around.config.handkept``). Output is
counts only: no value, note or cell is ever printed, and an error names the field
that is wrong, not what it held.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from reach_around.config.handkept import (
    FILE_NAME,
    HandKeptError,
    load_entries,
    make_entry,
    write_entries,
)
from reach_around.config.youtube_export import import_youtube


def _day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a date written YYYY-MM-DD") from None


def _fail(exc: Exception) -> int:
    print("error: " + " ".join(str(exc).split()), file=sys.stderr)
    return 1


def _run_add(args: argparse.Namespace) -> int:
    period: tuple[date, date] | None = None
    if args.period:
        start, end = args.period
        period = (start, end)
    try:
        entry = make_entry(
            platform=args.platform,
            key=args.key,
            value=args.value,
            as_of=args.as_of,
            unit=args.unit,
            period=period,
            note=args.note,
        )
        held = load_entries(args.file)
        write_entries(args.file, [*held, entry])
    except (HandKeptError, OSError) as exc:
        return _fail(exc)
    print(f"added 1 entry ({len(held) + 1} in the file)")
    return 0


def _run_import_youtube(args: argparse.Namespace) -> int:
    try:
        found = import_youtube(args.export)
        held = load_entries(args.file)
        write_entries(args.file, [*held, *found])
    except (HandKeptError, OSError) as exc:
        return _fail(exc)
    print(f"imported {len(found)} entries ({len(held) + len(found)} in the file)")
    return 0


def register(actions: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    measure = actions.add_parser(
        "measure",
        help="hand-kept measures: add one, or import a platform export",
        description="Write numbers kept by hand to hand-kept.json. Every entry "
        "uses a key of the measure registry and is labeled hand-kept; an "
        "import names its export. A measure is never visible more widely than "
        "its source field allows. Prints counts only.",
    )
    sub = measure.add_subparsers(dest="measure_command", required=True)

    add = sub.add_parser(
        "add",
        help="add one hand-kept measure",
        description="Validate one entry against the registry and add it. An "
        "unknown key, a unit that is not the key's unit, a measure the build "
        "works out itself, and a repeat of the same platform, key, day and "
        "period are refused. A percent is typed in tenths.",
    )
    add.add_argument("--file", type=Path, default=Path(FILE_NAME))
    add.add_argument("--platform", required=True)
    add.add_argument("--key", required=True)
    add.add_argument("--value", type=int, required=True)
    add.add_argument("--as-of", type=_day, required=True, help="UTC day, YYYY-MM-DD")
    add.add_argument("--unit", help="optional: must be the registry unit of the key")
    add.add_argument(
        "--period",
        type=_day,
        nargs=2,
        metavar=("START", "END"),
        help="the days the value covers; END may not be after --as-of",
    )
    add.add_argument(
        "--note", default="", help="short plain text, at most 200 characters"
    )
    add.set_defaults(func=_run_add)

    yt = sub.add_parser(
        "import-youtube",
        help="import a YouTube analytics export (CSV) as hand-kept measures",
        description="Read the CSV from YouTube Studio's analytics (columns Date, "
        "Views and Watch time (hours)), add the days up and add views and "
        "watch_hours as hand-kept measures that name the export. One regular "
        "file of at most 256 KiB; no network. The figures are private: no page "
        "or PDF shows them.",
    )
    yt.add_argument("--file", type=Path, default=Path(FILE_NAME))
    yt.add_argument("--export", type=Path, required=True, help="the CSV file")
    yt.set_defaults(func=_run_import_youtube)
