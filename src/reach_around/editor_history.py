# SPDX-License-Identifier: Apache-2.0
"""Undo and redo history for the local editor.

The history lives in ``.reach-around-history/`` beside the config: one file per
snapshot of the config text (``NNNN-<hash8>.yaml``) plus ``index.json`` with
the labels, times, hashes, the cursor and the hash the editor produced last.
The folder is mode 0700 and every file 0600; everything is written through a
directory descriptor (never by path), staged, synced and renamed into place.
A symlinked folder, snapshot or index is never followed.

States form one line. A new write drops every state after the cursor (redo is
cleared). Undo and redo move the cursor, and are allowed only while the file
on disk still hashes to what the editor produced last; otherwise the file was
edited elsewhere and a typed ``Refusal`` comes back. ``restore_anyway`` first
keeps the outside version as a state, so even that step can be undone.

This module never writes the config. Callers pass a ``write`` callback (the
locked write path); the cursor moves only after it returns.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

HISTORY_DIRNAME = ".reach-around-history"
IGNORE_LINE = f"{HISTORY_DIRNAME}/"
MAX_ENTRIES = 100
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 1_048_576  # the config core's own limit
MAX_INDEX_BYTES = 262_144
MAX_LABEL = 80
LOCK_TIMEOUT = 10.0
LOCK_POLL = 0.02
_VERSION = 1
_HEX64 = re.compile(r"[0-9a-f]{64}")
_SNAP_NAME = re.compile(r"[0-9]{4,}-[0-9a-f]{8}\.yaml")


class HistoryError(Exception):
    """The history cannot be used safely; nothing was changed."""


@dataclass(frozen=True)
class Refusal:
    """Undo or redo was refused. ``code`` is stable; ``message`` is for people."""

    code: str
    message: str


@dataclass(frozen=True)
class Restored:
    text: str
    label: str
    can_undo: bool
    can_redo: bool
    #: False when the file was restored but the history pointer could not be
    #: saved; the next read repairs it from the hash the file now has.
    recorded: bool = True


@dataclass(frozen=True)
class HistoryEntry:
    """One state as the UI may see it: no text, only a label, a time and a hash."""

    id: int
    hash: str
    label: str
    time: float
    current: bool


@dataclass(frozen=True)
class Summary:
    entries: tuple[HistoryEntry, ...]
    can_undo: bool
    can_redo: bool
    produced: str | None


@dataclass
class _Entry:
    n: int
    hash: str
    label: str
    time: float
    size: int

    def filename(self) -> str:
        return f"{self.n:04d}-{self.hash[:8]}.yaml"


@dataclass
class _State:
    entries: list[_Entry]
    cursor: int
    next: int
    produced: str | None
    #: Written before a restore touches the file: (cursor, hash) it will have
    #: afterwards. If the final save fails, the next read adopts it when the
    #: file really has that hash, so a restore is never mistaken for an
    #: outside edit.
    pending: tuple[int, str] | None = None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode(text: str, what: str) -> bytes:
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        raise HistoryError(f"{what} is not valid text") from None
    if len(data) > MAX_SNAPSHOT_BYTES:
        raise HistoryError(f"{what} is too large for the history")
    return data


def _clean_label(label: str) -> str:
    kept = "".join(ch if ch.isprintable() else " " for ch in label)
    return " ".join(kept.split())[:MAX_LABEL]


# ---- low-level file helpers (all relative to a directory descriptor) ----


def _fsync(fd: int) -> None:
    os.fsync(fd)


def _write_atomic(dir_fd: int, name: str, data: bytes) -> None:
    tmp = f".tmp-{secrets.token_hex(8)}"
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dir_fd
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            os.fsync(handle.fileno())
        os.rename(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise
    _fsync(dir_fd)


def _read_limited(dir_fd: int, name: str, limit: int) -> bytes | None:
    """Read ``name`` (never through a symlink); None if absent, unsafe or too big."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = b""
        while len(data) <= limit:
            chunk = os.pread(fd, 65536, len(data))
            if not chunk:
                break
            data += chunk
    except OSError:
        return None
    finally:
        os.close(fd)
    return data if len(data) <= limit else None


