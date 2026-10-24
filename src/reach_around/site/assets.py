# SPDX-License-Identifier: Apache-2.0
"""Copy the instance files the config names into the built site.

The config can point at files that live next to it: the avatar, the favicon,
the theme's background and mascot, theme font files and image blocks. Each is
a relative path under the instance directory. This module finds those paths,
reads each file safely and returns it under the same relative path, so the
page reaches it on its own origin and the policy never needs a new host.

Only files the config names are published. Anything else in the instance
directory (notes, drafts, the config itself) stays where it is.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

from reach_around.config.models import SiteConfig
from reach_around.site.theme import RESERVED_FOLDERS

MAX_ASSET_BYTES = 8 * 1024 * 1024
_FONT_EXTENSIONS = (".woff2", ".woff", ".ttf", ".otf")


def _is_local(value: str) -> bool:
    return ":" not in value and not value.startswith("//")


def referenced_assets(config: SiteConfig) -> list[str]:
    """Return the relative paths the config names, sorted and unique."""
    names: set[str] = set()
    creator = config.creator
    for value in (creator.avatar, creator.favicon):
        if value is not None and _is_local(value):
            names.add(value)
    theme = config.theme
    for value in (theme.background, theme.mascot):
        if value is not None and _is_local(value):
            names.add(value)
    for font in (theme.fonts.body, theme.fonts.heading):
        if font is not None and font.lower().endswith(_FONT_EXTENSIONS):
            names.add(font)
    for tab in config.tabs:
        for block in tab.blocks:
            src = getattr(block, "src", None)
            if isinstance(src, str) and _is_local(src):
                names.add(src)
    return sorted(names)


def _read(root: Path, rel: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)
    target = root / rel
    try:
        fd = os.open(target, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(
                f"{rel} is a symlink, which is not allowed for instance files"
            ) from exc
        raise ValueError(
            f"cannot read {rel} from {root}: {exc.strerror or 'unavailable'}"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{rel} must be a regular file")
        if info.st_size > MAX_ASSET_BYTES:
            raise ValueError(f"{rel} is larger than {MAX_ASSET_BYTES // 2**20} MiB")
        proc = f"/proc/self/fd/{fd}"
        if os.path.exists(proc) and root not in Path(os.path.realpath(proc)).parents:
            raise ValueError(f"{rel} resolves outside the instance directory")
        with os.fdopen(os.dup(fd), "rb") as handle:
            return handle.read(MAX_ASSET_BYTES + 1)
    finally:
        os.close(fd)


def collect_assets(config: SiteConfig, assets_dir: Path | None) -> dict[Path, bytes]:
    """Read every file the config names; the result maps output path to bytes."""
    names = referenced_assets(config)
    if not names:
        return {}
    if assets_dir is None:
        raise ValueError(
            f"the config names local files ({', '.join(names[:3])}) but no "
            "assets_dir was given to find them in"
        )
    root = assets_dir.resolve()
    files: dict[Path, bytes] = {}
    for rel in names:
        if rel.split("/", 1)[0] in RESERVED_FOLDERS:
            raise ValueError(f"{rel} is inside a folder the build writes itself")
        files[Path(rel)] = _read(root, rel)
    return files
