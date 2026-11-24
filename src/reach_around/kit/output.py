# SPDX-License-Identifier: Apache-2.0
"""Where the private PDF may go, and how it is written.

The PDF holds the gated kit, so the destination is checked before anything is
rendered and the write is careful:

* the directory is reached one component at a time from the root with
  ``openat(O_DIRECTORY | O_NOFOLLOW)``, and the descriptors stay open until the
  file is written, so no ancestor can be swapped for a link after it was checked
  (a link anywhere in the path is refused, never followed);
* it may not be, contain or sit inside a directory that is published or kept in
  the repository: the public site output, the gated directory, or the directory
  of the config; and it may not be inside a git work tree at all (a ``.git``
  entry in it or any parent) or hold a git work tree below it;
* an existing directory must belong to the owner and be closed to everyone else;
  a missing one is created owner-only;
* the file name is one plain component ending in ``.pdf``; an existing file is
  replaced only with ``overwrite``, and a symbolic link or directory at that
  name is never replaced;
* the bytes go to a new owner-only temporary file in the same directory and are
  renamed into place, so a failure leaves no partial target.

What this cannot do: every check of the path is a sample taken at one moment. A
process of the same user that renames the held directory into a published tree
and back between two samples (or after the last one) is not seen, because the
directory is where it should be at every sample. No number of extra samples
closes that; only a folder that no other process can rename closes it. So the
output must go to a folder only the owner can write, whose parent directory
no one else can write either; ``write_private`` warns (``OutputWarning``) when
the parent can be written by the group or by others.
"""

from __future__ import annotations

import contextlib
import errno
import os
import re
import secrets
import stat
import warnings
from collections.abc import Iterable, Sequence
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.pdf")


class OutputError(ValueError):
    """The PDF may not be written there."""


class OutputWarning(UserWarning):
    """The file was written, but where it went is weaker than it should be."""


JSON_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.json")


def check_name(name: str, pattern: re.Pattern[str] = NAME) -> str:
    if not pattern.fullmatch(name) or ".." in name:
        ending = ".json" if pattern is JSON_NAME else ".pdf"
        raise OutputError(
            "the output name must be a plain file name made of letters, digits, "
            f"dot, dash and underscore, ending in {ending}"
        )
    return name


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
# A directory scan for a work tree below the output directory stops here.
MAX_SCANNED_ENTRIES = 20000
MAX_SCAN_DEPTH = 16
# How many times in a row the held directory is compared with a fresh resolution
# of its path after the target is installed.
INSTALLED_CHECKS = 2


def _open_dir(name: str, parent_fd: int) -> int:
    """Open one directory component below ``parent_fd``, never through a link."""
    try:
        return os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            try:
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                info = None
            if info is not None and stat.S_ISLNK(info.st_mode):
                raise OutputError(
                    "the output directory goes through a symbolic link; use the "
                    "real path"
                ) from exc
            raise OutputError("the output path is not a directory") from exc
        raise OutputError(
            f"the output directory cannot be used ({exc.strerror})"
        ) from exc


