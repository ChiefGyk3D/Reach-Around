# SPDX-License-Identifier: Apache-2.0
"""Where the private PDF may go, and how it is written.

The PDF holds the gated kit, so the destination is checked before anything is
rendered and the write is careful:

* the directory and every parent of it must be free of symbolic links (a link is
  refused, never followed);
* it may not be, contain or sit inside a directory that is published or kept in
  the repository: the public site output, the gated directory, or the directory
  of the config; and it may not be inside a git work tree at all (a ``.git``
  entry in it or any parent);
* an existing directory must belong to the owner and be closed to everyone else;
  a missing one is created owner-only;
* the file name is one plain component ending in ``.pdf``; an existing file is
  replaced only with ``overwrite``, and a symbolic link or directory at that
  name is never replaced;
* the bytes go to a new owner-only temporary file in the same directory and are
  renamed into place, so a failure leaves no partial target.
"""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import stat
from collections.abc import Iterable
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.pdf")


class OutputError(ValueError):
    """The PDF may not be written there."""


def check_name(name: str) -> str:
    if not NAME.fullmatch(name) or ".." in name:
        raise OutputError(
            "the output name must be a plain file name made of letters, digits, "
            "dot, dash and underscore, ending in .pdf"
        )
    return name


def _no_links(path: Path) -> None:
    """Refuse a symbolic link in ``path`` or any parent that exists."""
    probe = Path("/")
    for part in path.parts[1:]:
        probe = probe / part
        try:
            info = os.lstat(probe)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            raise OutputError(
                "the output directory goes through a symbolic link; use the real path"
            )


def _check_owner_dir(path: Path) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise OutputError("the output path is not a directory")
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise OutputError(
            "the output directory is not owner-only; fix it with chmod 700 or "
            "choose another directory"
        )


def confine_out_dir(out_dir: Path, forbidden: Iterable[Path]) -> Path:
    """Validate (and create, owner-only) the output directory; return it."""
    target = Path(os.path.abspath(out_dir))
    _no_links(target)
    for other in forbidden:
        banned = Path(os.path.realpath(other))
        if target == banned or banned in target.parents or target in banned.parents:
            raise OutputError(
                f"the output directory {target} is the same as, inside or around "
                f"{banned}, which is published, gated or holds the config; choose "
                "a separate private directory"
            )
    for candidate in (target, *target.parents):
        if os.path.lexists(candidate / ".git"):
            raise OutputError(
                "the output directory is inside a git work tree; choose a "
                "directory outside every repository"
            )
    if os.path.lexists(target):
        _check_owner_dir(target)
        return target
    missing: list[Path] = []
    for candidate in (target, *target.parents):
        if os.path.lexists(candidate):
            break
        missing.append(candidate)
    for candidate in reversed(missing):
        os.mkdir(candidate, 0o700 if candidate == target else 0o777)
    os.chmod(target, 0o700)
    return target


def write_private(out_dir: Path, name: str, data: bytes, *, overwrite: bool) -> Path:
    """Write ``data`` to ``out_dir/name`` as described in the module doc."""
    check_name(name)
    directory = Path(os.path.abspath(out_dir))
    _no_links(directory)
    try:
        _check_owner_dir(directory)
        dir_fd = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    except OSError as exc:
        raise OutputError(
            f"the output directory cannot be used ({exc.strerror})"
        ) from exc
    temp = f".{name}.{secrets.token_hex(8)}.tmp"
    try:
        try:
            existing = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode):
                raise OutputError(
                    f"{name} exists and is not a regular file; it is not replaced"
                )
            if not overwrite:
                raise OutputError(f"{name} exists; pass --overwrite to replace it")
        made = False
        try:
            fd = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dir_fd,
            )
            made = True
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
        except OSError as exc:
            if made:
                with contextlib.suppress(OSError):
                    os.unlink(temp, dir_fd=dir_fd)
            raise OutputError(f"the PDF could not be written ({exc.strerror})") from exc
    finally:
        os.close(dir_fd)
    return directory / name
