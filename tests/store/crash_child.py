# SPDX-License-Identifier: Apache-2.0
"""Fault injection around ``Db.execute`` and the spawned child entry points."""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal

import mp
from reach_around.store import SqliteStore
from reach_around.store.base import Db
from reach_around.store.migrate import BOOTSTRAP, load_migrations


class InjectedFault(Exception):
    pass


class FaultyDb:
    """Wraps a ``Db``; the Nth migration statement raises or kills the process.

    Counts executes except the bootstrap table, so statement 1 is the first
    statement of the first pending file and the last is its version row.
    """

    def __init__(
        self,
        inner: Db,
        fail_at: int,
        mode: Literal["raise", "exit"],
        delay: float = 0.0,
    ) -> None:
        self._inner = inner
        self._delay = delay
        self._fail_at = fail_at
        self._mode = mode
        self.calls = 0
        self.dialect = inner.dialect

    def execute(self, sql: str, params: Sequence[object] = ()) -> int:
        if sql != BOOTSTRAP:
            self.calls += 1
            if self.calls == self._fail_at:
                if self._mode == "exit":
                    mp.kill_now()
                raise InjectedFault(f"statement {self.calls}")
        return self._inner.execute(sql, params)

    def query(
        self, sql: str, params: Sequence[object] = ()
    ) -> list[tuple[object, ...]]:
        rows = self._inner.query(sql, params)
        if self._delay and "schema_migrations" in sql:
            # widen the window between reading the plan and acting on it, so a
            # missing lock shows up as a collision every time
            time.sleep(self._delay)
        return rows

    def transaction(self, *, immediate: bool = False) -> AbstractContextManager[None]:
        return self._inner.transaction(immediate=immediate)

    def migration_lock(self) -> AbstractContextManager[None]:
        return self._inner.migration_lock()

    def table_exists(self, name: str) -> bool:
        return self._inner.table_exists(name)

    def pre_migration_backup(self, version: int) -> Path | None:
        return self._inner.pre_migration_backup(version)


def open_child_store(spec: str, migrations_dir: str) -> SqliteStore:
    return SqliteStore(spec, migrations=load_migrations(Path(migrations_dir)))


def migrate_and_die(index: int, spec: str, migrations_dir: str, die_at: int) -> None:
    store = open_child_store(spec, migrations_dir)
    store.db = FaultyDb(store.db, die_at, "exit")
    store.migrate()


def migrate_when_released(
    index: int, spec: str, migrations_dir: str, release: str
) -> None:
    store = open_child_store(spec, migrations_dir)
    store.db = FaultyDb(store.db, 0, "raise", delay=0.3)
    mp.wait_for_go(Path(release), index)
    store.migrate()
