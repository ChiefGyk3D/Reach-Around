# SPDX-License-Identifier: Apache-2.0
"""Backend-independent migration tests.

A backend's test module supplies two fixtures:

* ``make_store(migrations_dir, like=None)``: a store over a fresh empty
  database, or over the same database as ``like``.
* ``conn_spec(store)``: a picklable string a spawned child uses to reach the
  same database (a file path for SQLite).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import crash_child
import pytest

import mp
from reach_around.store import Store, StoreError
from reach_around.store.migrate import (
    default_migrations_dir,
    load_migrations,
    split_statements,
)

MakeStore = Callable[..., Store]

THREE = (
    "CREATE TABLE t1 (x INTEGER);\n"
    "CREATE TABLE t2 (x INTEGER);\n"
    "INSERT INTO t1 (x) VALUES (1);\n"
)
PINS = Path(__file__).parent / "pinned_migrations.json"


def put(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def versions(store: Store) -> list[int]:
    rows = store.db.query("SELECT version FROM schema_migrations ORDER BY version")
    return [int(str(r[0])) for r in rows]


def check_pins(pins: dict[str, str], directory: Path) -> list[str]:
    """Names of pinned files whose checksum no longer matches."""
    by_name = {m.filename: m.checksum for m in load_migrations(directory)}
    return sorted(n for n, digest in pins.items() if by_name.get(n) != digest)


class MigrationContract:
    @pytest.fixture
    def mdir(self, tmp_path: Path) -> Path:
        directory = tmp_path / "migrations"
        directory.mkdir()
        return directory

    def test_migrate_idempotent_and_returns_highest_version(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        put(mdir, "0003_b.sql", "CREATE TABLE t3 (x INTEGER);\n")
        store = make_store(mdir)
        assert store.migrate() == 3
        assert store.migrate() == 3
        assert versions(store) == [1, 3]

    def test_checksum_recorded_for_each_applied_file(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        store = make_store(mdir)
        store.migrate()
        (row,) = store.db.query("SELECT name, checksum FROM schema_migrations")
        assert row == ("a", hashlib.sha256(THREE.encode()).hexdigest())

    def test_checksum_ignores_crlf(self, make_store: MakeStore, mdir: Path) -> None:
        (mdir / "0001_a.sql").write_bytes(THREE.replace("\n", "\r\n").encode())
        store = make_store(mdir)
        store.migrate()
        (row,) = store.db.query("SELECT checksum FROM schema_migrations")
        assert row == (hashlib.sha256(THREE.encode()).hexdigest(),)

    def test_edited_applied_migration_is_refused(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        real = default_migrations_dir().joinpath("0001_core.sql").read_text("utf-8")
        put(mdir, "0001_core.sql", real)
        store = make_store(mdir)
        store.migrate()
        put(mdir, "0001_core.sql", real + " ")
        again = make_store(mdir, like=store)
        with pytest.raises(StoreError) as caught:
            again.migrate()
        assert "0001" in str(caught.value)
        assert "CREATE TABLE" not in str(caught.value)

    def test_missing_applied_migration_is_refused(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        put(mdir, "0002_b.sql", "CREATE TABLE t3 (x INTEGER);\n")
        store = make_store(mdir)
        store.migrate()
        (mdir / "0001_a.sql").unlink()
        again = make_store(mdir, like=store)
        with pytest.raises(StoreError, match="0001"):
            again.migrate()

    def test_newer_database_refused_with_downgrade_message(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        store = make_store(mdir)
        store.migrate()
        store.db.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_at) "
            "VALUES (9999, 'future', 'x', 'x')"
        )
        with pytest.raises(StoreError) as caught:
            store.migrate()
        assert "newer than this build" in str(caught.value)
        assert "restore a backup" in str(caught.value)

    def test_unapplied_lower_version_is_refused(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        put(mdir, "0004_d.sql", "CREATE TABLE t4 (x INTEGER);\n")
        store = make_store(mdir)
        store.migrate()
        put(mdir, "0003_c.sql", "CREATE TABLE t3 (x INTEGER);\n")
        again = make_store(mdir, like=store)
        with pytest.raises(StoreError, match="0003 is older than applied 0004"):
            again.migrate()
        assert not again.db.table_exists("t3")

    def test_duplicate_version_numbers_refused(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0002_a.sql", THREE)
        put(mdir, "0002_b.sql", "CREATE TABLE t3 (x INTEGER);\n")
        with pytest.raises(StoreError, match="duplicate migration number 0002"):
            make_store(mdir)

    @pytest.mark.parametrize("fail_at", [1, 2, 3, 4])
    def test_crash_after_every_statement_leaves_no_partial_state(
        self, make_store: MakeStore, mdir: Path, fail_at: int
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        store = make_store(mdir)
        real = store.db
        store.db = crash_child.FaultyDb(real, fail_at, "raise")
        with pytest.raises(crash_child.InjectedFault):
            store.migrate()
        store.db = real
        assert not real.table_exists("t1")
        assert not real.table_exists("t2")
        assert versions(store) == []
        assert store.migrate() == 1
        assert versions(store) == [1]

    def test_earlier_files_survive_a_later_file_failing(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        put(mdir, "0002_b.sql", "CREATE TABLE t3 (x INTEGER);\nSELECT * FROM nope;\n")
        store = make_store(mdir)
        with pytest.raises(Exception, match="nope"):
            store.migrate()
        assert versions(store) == [1]
        assert not store.db.table_exists("t3")

    @pytest.mark.parametrize("die_at", [1, 2, 3, 4])
    def test_killed_process_recovers(
        self,
        make_store: MakeStore,
        conn_spec: Callable[[Store], str],
        mdir: Path,
        die_at: int,
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        store = make_store(mdir)
        codes = mp.run_children(
            crash_child.migrate_and_die, 1, conn_spec(store), str(mdir), die_at
        )
        assert codes == [mp.KILLED]
        assert store.migrate() == 1
        assert versions(store) == [1]
        assert store.db.table_exists("t1")

    def test_two_processes_migrating_at_once_apply_once(
        self,
        make_store: MakeStore,
        conn_spec: Callable[[Store], str],
        mdir: Path,
        tmp_path: Path,
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        put(mdir, "0002_b.sql", "CREATE TABLE t3 (x INTEGER);\n")
        store = make_store(mdir)
        release = tmp_path / "release"
        release.mkdir()
        codes = mp.run_children(
            crash_child.migrate_when_released,
            4,
            conn_spec(store),
            str(mdir),
            str(release),
            release_dir=release,
        )
        assert codes == [0, 0, 0, 0]
        assert versions(store) == [1, 2]

    def test_lock_is_released_after_a_failed_migration(
        self, make_store: MakeStore, mdir: Path
    ) -> None:
        put(mdir, "0001_a.sql", THREE)
        store = make_store(mdir)
        real = store.db
        store.db = crash_child.FaultyDb(real, 2, "raise")
        with pytest.raises(crash_child.InjectedFault):
            store.migrate()
        other = make_store(mdir, like=store)
        assert other.migrate() == 1

    def test_migration_files_contain_only_splittable_statements(self) -> None:
        migrations = load_migrations()
        assert migrations, "no shipped migrations were collected"
        for migration in migrations:
            statements = split_statements(migration.sql)
            assert statements, migration.filename
            for statement in statements:
                assert sqlite3.complete_statement(statement), migration.filename
                assert ";" not in statement[:-1], migration.filename

    def test_splitter_guard_bites_on_trigger_bodies_and_semicolons_in_strings(
        self,
    ) -> None:
        trigger = (
            "CREATE TRIGGER t AFTER INSERT ON a BEGIN\n  UPDATE b SET x = 1;\nEND;\n"
        )
        with pytest.raises(StoreError):
            split_statements(trigger)
        with pytest.raises(StoreError):
            split_statements("INSERT INTO a (s) VALUES ('x;y');\n")
        with pytest.raises(StoreError):
            split_statements("CREATE TABLE a (x INTEGER)\n")

    def test_shipped_migration_checksums_match_the_pins(self, tmp_path: Path) -> None:
        pins = json.loads(PINS.read_text("utf-8"))["pins"]
        shipped = Path(str(default_migrations_dir()))
        assert check_pins(pins, shipped) == []

    def test_pin_guard_bites_on_an_edited_copy(self, tmp_path: Path) -> None:
        shipped = Path(str(default_migrations_dir()))
        copy = tmp_path / "copy"
        copy.mkdir()
        for entry in shipped.glob("*.sql"):
            (copy / entry.name).write_bytes(entry.read_bytes())
        pins = {m.filename: m.checksum for m in load_migrations(copy)}
        assert pins and check_pins(pins, copy) == []
        victim = sorted(copy.glob("*.sql"))[0]
        victim.write_text(victim.read_text("utf-8") + " ", encoding="utf-8")
        assert check_pins(pins, copy) == [victim.name]