def _ident(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return (info.st_dev, info.st_ino)


def _path_chain(path: Path) -> list[tuple[int, int]]:
    """Identities of a (real) path and each of its parents, root first."""
    found: list[tuple[int, int]] = []
    probe = Path("/")
    for part in ("", *Path(path).parts[1:]):
        probe = probe / part if part else probe
        try:
            info = os.stat(probe)
        except OSError:
            break
        found.append((info.st_dev, info.st_ino))
    return found


def _holds_worktree(fd: int) -> bool:
    """True if a ``.git`` entry is anywhere below ``fd``. Links are not followed;
    a tree too big to scan is refused, never assumed clean."""
    budget = MAX_SCANNED_ENTRIES
    stack: list[tuple[int, int]] = [(os.dup(fd), 0)]
    try:
        while stack:
            current, depth = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        budget -= 1
                        if budget < 0:
                            raise OutputError(
                                "the output directory has too many entries to check "
                                "for a git work tree; choose an empty directory"
                            )
                        if entry.name == ".git":
                            return True
                        if entry.is_dir(follow_symlinks=False):
                            if depth + 1 > MAX_SCAN_DEPTH:
                                raise OutputError(
                                    "the output directory is nested too deeply to "
                                    "check for a git work tree"
                                )
                            stack.append((_open_dir(entry.name, current), depth + 1))
            finally:
                os.close(current)
    finally:
        for left, _depth in stack:
            os.close(left)
    return False


def open_private_dir(
    out_dir: Path, forbidden: Iterable[Path] = (), *, create: bool
) -> int:
    """Open the output directory and return its descriptor (the caller closes it).

    The path is walked from the root with a descriptor per component, so what is
    checked is what is used: nothing above the directory can be swapped for a
    link in between. Every check runs before anything is created; a missing
    directory (and its missing parents) is then made with ``mkdirat`` mode 0700
    relative to the held parent. With ``create`` false a missing directory is an
    error."""
    target = Path(os.path.abspath(out_dir))
    fds: list[int] = []
    try:
        fds.append(os.open("/", _DIR_FLAGS))
        missing: list[str] = []
        for index, part in enumerate(target.parts[1:]):
            try:
                fds.append(_open_dir(part, fds[-1]))
            except FileNotFoundError:
                missing = list(target.parts[1 + index :])
                break
        if missing and not create:
            raise OutputError("the output directory does not exist")
        chain = [_ident(fd) for fd in fds]
        for other in forbidden:
            try:
                banned = Path(os.path.realpath(other))
                banned_chain = _path_chain(banned)
            except OSError:
                continue
            if len(banned_chain) != len(banned.parts):
                # not there (yet): only the names can collide
                inside = target == banned or banned in target.parents
                around = target in banned.parents
            else:
                inside = banned_chain[-1] in chain
                around = not missing and chain[-1] in banned_chain
            if inside or around:
                raise OutputError(
                    f"the output directory {target} is the same as, inside or "
                    f"around {banned}, which is published, gated or holds the "
                    "config; choose a separate private directory"
                )
        for fd in fds:
            if _has_entry(fd, ".git"):
                raise OutputError(
                    "the output directory is inside a git work tree; choose a "
                    "directory outside every repository"
                )
        if not missing:
            info = os.fstat(fds[-1])
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise OutputError(
                    "the output directory is not owner-only; fix it with chmod 700 "
                    "or choose another directory"
                )
            if _holds_worktree(fds[-1]):
                raise OutputError(
                    "the output directory holds a git work tree; choose a "
                    "directory with no repository in it"
                )
        for part in missing:
            try:
                os.mkdir(part, 0o700, dir_fd=fds[-1])
            except OSError as exc:
                raise OutputError(
                    f"the output directory could not be created ({exc.strerror})"
                ) from exc
            fds.append(_open_dir(part, fds[-1]))
            os.fchmod(fds[-1], 0o700)
        return fds.pop()
    finally:
        for fd in fds:
            os.close(fd)


def _has_entry(fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except OSError:
        return False
    return True


def _warn_if_parent_is_open(dir_fd: int) -> None:
    """Warn when the group or others can write to the parent of the held
    directory: they could rename the folder (see the module doc)."""
    try:
        parent = os.stat("..", dir_fd=dir_fd)
    except OSError:
        return
    if parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        warnings.warn(
            "the parent of the output directory can be written by the group or by "
            "others, so the folder could be moved; keep private output in a "
            "folder whose parent only you can write to",
            OutputWarning,
            stacklevel=3,
        )


def verify_unchanged(
    directory: Path, dir_fd: int, forbidden: Iterable[Path] = ()
) -> None:
    """Fail unless ``directory`` still resolves, by the same descriptor-relative
    walk and the same forbidden comparison, to the directory held in ``dir_fd``
    (same device and inode) and that directory is still linked. A directory that
    was renamed, replaced or removed after it was checked is refused."""
    changed = OutputError(
        "the output directory changed while the file was written; nothing was kept"
    )
    if os.fstat(dir_fd).st_nlink == 0:
        raise changed
    try:
        again = open_private_dir(directory, forbidden, create=False)
    except OutputError as exc:
        raise changed from exc
    try:
        if _ident(again) != _ident(dir_fd):
            raise changed
    finally:
        os.close(again)


def confine_out_dir(out_dir: Path, forbidden: Iterable[Path]) -> Path:
    """Validate (and create, owner-only) the output directory; return it."""
    os.close(open_private_dir(out_dir, forbidden, create=True))
    return Path(os.path.abspath(out_dir))


def write_private(
    out_dir: Path,
    name: str,
    data: bytes,
    *,
    overwrite: bool,
    pattern: re.Pattern[str] = NAME,
    forbidden: Sequence[Path] = (),
) -> Path:
    """Write ``data`` to ``out_dir/name`` as described in the module doc. The name
    must match ``pattern`` (a ``.pdf`` name unless the caller says otherwise).
    The directory is opened again here, by the same descriptor-relative walk and
    the same ``forbidden`` list, and the write goes through that descriptor."""
    check_name(name, pattern)
    directory = Path(os.path.abspath(out_dir))
    dir_fd = open_private_dir(directory, forbidden, create=False)
    _warn_if_parent_is_open(dir_fd)
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
            verify_unchanged(directory, dir_fd, forbidden)
            os.replace(temp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
            try:
                # The target is installed through the held descriptor; what
                # follows only samples where that directory is. A sample sees a
                # directory that is elsewhere at that moment; it cannot see one
                # that was moved into a published tree and back between two
                # samples, or moved after the last. More samples do not close
                # that (see the module doc and docs/kit.md).
                for _ in range(INSTALLED_CHECKS):
                    verify_unchanged(directory, dir_fd, forbidden)
            except OutputError:
                with contextlib.suppress(OSError):
                    os.unlink(name, dir_fd=dir_fd)
                raise
        except OutputError:
            if made:
                with contextlib.suppress(OSError):
                    os.unlink(temp, dir_fd=dir_fd)
            raise
        except OSError as exc:
            if made:
                with contextlib.suppress(OSError):
                    os.unlink(temp, dir_fd=dir_fd)
            raise OutputError(
                f"the file could not be written ({exc.strerror})"
            ) from exc
    finally:
        os.close(dir_fd)
    return directory / name
