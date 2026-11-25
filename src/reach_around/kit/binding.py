# SPDX-License-Identifier: Apache-2.0
"""The build binding: what ties a measures file to the pages one build wrote.

At build time the gated directory gets ``kit-binding.json`` beside the policy
record. It holds digests of the display policy, of the resolved measures the
build compiled, of the gated page and of the public kit page. ``kit private-measures``
recomputes each one from the inputs it was given and the directories it was
pointed at, and refuses when any differs, so a measures file is always the
numbers of the pages it names, from one build. The file records the same
digests, and a reader can check them against an expected binding.

The binding stays in the gated directory (never published): the digests of
gated figures are not for the public tree.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from reach_around.config.measures import Measure

BINDING_FILE = Path("kit-binding.json")
BINDING_KEYS = ("policy", "measures", "gated_page", "public_page")
_HEX = re.compile(r"[0-9a-f]{64}")
MAX_BINDING_BYTES = 4096


def measure_row(m: Measure) -> dict[str, Any]:
    """One measure as the measures file prints it."""
    return {
        "platform": m.platform,
        "key": m.key,
        "label": m.label,
        "unit": m.unit,
        "value": m.value,
        "as_of": None if m.as_of is None else m.as_of.isoformat(),
        "period": None
        if m.period is None
        else [m.period[0].isoformat(), m.period[1].isoformat()],
        "visibility": m.visibility,
        "trust": None
        if m.trust is None
        else {"label": m.trust.label, "method": m.trust.method},
        "in_summary": m.in_summary,
        "formula": m.formula,
        "note": m.note,
        "provenance": m.provenance,
        "source_note": m.source_note,
    }


def sha256_text(text: str | bytes) -> str:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return hashlib.sha256(data).hexdigest()


def rows_digest_of(rows: Iterable[Mapping[str, Any]]) -> str:
    """A digest of measure rows as ``measure_row`` prints them, in page order. The
    one canonical form: the build, the writer and the reader all use it, so a
    row read back from a file digests exactly as the measure it came from."""
    return sha256_text(
        json.dumps([dict(r) for r in rows], sort_keys=True, ensure_ascii=True)
    )


def rows_digest(measures: Iterable[Measure]) -> str:
    """A digest of the resolved measures, in page order."""
    return rows_digest_of(measure_row(m) for m in measures)


def make_binding(
    policy: str,
    measures: str,
    gated_page: str | bytes | None,
    public_page: str | bytes,
) -> dict[str, str | None]:
    """The binding of one build. A kit with no gated content has no gated page:
    ``gated_page`` is then None (null in the record), and the reader requires that
    the gated directory holds no page either."""
    return {
        "policy": policy,
        "measures": measures,
        "gated_page": None if gated_page is None else sha256_text(gated_page),
        "public_page": sha256_text(public_page),
    }


def binding_text(binding: Mapping[str, str | None]) -> str:
    return json.dumps(dict(binding), sort_keys=True, indent=2) + "\n"


def check_binding_shape(found: object) -> dict[str, str | None]:
    """``found`` as a binding: exactly the four keys, each a SHA-256 hex digest
    (``gated_page`` may be null: a kit with no gated page)."""
    if not isinstance(found, dict) or set(found) != set(BINDING_KEYS):
        raise ValueError("the build binding does not have the expected keys")
    for key in BINDING_KEYS:
        value = found[key]
        if value is None and key == "gated_page":
            continue
        if not isinstance(value, str) or not _HEX.fullmatch(value):
            raise ValueError(f"the build binding {key} is not a SHA-256 digest")
    return {key: found[key] for key in BINDING_KEYS}


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

# A test seam: called after the whole directory chain is held and before the final
# file is opened, so a test can swap a directory or a link in at the worst moment.
_before_final_open: Callable[[], None] | None = None


class AnchoredReadError(ValueError):
    """The anchored read refused: the message names the problem, never the content."""


def _check_dir(fd: int, me: int) -> None:
    info = os.fstat(fd)
    if info.st_uid not in (me, 0):
        raise AnchoredReadError("a directory on the path belongs to another user")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not info.st_mode & stat.S_ISVTX:
        raise AnchoredReadError(
            "a directory on the path can be written by the group or by others"
        )


def check_file_owner(info: os.stat_result, me: int) -> None:
    """Refuse a file that another user could have planted or can change: it must
    belong to this user or root, carry no group or other write bit, and have one
    link only. (A sticky world-writable directory lets another user create a
    same-named file; a second hard link lets a file be changed from elsewhere.)"""
    if info.st_uid not in (me, 0):
        raise AnchoredReadError("the file belongs to another user")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise AnchoredReadError("the file can be written by the group or by others")
    if info.st_nlink != 1:
        raise AnchoredReadError("the file has more than one link")


def _walk_parents(parts: tuple[str, ...]) -> list[int]:
    """Open every directory above the last name of ``parts`` with a held
    descriptor (see ``read_anchored``). The caller closes the returned list."""
    me = os.geteuid()
    held: list[int] = []
    try:
        held.append(os.open("/", _DIR_FLAGS))
        _check_dir(held[-1], me)
        for part in parts[1:-1]:
            try:
                held.append(os.open(part, _DIR_FLAGS, dir_fd=held[-1]))
            except OSError as exc:
                linked = exc.errno == errno.ELOOP
                if exc.errno == errno.ENOTDIR:
                    try:
                        linked = stat.S_ISLNK(
                            os.stat(
                                part, dir_fd=held[-1], follow_symlinks=False
                            ).st_mode
                        )
                    except OSError:
                        linked = False
                if linked:
                    raise AnchoredReadError(
                        "the path goes through a symbolic link"
                    ) from exc
                raise AnchoredReadError(
                    f"a directory on the path cannot be opened ({exc.strerror})"
                ) from exc
            _check_dir(held[-1], me)
        if _before_final_open is not None:
            _before_final_open()
    except BaseException:
        for left in held:
            os.close(left)
        raise
    return held


def require_absent(path: Path) -> None:
    """Refuse when anything is named ``path``: the last name is looked up in the
    held directory chain (never through a link), not by a fresh path walk. It
    says what is there now; keeping a builder from creating it a moment later is
    the job of ``gated_lock``."""
    parts = Path(os.path.abspath(path)).parts
    if len(parts) < 2:
        raise AnchoredReadError("the path is not a file")
    held = _walk_parents(parts)
    try:
        try:
            os.stat(parts[-1], dir_fd=held[-1], follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise AnchoredReadError(
                f"the name cannot be checked ({exc.strerror})"
            ) from exc
        raise AnchoredReadError("the name is present")
    finally:
        for left in held:
            os.close(left)


def read_anchored(path: Path, limit: int) -> bytes:
    """Read a regular file at ``path`` through a held chain of directory descriptors.

    The (absolute, not link-resolved) path is walked from ``/`` one component at a
    time with ``openat(O_DIRECTORY | O_NOFOLLOW)``, so a link anywhere on the path,
    in an ancestor as well as in the last name, is refused, and a directory
    swapped for a link after it was checked cannot redirect the read: the
    descriptor already held is what is used. Every directory on the way (the root
    included) must belong to this user or root and must not be writable by the
    group or others unless sticky, so no one else could rename an ancestor and
    move the tree. The file is opened ``O_NOFOLLOW | O_NONBLOCK`` and its
    ``fstat`` must say regular file before anything is read, so a FIFO, a socket or
    a device cannot block the reader; it must also belong to this user or root,
    carry no group or other write bit and have one link (``check_file_owner``), so
    a file another user planted in a sticky directory is refused. At most
    ``limit`` bytes are returned; one more byte present means the file is too
    large and it is refused."""
    parts = Path(os.path.abspath(path)).parts
    if len(parts) < 2:
        raise AnchoredReadError("the path is not a file")
    held = _walk_parents(parts)
    try:
        try:
            fd = os.open(parts[-1], _FILE_FLAGS, dir_fd=held[-1])
        except OSError as exc:
            raise AnchoredReadError(
                f"the file cannot be opened ({exc.strerror})"
            ) from exc
    finally:
        for left in held:
            os.close(left)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise AnchoredReadError("the file is not a regular file")
        check_file_owner(info, os.geteuid())
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as handle:
        if info.st_size > limit:
            raise AnchoredReadError("the file is too large")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise AnchoredReadError("the file is too large")
    return data


# --- the gated directory lock and the interrupted-commit marker ----------------

LOCK_FILE = Path(".kit-gated.lock")
PENDING_FILE = Path(".kit-commit.pending")
LOCK_WAIT_SECONDS = 30.0


class GatedLockError(ValueError):
    """The gated directory is locked by another build or reader."""


def acquire_gated_lock(gated_dir: Path, *, exclusive: bool) -> int:
    """Take ``flock`` on ``.kit-gated.lock`` in ``gated_dir`` and return the
    descriptor (close it to release). A build holds it exclusive from staging to
    the end of the commit; the measures file, the private PDF and the binding
    reader hold it shared, so no reader sees a half-made build and no build
    appears between a reader's checks and its use. The directory is opened
    ``O_NOFOLLOW`` and the lock file is created relative to it. It waits at most
    ``LOCK_WAIT_SECONDS`` and then refuses. The lock is advisory: it orders
    this tool's own processes, it does not stop another user from writing."""
    try:
        dir_fd = os.open(gated_dir, _DIR_FLAGS)
    except OSError as exc:
        linked = exc.errno == errno.ELOOP
        if exc.errno == errno.ENOTDIR:
            try:
                linked = stat.S_ISLNK(os.lstat(gated_dir).st_mode)
            except OSError:
                linked = False
        if linked:
            raise GatedLockError(
                "the gated directory is a symbolic link; it is not followed"
            ) from exc
        raise GatedLockError(
            f"the gated directory cannot be opened ({exc.strerror})"
        ) from exc
    try:
        try:
            fd = os.open(
                LOCK_FILE.name,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                0o600,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            raise GatedLockError(
                f"the gated directory lock cannot be opened ({exc.strerror})"
            ) from exc
    finally:
        os.close(dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise GatedLockError("the gated directory lock is not a regular file")
        try:
            check_file_owner(info, os.geteuid())
        except AnchoredReadError as exc:
            raise GatedLockError(f"the gated directory lock is refused: {exc}") from exc
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        while True:
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
                return fd
            except OSError as exc:
                if exc.errno not in (errno.EAGAIN, errno.EACCES):
                    raise GatedLockError(
                        f"the gated directory cannot be locked ({exc.strerror})"
                    ) from exc
            if time.monotonic() >= deadline:
                raise GatedLockError(
                    "another build or reader holds the gated directory; "
                    "wait for it to finish and run this again"
                )
            time.sleep(0.05)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def gated_lock(gated_dir: Path, *, exclusive: bool) -> Iterator[None]:
    """``acquire_gated_lock`` for a ``with`` block."""
    fd = acquire_gated_lock(gated_dir, exclusive=exclusive)
    try:
        yield
    finally:
        os.close(fd)


def check_no_pending_commit(gated_dir: Path) -> None:
    """Refuse a gated directory whose last build was interrupted while it
    replaced the gated files (the build leaves ``.kit-commit.pending`` until its
    last step): the page, the policy record and the binding may be from
    different builds. Rebuild to clear it."""
    try:
        require_absent(gated_dir / PENDING_FILE)
    except AnchoredReadError as exc:
        raise ValueError(
            f"the gated directory is not in a finished state ({exc}); "
            "a build was interrupted. Rebuild the site first "
            "(reach-around build --gated-out)"
        ) from exc


def read_binding(path: Path) -> dict[str, str | None]:
    """Read the binding record of a build, through ``read_anchored``. A missing,
    linked (in the last name or any ancestor), non-regular, oversized or malformed
    record is refused with a message that says to rebuild."""
    rebuild = "rebuild the site first (reach-around build --gated-out)"
    try:
        raw = read_anchored(path, MAX_BINDING_BYTES)
    except AnchoredReadError as exc:
        raise ValueError(
            f"the build has no usable binding record ({exc}); {rebuild}"
        ) from exc
    try:
        return check_binding_shape(json.loads(raw.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"the build binding record is not valid; {rebuild}") from exc
