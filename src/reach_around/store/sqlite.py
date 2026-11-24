# SPDX-License-Identifier: Apache-2.0
"""The SQLite adapter: WAL, a busy timeout, foreign keys, one connection per thread."""

from __future__ import annotations

import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path
from typing import Literal

from reach_around.store.base import SqlStore
from reach_around.store.migrate import Migration
from reach_around.store.models import StoreError

BUSY_TIMEOUT_SECONDS = 10.0
KEEP_BACKUPS = 3
_BACKUP = re.compile(r"\.pre-\d{4}$")


class _State(threading.local):
    depth = 0


class SqliteDb:
    """``Db`` over ``sqlite3``. ``":memory:"`` uses one guarded connection."""

    dialect: Literal["sqlite", "postgres"] = "sqlite"

    def __init__(
        self, path: Path | str, *, busy_timeout: float = BUSY_TIMEOUT_SECONDS
    ) -> None:
        self._memory = str(path) == ":memory:"
        self._path = None if self._memory else Path(path)
        self._busy = busy_timeout
        self._local = threading.local()
        self._state = _State()
        self._lock = threading.RLock()
        self._all: list[sqlite3.Connection] = []
        self._all_lock = threading.Lock()
        self._shared: sqlite3.Connection | None = None
        self._identity: tuple[int, int] | None = None
        if self._path is not None:
            self._identity = self._prepare_file(self._path)
        conn = self._conn()
        self._enable_wal(conn)

    # -- setup --------------------------------------------------------------

    @staticmethod
    def _verify_parents(path: Path) -> None:
        """Refuse a store path whose directories another user could swap.

        sqlite3 can only open a pathname (not a descriptor), so the
        check-to-use gap is closed by making the directory chain a trust
        boundary: no component is a symlink, every one is owned by this user
        or root, none is writable by group or other (a sticky directory is
        tolerated above the immediate parent), and the immediate parent is
        owned by this user. Nobody else can then rename or replace the
        database between the check and SQLite's open.
        """
        parent = Path(os.path.abspath(path)).parent
        me = os.geteuid()
        chain = [parent, *parent.parents]
        for directory in reversed(chain):
            info = os.lstat(directory)
            if stat.S_ISLNK(info.st_mode):
                raise PermissionError("a store path component is a symlink")
            if not stat.S_ISDIR(info.st_mode):
                raise PermissionError("a store path component is not a directory")
            last = directory == parent
            if info.st_uid != me and (last or info.st_uid != 0):
                raise PermissionError("a store directory belongs to another user")
            loose = info.st_mode & 0o022
            if loose and (last or not info.st_mode & stat.S_ISVTX):
                raise PermissionError("a store directory is writable by others")

    @classmethod
    def _prepare_file(cls, path: Path) -> tuple[int, int]:
        """Validate the path and file; return the file's (device, inode)."""
        parent = path.parent
        if not parent.exists():
            parent.mkdir(mode=0o700, parents=True)
            parent.chmod(0o700)
        cls._verify_parents(path)
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags, 0o600)
        except PermissionError:
            # an owner-only file without a write bit (0400, 0200, 0000): make
            # it usable (0600) first; the parent is trusted, so lstat-then-chmod
            # cannot be raced by another user
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
                raise
            os.chmod(path, 0o600)
            fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise PermissionError("the store path is not a regular file")
            if info.st_uid != os.geteuid():
                raise PermissionError("the store file belongs to another user")
            if stat.S_IMODE(info.st_mode) != 0o600:
                os.fchmod(fd, 0o600)
            return info.st_dev, info.st_ino
        finally:
            os.close(fd)

    def _same_file(self) -> None:
        """Refuse a database whose pathname no longer names the checked file."""
        if self._path is None or self._identity is None:
            return
        self._verify_parents(self._path)
        info = os.lstat(self._path)
        if (info.st_dev, info.st_ino) != self._identity or not stat.S_ISREG(
            info.st_mode
        ):
            raise PermissionError("the store file changed after it was checked")

    def _enable_wal(self, conn: sqlite3.Connection) -> None:
        if self._memory:
            return
        deadline = time.monotonic() + self._busy
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def _connect(self) -> sqlite3.Connection:
        target = ":memory:" if self._path is None else str(self._path)
        if self._path is not None:
            self._same_file()
        conn = sqlite3.connect(
            target,
            timeout=self._busy,
            isolation_level=None,
            check_same_thread=not self._memory,
        )
        conn.execute(f"PRAGMA busy_timeout={int(self._busy * 1000)}")
        if self._path is not None:  # SQLite opens lazily; confirm what it opened
            try:
                self._same_file()
            except BaseException:
                conn.close()
                raise
        conn.execute("PRAGMA foreign_keys=ON")
        with self._all_lock:
            self._all.append(conn)
        return conn

    def _conn(self) -> sqlite3.Connection:
        if self._memory:
            if self._shared is None:
                self._shared = self._connect()
            return self._shared
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _guard(self) -> AbstractContextManager[object]:
        return self._lock if self._memory else nullcontext()

    # -- Db -----------------------------------------------------------------

    def execute(self, sql: str, params: Sequence[object] = ()) -> int:
        with self._guard():
            count = self._conn().execute(sql, tuple(params)).rowcount
        return max(count, 0)

    def query(
        self, sql: str, params: Sequence[object] = ()
    ) -> list[tuple[object, ...]]:
        with self._guard():
            rows = self._conn().execute(sql, tuple(params)).fetchall()
        return [tuple(r) for r in rows]

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[None]:
        with self._guard():
            conn = self._conn()
            state = self._state
            if state.depth == 0:
                conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                state.depth = 1
                try:
                    yield
                except BaseException:
                    state.depth = 0
                    conn.execute("ROLLBACK")
                    raise
                state.depth = 0
                conn.execute("COMMIT")
            else:
                name = f"ra_sp{state.depth}"
                conn.execute(f"SAVEPOINT {name}")
                state.depth += 1
                try:
                    yield
                except BaseException:
                    state.depth -= 1
                    conn.execute(f"ROLLBACK TO {name}")
                    conn.execute(f"RELEASE {name}")
                    raise
                state.depth -= 1
                conn.execute(f"RELEASE {name}")

    def migration_lock(self) -> AbstractContextManager[None]:
        return self.transaction(immediate=True)

    def table_exists(self, name: str) -> bool:
        rows = self.query(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        )
        return bool(rows)

    def pre_migration_backup(self, version: int) -> Path | None:
        path = self._path
        if path is None or not self.table_exists("schema_migrations"):
            return None
        if not self.query("SELECT 1 FROM schema_migrations LIMIT 1"):
            return None
        label = f"{version:04d}"
        final = path.with_name(f"{path.name}.pre-{label}")
        scratch = path.with_name(f"{path.name}.pre-{label}.tmp-{uuid.uuid4().hex}")
        fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            self._conn().execute("VACUUM INTO ?", (str(scratch),))
            scratch.chmod(0o600)
            os.replace(scratch, final)
        finally:
            scratch.unlink(missing_ok=True)
        self._prune_backups(path)
        return final

    @staticmethod
    def _prune_backups(path: Path) -> None:
        copies = [
            p for p in path.parent.glob(f"{path.name}.pre-*") if _BACKUP.search(p.name)
        ]
        copies.sort(key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)
        for old in copies[KEEP_BACKUPS:]:
            old.unlink(missing_ok=True)

    def close(self) -> None:
        with self._all_lock:
            conns, self._all = self._all, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()
        self._shared = None


class SqliteStore(SqlStore):
    """The default store. Does not migrate by itself; ``open_store`` does."""

    def __init__(
        self,
        path: Path | str,
        *,
        migrations: list[Migration] | None = None,
        busy_timeout: float = BUSY_TIMEOUT_SECONDS,
    ) -> None:
        try:
            db = SqliteDb(path, busy_timeout=busy_timeout)
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"cannot open the SQLite store: {exc}") from None
        super().__init__(db, migrations)
        self.sqlite = db

    def close(self) -> None:
        self.sqlite.close()
