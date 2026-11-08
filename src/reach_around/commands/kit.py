# SPDX-License-Identifier: Apache-2.0
"""``reach-around kit pdf``: the full private media kit as an offline PDF."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from reach_around.config import ConfigError, load_site_config
from reach_around.kit.pdf import (
    Isolation,
    build_kit_pdf,
    chromium_renderer,
)
from reach_around.kit.stage import GATED_FILE


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a date written YYYY-MM-DD") from None


def _creation_date(explicit: date | None, gated_dir: Path) -> date:
    """The date written into the PDF: ``--date``, else SOURCE_DATE_EPOCH, else the
    day the gated page was built (its modification time, UTC). Never the clock
    of the print run, so the same build prints the same bytes."""
    if explicit is not None:
        return explicit
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if epoch is not None:
        try:
            return datetime.fromtimestamp(int(epoch), UTC).date()
        except (ValueError, OverflowError, OSError):
            raise ValueError("SOURCE_DATE_EPOCH is not a valid timestamp") from None
    try:
        stamp = (gated_dir / GATED_FILE).lstat().st_mtime
    except OSError as exc:
        raise ValueError(
            f"{GATED_FILE} is not in the gated directory; run reach-around build "
            "with --gated-out first"
        ) from exc
    return datetime.fromtimestamp(stamp, UTC).date()


def _run(args: argparse.Namespace) -> int:
    try:
        config = load_site_config(args.config)
        created = _creation_date(args.date, args.gated_out)
        isolation: Isolation = args.isolation
        result = build_kit_pdf(
            config,
            gated_dir=args.gated_out,
            site_dir=args.site_out,
            out_dir=args.out_dir,
            name=args.name,
            created=created,
            renderer=chromium_renderer(args.chromium, isolation=isolation),
            overwrite=args.overwrite,
            protect=[args.config.resolve().parent],
        )
    except (ConfigError, ValueError, OSError) as exc:
        print("error: " + " ".join(str(exc).split()), file=sys.stderr)
        return 1
    print("private: do not publish or share this file", file=sys.stderr)
    print(result.path)
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    kit = subparsers.add_parser("kit", help="media kit tools (private PDF)")
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
        "tree, and is never published. Prints the path on success.",
    )
    pdf.add_argument("--config", type=Path, required=True, help="site config file")
    pdf.add_argument(
        "--gated-out",
        type=Path,
        required=True,
        help="the gated directory the build wrote (holds kit.html)",
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
    pdf.add_argument("--name", default="kit.pdf", help="file name (default kit.pdf)")
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
        default="off",
        help="netns also runs the browser in its own network namespace "
        "(unprivileged user namespaces; the browser sandbox is off in that mode)",
    )
    pdf.add_argument("--overwrite", action="store_true", help="replace an existing PDF")
    kit.set_defaults(func=_run)
