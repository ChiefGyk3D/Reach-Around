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

from reach_around.store import SqliteStore, Store, StoreError
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


def _ignore_errors(call: Callable[..., object], *args: object) -> None:
    try:
        call(*args)
    except Exception:  # only the files SQLite held open matter to the caller
        return


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

    def test_existing_loose_file_is_tightened_to_0600(self, tmp_path: Path) -> None:
        path = tmp_path / "s.db"
        path.write_bytes(b"")
        path.chmod(0o644)
        SqliteStore(path).close()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_symlinked_store_path_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere.db"
        target.write_bytes(b"")
        target.chmod(0o644)
        link = tmp_path / "s.db"
        link.symlink_to(target)
        with pytest.raises(StoreError):
            SqliteStore(link)
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    @pytest.mark.parametrize("mode", [0o400, 0o200, 0o000, 0o640, 0o666])
    def test_any_existing_mode_ends_as_a_usable_0600_file(
        self, tmp_path: Path, mode: int
    ) -> None:
        path = tmp_path / "s.db"
        path.write_bytes(b"")
        path.chmod(mode)
        store = SqliteStore(path)
        store.migrate()
        store.kv_set("a", "b")
        store.close()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_file_swapped_for_a_symlink_after_the_check_is_not_opened(
        self, tmp_path: Path
    ) -> None:
        victim = tmp_path / "victim.db"
        victim.write_bytes(b"")
        victim.chmod(0o644)
        path = tmp_path / "s.db"
        store = SqliteStore(path)
        path.rename(tmp_path / "saved.db")
        path.symlink_to(victim)
        errors: list[BaseException] = []

        def reopen() -> None:  # a new thread makes a new connection by pathname
            try:
                store.sqlite.query("SELECT 1")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=reopen)
        worker.start()
        worker.join()
        assert len(errors) == 1 and isinstance(errors[0], PermissionError)
        assert victim.read_bytes() == b""

    def test_file_replaced_by_another_file_after_the_check_is_not_opened(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "s.db"
        store = SqliteStore(path)
        path.rename(tmp_path / "saved.db")
        path.write_bytes(b"")
        errors: list[BaseException] = []

        def reopen() -> None:
            try:
                store.sqlite.query("SELECT 1")
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=reopen)
        worker.start()
        worker.join()
        assert len(errors) == 1 and isinstance(errors[0], PermissionError)

    def test_intermediate_symlink_in_the_path_is_refused(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir(mode=0o700)
        (tmp_path / "redirect").symlink_to(real)
        with pytest.raises(StoreError):
            SqliteStore(tmp_path / "redirect" / "s.db")
        assert list(real.iterdir()) == []

    def test_parent_writable_by_others_is_refused(self, tmp_path: Path) -> None:
        parent = tmp_path / "shared"
        parent.mkdir()
        parent.chmod(0o777)
        with pytest.raises(StoreError):
            SqliteStore(parent / "s.db")
        assert list(parent.iterdir()) == []

    def test_group_writable_ancestor_is_refused(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        inner.chmod(0o700)
        outer.chmod(0o770)
        with pytest.raises(StoreError):
            SqliteStore(inner / "s.db")

    def test_parent_owned_by_another_user_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        owner = os.stat(tmp_path).st_uid
        monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
        with pytest.raises(StoreError):
            SqliteStore(tmp_path / "s.db")

    def test_store_file_owned_by_another_user_is_refused(self) -> None:
        import os
        from types import SimpleNamespace
        from typing import cast

        from reach_around.store.sqlite import SqliteDb

        def fake(mode: int, uid: int) -> os.stat_result:
            return cast(os.stat_result, SimpleNamespace(st_mode=mode, st_uid=uid))

        me = os.geteuid()
        SqliteDb._check_file(fake(stat.S_IFREG | 0o600, me))
        for bad in (fake(stat.S_IFREG | 0o600, me + 1), fake(stat.S_IFDIR | 0o700, me)):
            with pytest.raises(PermissionError):
                SqliteDb._check_file(bad)

    def test_pathname_swapped_and_restored_around_the_open_is_not_followed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        from typing import Any

        path = tmp_path / "s.db"
        store = SqliteStore(path)
        store.migrate()
        real_connect = sqlite3.connect
        seen: dict[str, object] = {}

        def open_inodes() -> set[int]:
            found: set[int] = set()
            for name in os.listdir("/proc/self/fd"):
                try:
                    found.add(os.stat(f"/proc/self/fd/{name}").st_ino)
                except OSError:
                    pass
            return found

        def swapping_connect(target: str, **kwargs: Any) -> sqlite3.Connection:
            # same-user ABA: another file sits at the pathname while SQLite
            # opens it, and the original is back before any later check
            original = tmp_path / "orig.db"
            path.rename(original)
            path.write_bytes(b"")
            seen["substitute"] = path.stat().st_ino
            conn: sqlite3.Connection = real_connect(target, **kwargs)
            try:
                conn.execute("PRAGMA schema_version").fetchall()  # force the open
            finally:
                seen["open"] = open_inodes()
                path.unlink()
                original.rename(path)
            return conn

        monkeypatch.setattr(sqlite3, "connect", swapping_connect)
        worker = threading.Thread(
            target=lambda: _ignore_errors(store.sqlite.query, "SELECT 1")
        )
        worker.start()
        worker.join()
        monkeypatch.setattr(sqlite3, "connect", real_connect)
        # SQLite must never have held the substitute file open
        assert "substitute" in seen
        open_now = seen["open"]
        assert isinstance(open_now, set)
        assert seen["substitute"] not in open_now

    def test_non_regular_store_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(StoreError):
            SqliteStore(tmp_path)

    def test_click_daily_rebuild_keeps_rows_and_enforces_integer_counts(
        self, tmp_path: Path
    ) -> None:
        every = load_migrations()
        first = SqliteStore(tmp_path / "s.db", migrations=every[:1])
        first.migrate()
        first.db.execute(
            "INSERT INTO click_daily VALUES ('2026-10-22', 'UTC', 'docs', 'US', 7)"
        )
        full = SqliteStore(tmp_path / "s.db")
        assert full.migrate() == 2
        assert full.db.query("SELECT day, count FROM click_daily") == [
            ("2026-10-22", 7)
        ]
        for bad in (1.5, 9007199254740992, -1, "x"):
            with pytest.raises(sqlite3.IntegrityError):
                full.db.execute(
                    "INSERT INTO click_daily VALUES "
                    "('2026-10-23', 'UTC', 'docs', 'US', ?)",
                    (bad,),
                )
        first.close()
        full.close()

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
