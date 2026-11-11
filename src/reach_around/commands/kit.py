# SPDX-License-Identifier: Apache-2.0
"""``reach-around kit pdf``: the full private media kit as an offline PDF."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from reach_around.config import ConfigError, load_site_config
from reach_around.config.numbers import Numbers, load_numbers
from reach_around.kit.pdf import (
    Isolation,
    build_kit_pdf,
    build_public_kit_pdf,
    chromium_renderer,
)
from reach_around.kit.stage import GATED_FILE, PUBLIC_FILE


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a date written YYYY-MM-DD") from None


def _creation_date(
    explicit: date | None, built_dir: Path, file: str = GATED_FILE
) -> date:
    """The date written into the PDF: ``--date``, else SOURCE_DATE_EPOCH, else the
    day the page was built (``file`` in ``built_dir``, its modification time,
    UTC). Never the clock of the print run, so the same build prints the same
    bytes."""
    if explicit is not None:
        return explicit
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), UTC).date()
        except (ValueError, OverflowError, OSError):
            raise ValueError("SOURCE_DATE_EPOCH is not a valid timestamp") from None
    try:
        stamp = (built_dir / file).lstat().st_mtime
    except OSError as exc:
        raise ValueError(
            f"{file} is not in {built_dir}; run reach-around build first "
            "(with --gated-out for the private PDF)"
        ) from exc
    return datetime.fromtimestamp(stamp, UTC).date()


def _numbers(args: argparse.Namespace) -> Numbers | None:
    """The numbers file the build used: ``--numbers``, else ``numbers.json`` beside
    the config if it exists (the same default as ``build``)."""
    path: Path | None = args.numbers
    if path is None:
        beside = args.config.parent / "numbers.json"
        path = beside if os.path.lexists(beside) else None
    return None if path is None else load_numbers(path)


def _run_public(args: argparse.Namespace) -> int:
    try:
        if args.gated_out is not None:
            raise ValueError(
                "--public prints the public page only; it does not take --gated-out"
            )
        if args.isolation not in (None, "netns"):
            raise ValueError(
                "--public requires --isolation netns for offline rendering"
            )
        config = load_site_config(args.config)
        created = _creation_date(args.date, args.site_out, PUBLIC_FILE)
        isolation: Isolation = args.isolation or "netns"
        result = build_public_kit_pdf(
            config,
            site_dir=args.site_out,
            out_dir=args.out_dir,
            name=args.name or f"kit-public-{created.isoformat()}.pdf",
            created=created,
            renderer=chromium_renderer(args.chromium, isolation=isolation),
            overwrite=args.overwrite,
            protect=[args.config.resolve().parent],
            numbers=_numbers(args),
        )
    except (ConfigError, ValueError, OSError) as exc:
        print("error: " + " ".join(str(exc).split()), file=sys.stderr)
        return 1
    print(result.path)
    return 0


def _run(args: argparse.Namespace) -> int:
    if args.public:
        return _run_public(args)
    try:
        if args.gated_out is None:
            raise ValueError("--gated-out is required unless --public is given")
        config = load_site_config(args.config)
        created = _creation_date(args.date, args.gated_out)
        isolation: Isolation = args.isolation or "off"
        result = build_kit_pdf(
            config,
            gated_dir=args.gated_out,
            site_dir=args.site_out,
            out_dir=args.out_dir,
            name=args.name or "kit.pdf",
            created=created,
            renderer=chromium_renderer(args.chromium, isolation=isolation),
            overwrite=args.overwrite,
            protect=[args.config.resolve().parent],
            numbers=_numbers(args),
        )
    except (ConfigError, ValueError, OSError) as exc:
        print("error: " + " ".join(str(exc).split()), file=sys.stderr)
        return 1
    print("private: do not publish or share this file", file=sys.stderr)
    print(result.path)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    kit = subparsers.add_parser("kit", help="media kit tools (PDF)")
    actions = kit.add_subparsers(dest="kit_command", required=True)
    pdf = actions.add_parser(
        "pdf",
        help="render the full private media kit to a PDF, offline",
        description="Render the gated media kit (written by 'build --gated-out') "
        "to a PDF with headless Chromium. No network access, no fetched fonts or "
        "images; the PDF's metadata is fixed, so the same inputs give the same "
        "bytes. The build fails if a private value is found in the PDF text. The "
        "file is private: it goes to an owner-only directory outside the site "
        "output, the gated directory, the config directory and any git work "
        "tree, and is never published. With --public the public half of the "
        "kit (the page the site publishes, public numbers only) is printed "
        "the same way, with a 'Valid as of' line and a dated file name. Prints "
        "the path on success.",
    )
    pdf.add_argument(
        "--public",
        action="store_true",
        help="print the public half of the kit instead (needs no --gated-out; "
        "default file name kit-public-YYYY-MM-DD.pdf)",
    )
    pdf.add_argument("--config", type=Path, required=True, help="site config file")
    pdf.add_argument(
        "--gated-out",
        type=Path,
        help="the gated directory the build wrote (holds kit.html); required "
        "unless --public",
    )
    pdf.add_argument(
        "--site-out",
        type=Path,
        required=True,
        help="the public output directory of the same build (stylesheets, images)",
    )
    pdf.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="private directory for the PDF (created owner-only if missing)",
    )
    pdf.add_argument(
        "--numbers",
        type=Path,
        help="the numbers file the build used (default: numbers.json beside the "
        "config, if it exists); the printed headline numbers are checked "
        "against it",
    )
    pdf.add_argument(
        "--name",
        help="file name (default kit.pdf, or kit-public-YYYY-MM-DD.pdf with --public)",
    )
    pdf.add_argument(
        "--date",
        type=_iso_date,
        help="creation date written into the PDF, YYYY-MM-DD (default: "
        "SOURCE_DATE_EPOCH, else the day the gated page was built)",
    )
    pdf.add_argument(
        "--chromium",
        type=Path,
        help="browser binary (default: RA_CHROMIUM, else chromium on PATH)",
    )
    pdf.add_argument(
        "--isolation",
        choices=("off", "netns"),
        default=None,
        help="netns runs the browser in its own network namespace and is required "
        "for --public; off keeps browser-level defenses only for private PDFs "
        "(the browser sandbox is off in netns mode)",
    )
    pdf.add_argument("--overwrite", action="store_true", help="replace an existing PDF")
    kit.set_defaults(func=_run)
