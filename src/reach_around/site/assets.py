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
from collections.abc import Iterator
from pathlib import Path

from reach_around.config.models import Link, Section, SiteConfig
from reach_around.site.assetcheck import check_content
from reach_around.site.theme import IMAGE_EXTENSIONS, RESERVED_FOLDERS

MAX_ASSET_BYTES = 8 * 1024 * 1024
# A link thumbnail is drawn at 44 px; the build does not resize, so it caps.
MAX_THUMBNAIL_BYTES = 256 * 1024
FONT_EXTENSIONS = (".woff2", ".woff", ".ttf", ".otf")

_KINDS: dict[str, tuple[str, ...]] = {
    "image": IMAGE_EXTENSIONS,
    "thumbnail": IMAGE_EXTENSIONS,
    "font": FONT_EXTENSIONS,
}


def _is_local(value: str) -> bool:
    return ":" not in value and not value.startswith("//")


def _thumb(where: str, link: Link) -> Iterator[tuple[str, str, str]]:
    if link.image is not None:
        yield f"{where}.image", link.image, "thumbnail"


def _slots(config: SiteConfig) -> Iterator[tuple[str, str, str]]:
    """Yield (config key, relative path, kind) for every local file named."""
    creator, theme = config.creator, config.theme
    for key, value in (
        ("creator.avatar", creator.avatar),
        ("creator.favicon", creator.favicon),
        ("theme.background", theme.background),
        ("theme.mascot", theme.mascot),
    ):
        if value is not None and _is_local(value):
            yield key, value, "image"
    for key, font in (
        ("theme.fonts.body", theme.fonts.body),
        ("theme.fonts.heading", theme.fonts.heading),
    ):
        if font is not None and font.lower().endswith(FONT_EXTENSIONS):
            yield key, font, "font"
    for t, tab in enumerate(config.tabs):
        for b, block in enumerate(tab.blocks):
            src = getattr(block, "src", None)
            if isinstance(src, str) and _is_local(src):
                yield f"tabs[{t}].blocks[{b}].src", src, "image"
            for c, entry in enumerate(getattr(block, "coins", ())):
                if entry.qr is not None:
                    yield f"tabs[{t}].blocks[{b}].coins[{c}].qr", entry.qr, "image"
            shown = f"tabs[{t}].blocks[{b}]"
            if isinstance(block, Link):
                yield from _thumb(shown, block)
            elif isinstance(block, Section):
                for n, item in enumerate(block.links):
                    yield from _thumb(f"{shown}.links[{n}]", item)


def referenced_assets(config: SiteConfig) -> list[str]:
    """Return the relative paths the config names, sorted and unique."""
    return sorted({rel for _key, rel, _kind in _slots(config)})


def _check_slot(key: str, rel: str, kind: str) -> None:
    """The one place every slot is checked before a file is touched."""
    allowed = _KINDS[kind]
    if not rel.lower().endswith(allowed):
        article = "an" if kind == "image" else "a"
        raise ValueError(
            f"{key}: {rel!r} must be {article} {kind} file "
            f"ending in {', '.join(allowed)}"
        )
    parts = rel.split("/")
    if rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"{key}: {rel!r} must be a plain relative path")
    if parts[0] in RESERVED_FOLDERS:
        raise ValueError(f"{key}: {rel!r} is inside a folder the build writes itself")


def _open_dir(name: str, dir_fd: int | None, shown: str) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY | os.O_CLOEXEC
    try:
        return os.open(name, flags, dir_fd=dir_fd)
    except OSError as exc:
        try:
            is_link = stat.S_ISLNK(
                os.stat(name, dir_fd=dir_fd, follow_symlinks=False).st_mode
            )
        except OSError:
            is_link = False
        if exc.errno == errno.ELOOP or is_link:
            raise ValueError(
                f"{shown} is or passes through a symlink, which is not allowed"
            ) from exc
        raise ValueError(
            f"cannot read {shown}: {exc.strerror or 'unavailable'}"
        ) from exc


def _read(
    root: Path,
    rel: str,
    limit: int = MAX_ASSET_BYTES,
    too_big: str | None = None,
) -> bytes:
    """Read ``rel`` below ``root`` one component at a time.

    Every directory and the file are opened relative to the previous
    descriptor with ``O_NOFOLLOW``, so no component, root included, can be a
    symlink and nothing depends on ``/proc``. The result is capped by counting
    the bytes actually read, not by trusting a size.
    """
    fds: list[int] = []
    try:
        cur = _open_dir(os.fspath(root), None, str(root))
        fds.append(cur)
        *folders, leaf = rel.split("/")
        for part in folders:
            cur = _open_dir(part, cur, rel)
            fds.append(cur)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            fd = os.open(leaf, flags, dir_fd=cur)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(
                    f"{rel} is a symlink, which is not allowed for instance files"
                ) from exc
            raise ValueError(
                f"cannot read {rel} from {root}: {exc.strerror or 'unavailable'}"
            ) from exc
        fds.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{rel} must be a regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                if too_big is not None:
                    raise ValueError(too_big)
                raise ValueError(
                    f"{rel} is larger than {limit // 2**20} MiB"
                    if limit >= 2**20
                    else f"{rel} is larger than {limit} bytes"
                )
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        for fd in reversed(fds):
            os.close(fd)


def collect_assets(config: SiteConfig, assets_dir: Path | None) -> dict[Path, bytes]:
    """Read every file the config names; the result maps output path to bytes."""
    slots = list(_slots(config))
    if not slots:
        return {}
    if assets_dir is None:
        names = ", ".join(sorted({rel for _k, rel, _t in slots})[:3])
        raise ValueError(
            f"the config names local files ({names}) but no assets_dir was given "
            "to find them in"
        )
    files: dict[Path, bytes] = {}
    for key, rel, kind in slots:
        _check_slot(key, rel, kind)
        if kind == "thumbnail":
            kib = MAX_THUMBNAIL_BYTES // 1024
            _read(
                assets_dir,
                rel,
                MAX_THUMBNAIL_BYTES,
                f"{key}: {rel!r} is larger than {kib} KiB; a thumbnail is drawn "
                "at 44 px, so resize it (for example 128 px wide) and try again",
            )
        if Path(rel) not in files:
            data = _read(assets_dir, rel)
            check_content(key, rel, data)
            files[Path(rel)] = data
    return files