def _unlink(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


# ---- index ----


def _fresh() -> _State:
    return _State([], -1, 1, None)


def _parse_index(raw: bytes) -> _State | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return None
    items, cursor, nxt, produced = (
        data.get("entries"),
        data.get("cursor"),
        data.get("next"),
        data.get("produced"),
    )
    if not isinstance(items, list) or len(items) > 1000:
        return None
    entries: list[_Entry] = []
    for item in items:
        if not isinstance(item, dict):
            return None
        n, digest, label, when, size = (
            item.get("n"),
            item.get("hash"),
            item.get("label"),
            item.get("time"),
            item.get("size"),
        )
        if (
            type(n) is not int
            or n < 1
            or (entries and n <= entries[-1].n)
            or not isinstance(digest, str)
            or not _HEX64.fullmatch(digest)
            or not isinstance(label, str)
            or isinstance(when, bool)
            or not isinstance(when, int | float)
            or type(size) is not int
            or not 0 <= size <= MAX_SNAPSHOT_BYTES
        ):
            return None
        entries.append(_Entry(n, digest, _clean_label(label), float(when), size))
    expected_cursor = range(len(entries)) if entries else range(-1, 0)
    if type(cursor) is not int or cursor not in expected_cursor:
        return None
    if type(nxt) is not int or nxt <= (entries[-1].n if entries else 0):
        return None
    if produced is not None and not (
        isinstance(produced, str) and _HEX64.fullmatch(produced)
    ):
        return None
    pending = None
    raw_pending = data.get("pending")
    if raw_pending is not None:
        if not isinstance(raw_pending, dict):
            return None
        p_cursor, p_hash = raw_pending.get("cursor"), raw_pending.get("hash")
        if (
            type(p_cursor) is not int
            or p_cursor not in range(len(entries))
            or not isinstance(p_hash, str)
            or not _HEX64.fullmatch(p_hash)
        ):
            return None
        pending = (p_cursor, p_hash)
    return _State(entries, cursor, nxt, produced, pending)


def _dump_index(state: _State) -> bytes:
    return json.dumps(
        {
            "version": _VERSION,
            "cursor": state.cursor,
            "next": state.next,
            "produced": state.produced,
            "pending": (
                None
                if state.pending is None
                else {"cursor": state.pending[0], "hash": state.pending[1]}
            ),
            "entries": [
                {
                    "n": e.n,
                    "hash": e.hash,
                    "label": e.label,
                    "time": e.time,
                    "size": e.size,
                }
                for e in state.entries
            ],
        },
        indent=1,
    ).encode("utf-8")


class History:
    def __init__(
        self,
        config: Path,
        *,
        max_entries: int = MAX_ENTRIES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ) -> None:
        self.config = Path(os.path.abspath(config))
        self.max_entries = max_entries
        self.max_total_bytes = max_total_bytes

    @property
    def directory(self) -> Path:
        return self.config.parent / HISTORY_DIRNAME

    # -- directory and lock --

    def _open_dir(self, *, create: bool) -> int | None:
        parent = os.open(self.config.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                info = os.stat(HISTORY_DIRNAME, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    return None
                try:
                    os.mkdir(HISTORY_DIRNAME, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass  # another writer made it first; checked just below
                info = os.stat(HISTORY_DIRNAME, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise HistoryError(
                    f"{self.directory}: the history folder is a symlink; refusing"
                )
            if not stat.S_ISDIR(info.st_mode):
                raise HistoryError(f"{self.directory}: not a directory; refusing")
            try:
                fd = os.open(
                    HISTORY_DIRNAME,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
            except OSError as exc:
                raise HistoryError(
                    f"{self.directory}: cannot open the history folder: {exc.strerror}"
                ) from exc
        except OSError as exc:
            raise HistoryError(
                f"{self.directory}: cannot use the history folder: {exc.strerror}"
            ) from exc
        finally:
            os.close(parent)
        try:
            if os.fstat(fd).st_uid != os.geteuid():
                raise HistoryError(f"{self.directory}: not owned by you; refusing")
            os.fchmod(fd, 0o700)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _lock(self, dir_fd: int) -> int:
        deadline = time.monotonic() + LOCK_TIMEOUT
        try:
            lock = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            raise HistoryError(f"cannot open the history lock: {exc.strerror}") from exc
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return lock
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(lock)
                    raise HistoryError(
                        "the history is locked by another edit; nothing was changed"
                    ) from None
                time.sleep(LOCK_POLL)
            except OSError as exc:
                os.close(lock)
                raise HistoryError(f"cannot lock the history: {exc.strerror}") from exc

    # -- state --

    def _load(self, dir_fd: int) -> _State:
        raw = _read_limited(dir_fd, "index.json", MAX_INDEX_BYTES)
        if raw is None:
            try:
                os.stat("index.json", dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                return _fresh()
            except OSError:
                pass
        else:
            parsed = _parse_index(raw)
            if parsed is not None:
                return parsed
        # Present but unusable: keep it for inspection and start clean.
        try:
            os.rename(
                "index.json",
                f"index.json.corrupt-{int(time.time())}-{secrets.token_hex(2)}",
                src_dir_fd=dir_fd,
                dst_dir_fd=dir_fd,
            )
        except OSError:
            _unlink(dir_fd, "index.json")
        return _fresh()

    def _save(self, dir_fd: int, state: _State) -> None:
        _write_atomic(dir_fd, "index.json", _dump_index(state))
        keep = {e.filename() for e in state.entries}
        for name in os.listdir(dir_fd):
            if _SNAP_NAME.fullmatch(name) and name not in keep:
                _unlink(dir_fd, name)

    def _resolve(self, dir_fd: int, state: _State, current_hash: str | None) -> None:
        """Settle a ``pending`` restore against the hash the file has now."""
        pending = state.pending
        if pending is None or current_hash is None:
            return
        state.pending = None
        if current_hash == pending[1]:  # the write landed, the pointer save did not
            state.cursor, state.produced = pending
        try:
            self._save(dir_fd, state)
        except (OSError, HistoryError):
            pass  # best effort; the in-memory state is already right

    def _store(self, dir_fd: int, state: _State, text: str, label: str) -> _Entry:
        data = _encode(text, "the snapshot")
        entry = _Entry(
            state.next,
            hashlib.sha256(data).hexdigest(),
            _clean_label(label),
            time.time(),
            len(data),
        )
        _write_atomic(dir_fd, entry.filename(), data)
        state.next += 1
        state.entries.append(entry)
        return entry

    def _prune(self, state: _State) -> None:
        """Drop the oldest states over the caps; never the current one or later."""

        def total() -> int:
            return sum(e.size for e in state.entries)

        while state.cursor > 0 and (
            len(state.entries) > self.max_entries or total() > self.max_total_bytes
        ):
            del state.entries[0]
            state.cursor -= 1

    def _read_state_text(self, dir_fd: int, entry: _Entry) -> str | Refusal:
        data = _read_limited(dir_fd, entry.filename(), MAX_SNAPSHOT_BYTES)
        if data is None:
            return Refusal(
                "snapshot_unreadable", "That earlier version is missing or unreadable."
            )
        if hashlib.sha256(data).hexdigest() != entry.hash:
            return Refusal(
                "snapshot_corrupt",
                "That earlier version was changed; not restoring it.",
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return Refusal(
                "snapshot_corrupt", "That earlier version is not valid text."
            )

    # -- public API --

    def record_write(self, old: str, new: str, label: str) -> None:
        """Keep a snapshot of ``old`` (if new to us) and of ``new``; clear redo."""
        _encode(old, "the previous text")
        _encode(new, "the new text")
        if old == new:
            return
        dir_fd = self._open_dir(create=True)
        if dir_fd is None:  # not reachable with create=True
            raise HistoryError(f"{self.directory}: cannot create the history folder")
        try:
            lock = self._lock(dir_fd)
            try:
                state = self._load(dir_fd)
                old_hash = _sha(old)
                self._resolve(dir_fd, state, old_hash)
                state.pending = None
                del state.entries[state.cursor + 1 :]
                if not state.entries or state.entries[state.cursor].hash != old_hash:
                    self._store(
                        dir_fd,
                        state,
                        old,
                        "start" if not state.entries else "outside edit",
                    )
                self._store(dir_fd, state, new, label)
                state.cursor = len(state.entries) - 1
                state.produced = _sha(new)
                self._prune(state)
                self._save(dir_fd, state)
            finally:
                os.close(lock)
        finally:
            os.close(dir_fd)

    def summary(self, current_hash: str | None = None) -> Summary:
        """What the history holds now, without its text. Empty if there is none.

        ``current_hash`` (the file's hash) lets a restore whose pointer save
        failed be repaired before it is reported.
        """
        dir_fd = self._open_dir(create=False)
        if dir_fd is None:
            return Summary((), False, False, None)
        try:
            lock = self._lock(dir_fd)
            try:
                state = self._load(dir_fd)
                self._resolve(dir_fd, state, current_hash)
            finally:
                os.close(lock)
        finally:
            os.close(dir_fd)
        entries = tuple(
            HistoryEntry(e.n, e.hash, e.label, e.time, i == state.cursor)
            for i, e in enumerate(state.entries)
        )
        return Summary(
            entries,
            state.cursor > 0,
            state.cursor < len(state.entries) - 1,
            state.produced,
        )

    def undo(self, current: str, write: Callable[[str], object]) -> Restored | Refusal:
        return self._move(current, write, -1, anyway=False)

    def redo(self, current: str, write: Callable[[str], object]) -> Restored | Refusal:
        return self._move(current, write, +1, anyway=False)

    def restore_anyway(
        self, current: str, write: Callable[[str], object]
    ) -> Restored | Refusal:
        """Undo even though the file was edited elsewhere, keeping that edit first."""
        return self._move(current, write, -1, anyway=True)

    def _move(
        self,
        current: str,
        write: Callable[[str], object],
        step: int,
        *,
        anyway: bool,
    ) -> Restored | Refusal:
        dir_fd = self._open_dir(create=False)
        if dir_fd is None:
            return Refusal(*_NOTHING[step])
        try:
            lock = self._lock(dir_fd)
            try:
                return self._move_locked(dir_fd, current, write, step, anyway)
            finally:
                os.close(lock)
        finally:
            os.close(dir_fd)

    def _move_locked(
        self,
        dir_fd: int,
        current: str,
        write: Callable[[str], object],
        step: int,
        anyway: bool,
    ) -> Restored | Refusal:
        state = self._load(dir_fd)
        self._resolve(dir_fd, state, _sha(current))
        target = state.cursor + step
        if not 0 <= target < len(state.entries):
            return Refusal(*_NOTHING[step])
        outside = _sha(current) != state.produced
        if outside and not anyway:
            return Refusal(
                "outside_edit",
                "The file was changed outside the editor, so this would erase "
                "that change. Nothing was changed.",
            )
        loaded = self._read_state_text(dir_fd, state.entries[target])
        if isinstance(loaded, Refusal):
            return loaded
        if outside:
            try:
                self._store(dir_fd, state, current, "outside edit")
            except HistoryError as exc:
                return Refusal("too_large", str(exc))
            # Kept before anything is written; the cursor stays on the old
            # state until the write succeeds, so a failed write loses nothing.
            del state.entries[state.cursor + 1 : -1]
        # Record the intent before the file changes, so a failed save after
        # the write can be repaired from the hash the file will have.
        state.pending = (target, _sha(loaded))
        try:
            self._save(dir_fd, state)
        except OSError as exc:
            raise HistoryError(
                f"cannot save the history: {exc.strerror or 'I/O error'}"
            ) from exc
        write(loaded)
        state.cursor = target
        state.produced = _sha(loaded)
        state.pending = None
        recorded = True
        try:
            self._prune(state)
            self._save(dir_fd, state)
        except (OSError, HistoryError):
            # The file already changed. Never report a refusal; the index on
            # disk still holds the pending marker, which repairs the pointer.
            recorded = False
        return Restored(
            loaded,
            state.entries[state.cursor].label,
            state.cursor > 0,
            state.cursor < len(state.entries) - 1,
            recorded,
        )


_NOTHING = {
    -1: ("nothing_to_undo", "There is nothing earlier to go back to."),
    +1: ("nothing_to_redo", "There is nothing later to redo."),
}


# ---- .gitignore helper ----


def _work_tree_root(start: Path) -> Path | None:
    here = Path(os.path.abspath(start))
    for candidate in (here, *here.parents):
        if os.path.lexists(candidate / ".git"):
            return candidate
    return None


def ensure_gitignore(config: Path) -> bool:
    """Add ``IGNORE_LINE`` to the work tree's ``.gitignore``; True if it wrote.

    Does nothing (False) outside a git work tree or when the line is already
    there. A symlinked ``.gitignore`` is refused, never followed.
    """
    root = _work_tree_root(Path(config).parent)
    if root is None:
        return False
    target = root / ".gitignore"
    existing = ""
    mode = 0o644
    if os.path.lexists(target):
        if target.is_symlink():
            raise HistoryError(f"{target}: .gitignore is a symlink; refusing")
        dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            raw = _read_limited(dir_fd, ".gitignore", MAX_SNAPSHOT_BYTES)
        finally:
            os.close(dir_fd)
        if raw is None:
            raise HistoryError(f"{target}: cannot read .gitignore safely")
        existing = raw.decode("utf-8", errors="replace")
        mode = stat.S_IMODE(target.stat().st_mode)
    present = {IGNORE_LINE, f"/{IGNORE_LINE}", HISTORY_DIRNAME, f"/{HISTORY_DIRNAME}"}
    if any(line.strip() in present for line in existing.splitlines()):
        return False
    if existing and not existing.endswith("\n"):
        existing += "\n"
    data = (existing + IGNORE_LINE + "\n").encode("utf-8")
    dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        tmp = f".gitignore.tmp-{secrets.token_hex(8)}"
        fd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            mode,
            dir_fd=dir_fd,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fchmod(handle.fileno(), mode)
                os.fsync(handle.fileno())
            os.rename(tmp, ".gitignore", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            _unlink(dir_fd, tmp)
            raise
        _fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return True


__all__ = [
    "HISTORY_DIRNAME",
    "IGNORE_LINE",
    "History",
    "HistoryEntry",
    "HistoryError",
    "Refusal",
    "Restored",
    "Summary",
    "ensure_gitignore",
]
