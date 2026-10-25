# SPDX-License-Identifier: Apache-2.0
"""Safe handling of the files ``counts refresh`` reads and writes.

* ``plan_paths`` fixes the numbers, status and lock paths and refuses any layout
  in which an output could be the config (or another output).
* ``FileLock`` serializes whole refresh cycles with an exclusive ``flock``.
* ``write_atomic`` writes through a directory descriptor opened once, so a
  parent directory swapped for a symlink mid-run cannot redirect the write.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from reach_around.config.numbers import NumbersEntry, parse_numbers

LOCK_WAIT_SECONDS = 120.0
_LOCK_POLL = 0.05
_FORBIDDEN_SUFFIXES = (".yaml", ".yml")


def utc_stamp(moment: datetime) -> str:
    """``moment`` as ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dump_numbers(entries: Mapping[str, NumbersEntry]) -> str:
    """Numbers-file text in the shape the build reads (see config/numbers.py).

    The text is parsed again with the build's own parser before it is returned,
    so what refresh writes is exactly what ``build --numbers`` accepts.
    """
    platforms = {
        key: {"count": entries[key].count, "as_of": utc_stamp(entries[key].as_of)}
        for key in sorted(entries)
    }
    text = json.dumps({"version": 1, "platforms": platforms}, indent=2) + "\n"
    parse_numbers(text.encode("utf-8"), source="the numbers to write")
    return text


class LockBusy(Exception):
    """Another refresh held the lock for longer than we were willing to wait."""


class LockLost(Exception):
    """The lock file or its folder was replaced while we were locking it."""


@dataclass(frozen=True)
class Paths:
    config: Path
    target: Path
    status: Path
    lock: Path


def _in_real_parent(path: Path) -> Path:
    if path.name in ("", ".", ".."):
        raise ValueError(f"{path} is not a file name")
    try:
        parent = path.parent.resolve(strict=True)
    except OSError:
        raise ValueError(f"the folder for {path} does not exist") from None
    if not parent.is_dir():
        raise ValueError(f"the folder for {path} is not a directory")
    return parent / path.name


def plan_paths(config_path: Path, out: Path | None) -> Paths:
    """Resolve and check the config, numbers, status and lock paths.

    Raises ``ValueError`` if any two are the same file (by resolved path, or by
    device and inode when both exist, which catches hardlinks), if an output is
    a symlink or a directory, or if an output name looks like a config file.
    """
    config = config_path.resolve(strict=True)
    wanted = out if out is not None else config.parent / "numbers.json"
    target = _in_real_parent(wanted)
    status = target.with_name(f"{target.stem}.status.json")
    lock = target.with_name(f"{target.name}.lock")
    outputs = {"numbers file": target, "status file": status, "lock file": lock}
    for label, path in outputs.items():
        if path.name.lower().endswith(_FORBIDDEN_SUFFIXES):
            raise ValueError(f"the {label} name {path.name} looks like a config file")
        if path.is_symlink():
            raise ValueError(f"the {label} {path} is a symlink")
        if path.is_dir():
            raise ValueError(f"the {label} {path} is a directory")
    named = {"config": config, **outputs}
    labels = list(named)
    for i, first in enumerate(labels):
        for second in labels[i + 1 :]:
            if named[first] == named[second]:
                raise ValueError(f"the {first} and the {second} are the same path")
    seen: dict[tuple[int, int], str] = {}
    for label, path in named.items():
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        key = (info.st_dev, info.st_ino)
        if key in seen:
            raise ValueError(f"the {seen[key]} and the {label} are the same file")
        seen[key] = label
    return Paths(config, target, status, lock)


class FileLock:
    """Exclusive advisory lock for one output folder; waits up to ``wait`` seconds.

    Two locks are taken. The folder itself is locked first, so two runs cannot
    overlap even if the lock file is deleted or replaced while one is running
    (a new file would be a new inode, and a lock on the old one protects no
    one). Then the lock file is locked and checked: after ``flock`` succeeds
    the name must still lead to the file that was locked (same device and
    inode, no symlink followed); if not, the lock is dropped and taken again,
    a few times at most, then ``LockLost`` is raised. Do not delete the lock
    file while a refresh runs. ``after_dir_lock`` and ``after_lock`` are test
    hooks that run after the folder lock and after each lock-file ``flock``, each
    just before its check.
    """

    ATTEMPTS = 5

    def __init__(
        self,
        path: Path,
        *,
        wait: float = LOCK_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        after_lock: Callable[[], None] | None = None,
        after_dir_lock: Callable[[], None] | None = None,
    ) -> None:
        self._path = path
        self._wait = wait
        self._clock = clock
        self._sleep = sleep
        self._after_lock = after_lock
        self._after_dir_lock = after_dir_lock
        self._fds: list[int] = []
        self.identity: tuple[int, int] | None = None

    def _flock(self, fd: int, deadline: float) -> None:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if self._clock() >= deadline:
                    raise LockBusy from None
                self._sleep(_LOCK_POLL)

    def __enter__(self) -> FileLock:
        deadline = self._clock() + self._wait
        dir_fd = os.open(
            self._path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        self._fds.append(dir_fd)
        try:
            self._flock(dir_fd, deadline)
            if self._after_dir_lock is not None:
                self._after_dir_lock()
            if _identity(os.fstat(dir_fd)) != _identity(
                os.stat(self._path.parent, follow_symlinks=False)
            ):
                raise LockLost("the output folder was replaced while locking")
            for _ in range(self.ATTEMPTS):
                fd = os.open(
                    self._path.name,
                    os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=dir_fd,
                )
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode):
                        raise OSError("lock file is not a regular file")
                    self._flock(fd, deadline)
                    if self._after_lock is not None:
                        self._after_lock()
                    try:
                        named = os.stat(
                            self._path.name, dir_fd=dir_fd, follow_symlinks=False
                        )
                    except FileNotFoundError:
                        named = None
                    if named is not None and _identity(named) == _identity(info):
                        self._fds.append(fd)
                        self.identity = _identity(info)
                        return self
                except BaseException:
                    os.close(fd)
                    raise
                os.close(fd)  # the name no longer leads here: lock the new file
            raise LockLost("the lock file keeps being replaced")
        except BaseException:
            self._release()
            raise

    def _release(self) -> None:
        while self._fds:
            os.close(self._fds.pop())  # closing a descriptor drops its flock

    def __exit__(self, *exc: object) -> None:
        self._release()


def _identity(info: os.stat_result) -> tuple[int, int]:
    return (info.st_dev, info.st_ino)


def write_atomic(
    path: Path,
    text: str,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    """Write ``text`` to ``path``: temp file, fsync, rename, mode 0600.

    The directory is opened once (no symlink followed for it) and every step
    uses that descriptor, so swapping the directory's path for a symlink while
    the write runs cannot move the file elsewhere. ``before_replace`` is a test
    hook that runs just before the rename.
    """
    dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    tmp_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(
            tmp_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            try:
                handle = os.fdopen(fd, "w", encoding="utf-8")
            except BaseException:
                os.close(fd)  # fdopen did not take ownership
                raise
            with handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if before_replace is not None:
                before_replace()
            os.replace(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            raise
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
