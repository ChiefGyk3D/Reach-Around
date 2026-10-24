# SPDX-License-Identifier: Apache-2.0
"""The append-only snapshot store."""

from __future__ import annotations

from collections.abc import Mapping

from reach_around.config.models import StoreConfig
from reach_around.store.base import Db, SqlStore, Store
from reach_around.store.migrate import Migration, MigrationStatus
from reach_around.store.models import (
    Access,
    ClickDay,
    Coverage,
    DeadLetter,
    Kind,
    Observation,
    Refusal,
    Run,
    SeriesKey,
    Source,
    StoreError,
    WriteResult,
    local_day,
)
from reach_around.store.sqlite import SqliteStore

__all__ = [
    "Access",
    "ClickDay",
    "Coverage",
    "DeadLetter",
    "Db",
    "Kind",
    "Migration",
    "MigrationStatus",
    "Observation",
    "Refusal",
    "Run",
    "SeriesKey",
    "Source",
    "SqlStore",
    "SqliteStore",
    "Store",
    "StoreError",
    "WriteResult",
    "local_day",
    "open_store",
]


def open_store(
    config: StoreConfig, env: Mapping[str, str], *, migrate: bool = True
) -> Store:
    """Open the configured store, migrating it unless ``migrate`` is false."""
    if config.kind == "postgres":
        # Seam for Task 3: PostgresStore(env[config.dsn_env]) goes here.
        raise StoreError("the postgres store is not available in this build")
    store = SqliteStore(config.path)
    if migrate:
        store.migrate()
    return store
