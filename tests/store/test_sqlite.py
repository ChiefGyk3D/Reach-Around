# SPDX-License-Identifier: Apache-2.0
"""The SQLite backend against the shared contracts, plus SQLite-only checks."""

from __future__ import annotations

import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from contract import StoreContract
from migration_contract import MigrationContract

from reach_around.store import SqliteStore, Store
from reach_around.store.migrate import load_migrations


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    sqlite = SqliteStore(tmp_path / "data" / "s.db")
    sqlite.migrate()
    yield sqlite
    sqlite.close()


@pytest.fixture
def make_store(tmp_path: Path) -> Iterator[Callable[..., Store]]:
    opened: list[SqliteStore] = []
    counter = iter(range(1000))

    def make(migrations_dir: Path, like: Store | None = None) -> Store:
        if like is None:
            path = tmp_path / "db" / f"m{next(counter)}.db"
        else:
            assert isinstance(like, SqliteStore)
            path = Path(like.sqlite._path or "")
        created = SqliteStore(path, migrations=load_migrations(migrations_dir))
        opened.append(created)
        return created

    yield make
    for created in opened:
        created.close()


@pytest.fixture
def conn_spec() -> Callable[[Store], str]:
    def spec(store: Store) -> str:
        assert isinstance(store, SqliteStore)
        return str(store.sqlite._path)

    return spec


class TestSqliteStore(StoreContract):
    pass


class TestSqliteMigrations(MigrationContract):
    pass


class TestSqliteOnly:
    def test_memory_store_works_across_threads(self) -> None:
        mem = SqliteStore(":memory:")
        mem.migrate()
        errors: list[BaseException] = []

        def work(n: int) -> None:
            try:
                mem.kv_set(f"k{n}", "v")
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert mem.kv_get("k3") == "v"

    def test_file_mode_is_0600(self, tmp_path: Path) -> None:
        sqlite = SqliteStore(tmp_path / "private" / "s.db")
        sqlite.migrate()
        sqlite.kv_set("a", "b")  # keeps the -wal and -shm files alive
        base = tmp_path / "private" / "s.db"
        names = [base, Path(f"{base}-wal"), Path(f"{base}-shm")]
        for path in names:
            assert path.exists(), path.name
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
        assert stat.S_IMODE(base.parent.stat().st_mode) == 0o700
        sqlite.close()

    def test_wal_and_foreign_keys_are_on(self, tmp_path: Path) -> None:
        sqlite = SqliteStore(tmp_path / "s.db")
        assert sqlite.db.query("PRAGMA journal_mode") == [("wal",)]
        assert sqlite.db.query("PRAGMA foreign_keys") == [(1,)]
        sqlite.close()

    def test_pre_migration_backup_written_0600_and_only_three_kept(
        self, tmp_path: Path
    ) -> None:
        mdir = tmp_path / "m"
        mdir.mkdir()
        (mdir / "0001_a.sql").write_text("CREATE TABLE t1 (x INTEGER);\n")
        path = tmp_path / "data" / "s.db"
        first = SqliteStore(path, migrations=load_migrations(mdir))
        first.migrate()
        assert list(path.parent.glob("s.db.pre-*")) == []  # fresh: nothing to save
        for n in range(2, 7):
            (mdir / f"{n:04d}_x.sql").write_text(f"CREATE TABLE t{n} (x INTEGER);\n")
            SqliteStore(path, migrations=load_migrations(mdir)).migrate()
        copies = sorted(p.name for p in path.parent.glob("s.db.pre-*"))
        assert copies == ["s.db.pre-0004", "s.db.pre-0005", "s.db.pre-0006"]
        for name in copies:
            mode = stat.S_IMODE((path.parent / name).stat().st_mode)
            assert mode == 0o600
        newest = sqlite3.connect(path.parent / "s.db.pre-0006")
        try:
            # the copy holds the state before 0006 was applied
            tables = {r[0] for r in newest.execute("SELECT name FROM sqlite_master")}
        finally:
            newest.close()
        assert "t5" in tables and "t6" not in tables

    def test_migration_files_applied_in_numeric_order_with_gaps(
        self, tmp_path: Path
    ) -> None:
        mdir = tmp_path / "m"
        mdir.mkdir()
        (mdir / "0007_x.sql").write_text("CREATE TABLE t7 (x INTEGER);\n")
        (mdir / "0001_a.sql").write_text("CREATE TABLE t1 (x INTEGER);\n")
        sqlite = SqliteStore(tmp_path / "s.db", migrations=load_migrations(mdir))
        assert sqlite.migrate() == 7
        rows = sqlite.db.query("SELECT version FROM schema_migrations ORDER BY rowid")
        assert rows == [(1,), (7,)]

    def test_busy_database_waits_rather_than_failing_immediately(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "s.db"
        sqlite = SqliteStore(path, busy_timeout=5.0)
        sqlite.migrate()
        blocker = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        blocker.execute("BEGIN IMMEDIATE")

        def release() -> None:
            time.sleep(0.5)
            blocker.execute("COMMIT")

        thread = threading.Thread(target=release)
        thread.start()
        started = time.monotonic()
        sqlite.kv_set("k", "v")
        waited = time.monotonic() - started
        thread.join()
        blocker.close()
        assert waited >= 0.3
        assert sqlite.kv_get("k") == "v"
        sqlite.close()
