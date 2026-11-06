#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate examples/themes/brand-dark.yaml from the vendored brand tokens.

Usage: python3 -I scripts/gen-brand-theme.py [--check] [--source FILE] [--out FILE]

The blog's brand/tokens.json is the source of truth for color and shape. This
maps its tokens to the theme keys that become the --ra-* custom properties:

    accent -> colors.accent      (--ra-color-accent)
    on-accent -> colors.on_accent (--ra-color-on-accent)
    bg, surface, text, muted, border -> the same colors.* key
    radius -> radius             (--ra-radius)

Output is deterministic. --check writes nothing and exits 1 if the file on disk
differs from what the tokens generate. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "vendor" / "brand" / "tokens.json"
DEFAULT_OUT = ROOT / "examples" / "themes" / "brand-dark.yaml"

# (token name, theme color key), in output order.
COLOR_MAP: tuple[tuple[str, str], ...] = (
    ("accent", "accent"),
    ("on-accent", "on_accent"),
    ("bg", "bg"),
    ("surface", "surface"),
    ("text", "text"),
    ("muted", "muted"),
    ("border", "border"),
)
RADIUS_NAME = "radius"

_HEX = re.compile(r"^#[0-9A-F]{6}$")
_LENGTH = re.compile(r"^\d{1,2}(?:\.\d{1,3})?rem$")

HEADER = """\
# GENERATED FILE. Do not edit: change vendor/brand/tokens.json (a pinned copy of
# the blog's brand/tokens.json) and run python3 -I scripts/gen-brand-theme.py.
# CI runs scripts/check-token-drift, which fails if this file, the vendored
# tokens and the pinned hash in vendor/brand/tokens.lock.json disagree.
#
# A generic dark brand preset: amber accent on near-black, dark only. Every value
# is editable in your own config; replace the hex colors with your own kit.
#
# Contrast (computed by the tests): text on bg 16.8:1, accent on bg 10.4:1,
# on_accent on accent 9.4:1, muted on surface 7.3:1.
#
# Fonts: the body is Jost, which the engine already serves from your own site, so
# `fonts.body` stays unset. For the Orbitron heading face, follow "Heading font:
# Orbitron" in docs/config.md: put the .woff2 file (SIL OFL 1.1, Reserved Font
# Name, ship it unmodified) in your instance folder and uncomment `fonts.heading`;
# nothing is ever fetched from a font host.
"""

FOOTER = """\
# fonts:
#   heading: fonts/orbitron.woff2
button_style: pill
mode: dark
"""


class TokenError(Exception):
    pass


def flat_tokens(path: Path) -> dict[str, str]:
    """Read the token file and return every token as one name -> value map."""
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TokenError(f"{path}: {exc}") from exc
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list) or not groups:
        raise TokenError(f"{path}: needs a non-empty groups list")
    out: dict[str, str] = {}
    for group in groups:
        tokens = group.get("tokens") if isinstance(group, dict) else None
        if not isinstance(tokens, dict):
            raise TokenError(f"{path}: every group needs a tokens object")
        for name, value in tokens.items():
            if name in out:
                raise TokenError(f"{path}: duplicate token {name!r}")
            if not isinstance(value, str):
                raise TokenError(f"{path}: token {name!r} is not a string")
            out[name] = value
    return out


def render(source: Path) -> str:
    """Return the theme file text for the token file at ``source``."""
    tokens = flat_tokens(source)
    lines = [HEADER.rstrip("\n"), "colors:"]
    for token, key in COLOR_MAP:
        value = tokens.get(token)
        if value is None:
            raise TokenError(f"{source}: missing token {token!r}")
        if not _HEX.match(value):
            raise TokenError(
                f"{source}: token {token!r} is {value!r}, not a 6-digit uppercase hex"
            )
        lines.append(f'  {key}: "{value}"')
    radius = tokens.get(RADIUS_NAME)
    if radius is None or not _LENGTH.match(radius):
        raise TokenError(f"{source}: token {RADIUS_NAME!r} must be a rem length")
    return "\n".join(lines) + "\n" + FOOTER + f"radius: {radius}\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        text = render(args.source)
    except TokenError as exc:
        print(f"FAIL {exc}")
        return 1
    if args.check:
        try:
            current = args.out.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"FAIL cannot read {args.out}: {exc}")
            return 1
        if current != text:
            print(f"FAIL drift: {args.out} differs from what {args.source} generates")
            return 1
        print(f"ok   {args.out} is current")
        return 0
    args.out.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
