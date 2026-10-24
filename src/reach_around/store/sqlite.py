# SPDX-License-Identifier: Apache-2.0
"""The SQLite adapter: WAL, a busy timeout, foreign keys, one connection per thread."""

from __future__ import annotations

import os
import re
import sqlite3
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
        if self._path is not None:
            self._prepare_file(self._path)
        conn = self._conn()
        self._enable_wal(conn)

    # -- setup --------------------------------------------------------------

    @staticmethod
    def _prepare_file(path: Path) -> None:
        parent = path.parent
        if not parent.exists():
            parent.mkdir(mode=0o700, parents=True)
            parent.chmod(0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)

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
        conn = sqlite3.connect(
            target,
            timeout=self._busy,
            isolation_level=None,
            check_same_thread=not self._memory,
        )
        conn.execute(f"PRAGMA busy_timeout={int(self._busy * 1000)}")
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
