# SPDX-License-Identifier: Apache-2.0
"""Test helper: find the content-hashed file for a logical asset name.

The build publishes ``static/site.css`` as ``static/site.<10 hex>.css``. Tests
name files the way a template does and this helper maps that to the built path,
so a test never hard-codes a hash and never accepts the unhashed name.
"""

from __future__ import annotations

import re
from pathlib import Path

HASH = r"[0-9a-f]{10}"


def hashed_re(logical: str) -> re.Pattern[str]:
    """Regex matching the hashed form of ``logical`` (a site-root-relative path)."""
    path = Path(logical)
    stem, suffix = path.stem, path.suffix
    prefix = f"{path.parent.as_posix()}/" if str(path.parent) != "." else ""
    return re.compile(
        re.escape(prefix) + re.escape(stem) + r"\." + HASH + re.escape(suffix)
    )


def asset(out: Path, logical: str) -> str:
    """The built, hashed path of ``logical`` relative to ``out``; fails if absent."""
    pattern = hashed_re(logical)
    found = sorted(
        p.relative_to(out).as_posix()
        for p in out.rglob("*")
        if p.is_file() and pattern.fullmatch(p.relative_to(out).as_posix())
    )
    assert len(found) == 1, f"{logical}: expected one hashed file, found {found}"
    return found[0]


def asset_file(out: Path, logical: str) -> Path:
    return out / asset(out, logical)


def has_asset(out: Path, logical: str) -> bool:
    pattern = hashed_re(logical)
    return any(
        p.is_file() and pattern.fullmatch(p.relative_to(out).as_posix())
        for p in out.rglob("*")
    )


def href(out: Path, logical: str, root: str = "") -> str:
    """The reference a page at ``root`` ("" or "../" or "/") uses for ``logical``."""
    return root + asset(out, logical)


def read(out: Path, logical: str) -> str:
    return asset_file(out, logical).read_text(encoding="utf-8")


def png(tag: str) -> bytes:
    """A tiny file the asset check accepts as a PNG; ``tag`` makes the bytes differ."""
    return b"\x89PNG\r\n\x1a\n" + tag.encode() + b"\x00\x00\x00\x00IEND\xaeB`\x82"
