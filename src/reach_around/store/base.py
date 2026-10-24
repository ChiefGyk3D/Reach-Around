# SPDX-License-Identifier: Apache-2.0
"""The database seam (``Db``), the ``Store`` interface and ``SqlStore``.

Portable SQL only: ``?`` placeholders, ISO text for dates and fixed-width UTC
text for timestamps. An adapter (SQLite today, Postgres later) implements
``Db``; everything else lives in ``SqlStore``.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from reach_around.store import migrate as _migrate
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
    check_utc,
    check_zone,
    fmt_ts,
    local_day,
    parse_ts,
)


class Db(Protocol):
    dialect: Literal["sqlite", "postgres"]

    def execute(self, sql: str, params: Sequence[object] = ()) -> int:
        """Run one statement; return the number of rows affected (CAS result)."""
        ...

    def query(
        self, sql: str, params: Sequence[object] = ()
    ) -> list[tuple[object, ...]]: ...

    def transaction(self, *, immediate: bool = False) -> AbstractContextManager[None]:
        """A transaction; nested inside another it joins as a savepoint."""
        ...

    def migration_lock(self) -> AbstractContextManager[None]: ...

    def table_exists(self, name: str) -> bool: ...

    def pre_migration_backup(self, version: int) -> Path | None:
        """Copy the database before migrating when the engine can; else None."""
        ...


class Store(Protocol):
    db: Db

    def migrate(self) -> int: ...
    def migration_status(self) -> MigrationStatus: ...
    def write(
        self,
        observations: Sequence[Observation],
        *,
        now: datetime,
        settle_days: int = 3,
    ) -> WriteResult: ...
    def purge_expired(self, today: date) -> int: ...
    def series(
        self, key: SeriesKey, *, since: date | None = None
    ) -> list[Observation]: ...
    def latest(self, key: SeriesKey) -> Observation | None: ...
    def keys(self) -> list[SeriesKey]: ...
    def revisions(self, key: SeriesKey, day: date) -> list[Observation]: ...
    def record_run(self, run: Run) -> None: ...
    def last_run(self, platform: str, account: str) -> Run | None: ...
    def last_ok(self, platform: str, account: str) -> datetime | None: ...
    def stale_since(self, platform: str, account: str) -> date | None: ...
    def kv_get(self, key: str) -> str | None: ...
    def kv_set(self, key: str, value: str) -> None: ...
    def kv_cas(self, key: str, expected: str | None, new: str) -> bool: ...
    def click_cursor(self) -> int: ...
    def apply_click_batch(
        self, rows: Sequence[ClickDay], *, lo: int, hi: int, source_total: int
    ) -> bool: ...
    def record_dead_letter(
        self, lo: int, hi: int, reason: str, now: datetime
    ) -> None: ...
    def dead_letters(self, *, open_only: bool = True) -> list[DeadLetter]: ...
    def resolve_dead_letter(
        self, lo: int, hi: int, now: datetime, how: Literal["applied", "skipped"]
    ) -> None: ...
    def clicks(self, since: date) -> list[ClickDay]: ...


# Literal SQL only: nothing here is built from caller data.
_WINNERS_HEAD = (
    "SELECT platform, account, metric, kind, day, day_tz, observed_at, value, "
    "source, access, expires_at, coverage, coverage_note FROM ("
    "SELECT *, ROW_NUMBER() OVER (PARTITION BY day ORDER BY "
    "CASE source WHEN 'typed' THEN 0 WHEN 'imported' THEN 1 ELSE 2 END, "
    "observed_at DESC) AS rn FROM observations "
    "WHERE platform = ? AND account = ? AND metric = ?"
)
_WINNERS_SINCE = _WINNERS_HEAD + " AND day >= ?) AS winners WHERE rn = 1"
_WINNERS_ALL = _WINNERS_HEAD + ") AS winners WHERE rn = 1"
_REVISIONS = (
    "SELECT platform, account, metric, kind, day, day_tz, observed_at, value, "
    "source, access, expires_at, coverage, coverage_note FROM observations "
    "WHERE platform = ? AND account = ? AND metric = ? AND day = ? "
    "ORDER BY observed_at, source"
)
_DEAD_LETTERS_OPEN = (
    "SELECT lo_id, hi_id, reason, first_seen, last_seen, attempts, resolved_at, "
    "resolution FROM click_dead_letters WHERE resolved_at IS NULL "
    "ORDER BY lo_id, hi_id"
)
_DEAD_LETTERS_ALL = (
    "SELECT lo_id, hi_id, reason, first_seen, last_seen, attempts, resolved_at, "
    "resolution FROM click_dead_letters ORDER BY lo_id, hi_id"
)
_REASON_MAX = 500


def _text(value: object) -> str:
    return str(value)


def _observation(row: tuple[object, ...]) -> Observation:
    return Observation(
        platform=_text(row[0]),
        account=_text(row[1]),
        metric=_text(row[2]),
        kind=Kind(_text(row[3])),
        day=date.fromisoformat(_text(row[4])),
        day_tz=_text(row[5]),
        observed_at=parse_ts(_text(row[6])),
        value=float(cast(float, row[7])),
        source=Source(_text(row[8])),
        access=Access(_text(row[9])),
        expires_at=None if row[10] is None else date.fromisoformat(_text(row[10])),
        coverage=Coverage(_text(row[11])),
        coverage_note=None if row[12] is None else _text(row[12]),
    )


def _refusal_reason(
    obs: Observation, now: datetime, settle_days: int
) -> Literal["day_closed", "future_day", "day_incomplete", "day_sealed"] | None:
    today = local_day(now, obs.day_tz)
    fetched = obs.source is Source.FETCHED
    if obs.kind is Kind.SNAPSHOT:
        if obs.day > today:
            return "future_day"
        if fetched and obs.day < today:
            return "day_closed"
        return None
    # aggregate: only a day that has completed in its own zone
    if obs.day > today:
        return "future_day"
    if obs.day == today:
        return "day_incomplete"
    if fetched and (today - obs.day).days > settle_days:
        return "day_sealed"
    return None


class SqlStore:
    """Every ``Store`` method over a ``Db``."""

    def __init__(self, db: Db, migrations: list[Migration] | None = None) -> None:
        self.db = db
        self._migrations = (
            _migrate.load_migrations() if migrations is None else migrations
        )

    # -- migrations ---------------------------------------------------------

    def migrate(self) -> int:
        return _migrate.run_migrations(self.db, self._migrations)

    def migration_status(self) -> MigrationStatus:
        return _migrate.status(self.db, self._migrations)

    # -- observations -------------------------------------------------------

    def write(
        self,
        observations: Sequence[Observation],
        *,
        now: datetime,
        settle_days: int = 3,
    ) -> WriteResult:
        check_utc(now)
        if settle_days < 0:
            raise ValueError("settle_days must not be negative")
        inserted = unchanged = 0
        refused: list[Refusal] = []
        with self.db.transaction(immediate=True):
            for obs in observations:
                reason = _refusal_reason(obs, now, settle_days)
                if reason is not None:
                    refused.append(Refusal(obs.key, obs.day, reason))
                    continue
                self._check_kind(obs)
                current = self.db.query(
                    "SELECT value, coverage FROM observations WHERE platform = ? "
                    "AND account = ? AND metric = ? AND kind = ? AND day = ? "
                    "AND source = ? ORDER BY observed_at DESC LIMIT 1",
                    (
                        obs.platform,
                        obs.account,
                        obs.metric,
                        obs.kind.value,
                        obs.day.isoformat(),
                        obs.source.value,
                    ),
                )
                if (
                    current
                    and float(cast(float, current[0][0])) == float(obs.value)
                    and _text(current[0][1]) == obs.coverage.value
                ):
                    unchanged += 1
                    continue
                added = self.db.execute(
                    "INSERT INTO observations (platform, account, metric, kind, "
                    "day, day_tz, observed_at, value, source, access, expires_at, "
                    "coverage, coverage_note, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT DO NOTHING",
                    (
                        obs.platform,
                        obs.account,
                        obs.metric,
                        obs.kind.value,
                        obs.day.isoformat(),
                        obs.day_tz,
                        fmt_ts(obs.observed_at),
                        float(obs.value),
                        obs.source.value,
                        obs.access.value,
                        None if obs.expires_at is None else obs.expires_at.isoformat(),
                        obs.coverage.value,
                        obs.coverage_note,
                        fmt_ts(now),
                    ),
                )
                inserted += added
                unchanged += 1 - added
        return WriteResult(inserted, unchanged, tuple(refused))

    def _check_kind(self, obs: Observation) -> None:
        clash = self.db.query(
            "SELECT 1 FROM observations WHERE platform = ? AND account = ? "
            "AND metric = ? AND kind <> ? LIMIT 1",
            (obs.platform, obs.account, obs.metric, obs.kind.value),
        )
        if clash:
            raise StoreError(
                f"metric {obs.platform}/{obs.account}/{obs.metric} cannot change kind"
            )

    def purge_expired(self, today: date) -> int:
        return self.db.execute(
            "DELETE FROM observations WHERE access = 'owner_authorized' "
            "AND expires_at < ?",
            (today.isoformat(),),
        )

    def series(self, key: SeriesKey, *, since: date | None = None) -> list[Observation]:
        params: list[object] = [key.platform, key.account, key.metric]
        if since is None:
            sql = _WINNERS_ALL + " ORDER BY day"
        else:
            sql = _WINNERS_SINCE + " ORDER BY day"
            params.append(since.isoformat())
        return [_observation(r) for r in self.db.query(sql, params)]

    def latest(self, key: SeriesKey) -> Observation | None:
        rows = self.db.query(
            _WINNERS_ALL + " ORDER BY day DESC LIMIT 1",
            (key.platform, key.account, key.metric),
        )
        return _observation(rows[0]) if rows else None

    def keys(self) -> list[SeriesKey]:
        rows = self.db.query(
            "SELECT DISTINCT platform, account, metric FROM observations "
            "ORDER BY platform, account, metric"
        )
        return [SeriesKey(_text(r[0]), _text(r[1]), _text(r[2])) for r in rows]

    def revisions(self, key: SeriesKey, day: date) -> list[Observation]:
        rows = self.db.query(
            _REVISIONS, (key.platform, key.account, key.metric, day.isoformat())
        )
        return [_observation(r) for r in rows]

    # -- runs ---------------------------------------------------------------

    def record_run(self, run: Run) -> None:
        self.db.execute(
            "INSERT INTO runs (platform, account, ts, ok, reason, partial) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            (
                run.platform,
                run.account,
                fmt_ts(run.ts),
                int(run.ok),
                run.reason,
                int(run.partial),
            ),
        )

    def last_run(self, platform: str, account: str) -> Run | None:
        rows = self.db.query(
            "SELECT ts, ok, reason, partial FROM runs WHERE platform = ? "
            "AND account = ? ORDER BY ts DESC LIMIT 1",
            (platform, account),
        )
        if not rows:
            return None
        r = rows[0]
        return Run(
            platform,
            account,
            parse_ts(_text(r[0])),
            bool(r[1]),
            None if r[2] is None else _text(r[2]),
            bool(r[3]),
        )

    def last_ok(self, platform: str, account: str) -> datetime | None:
        rows = self.db.query(
            "SELECT MAX(ts) FROM runs WHERE platform = ? AND account = ? AND ok = 1",
            (platform, account),
        )
        return parse_ts(_text(rows[0][0])) if rows and rows[0][0] is not None else None

    def stale_since(self, platform: str, account: str) -> date | None:
        last = self.last_run(platform, account)
        if last is None or last.ok:
            return None
        good = self.last_ok(platform, account)
        if good is not None:
            return good.date()
        rows = self.db.query(
            "SELECT MIN(ts) FROM runs WHERE platform = ? AND account = ?",
            (platform, account),
        )
        return parse_ts(_text(rows[0][0])).date()

    # -- kv -----------------------------------------------------------------

    def kv_get(self, key: str) -> str | None:
        rows = self.db.query("SELECT value FROM kv WHERE key = ?", (key,))
        return _text(rows[0][0]) if rows else None

    def kv_set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def kv_cas(self, key: str, expected: str | None, new: str) -> bool:
        if expected is None:
            return (
                self.db.execute(
                    "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    (key, new),
                )
                == 1
            )
        return (
            self.db.execute(
                "UPDATE kv SET value = ? WHERE key = ? AND value = ?",
                (new, key, expected),
            )
            == 1
        )

    # -- clicks -------------------------------------------------------------

    def click_cursor(self) -> int:
        rows = self.db.query("SELECT last_id FROM click_cursor WHERE id = 1")
        return int(cast(int, rows[0][0])) if rows else 0

    def apply_click_batch(
        self, rows: Sequence[ClickDay], *, lo: int, hi: int, source_total: int
    ) -> bool:
        if lo < 0 or hi <= lo:
            raise ValueError("window must satisfy 0 <= lo < hi")
        total = 0
        for row in rows:
            if (
                isinstance(row.count, bool)
                or not isinstance(row.count, int)
                or row.count < 0
            ):
                raise ValueError("click count must be a non-negative integer")
            if not row.link_id or not row.link_id.isprintable():
                raise ValueError("invalid link_id")
            if row.country and not (
                len(row.country) == 2
                and row.country.isascii()
                and row.country.isalpha()
            ):
                raise ValueError("country must be empty or two letters")
            check_zone(row.day_tz)
            total += row.count
        if total != source_total:
            raise ValueError("row counts do not sum to source_total")
        with self.db.transaction(immediate=True):
            moved = self.db.execute(
                "UPDATE click_cursor SET last_id = ? WHERE id = 1 AND last_id = ?",
                (hi, lo),
            )
            if moved != 1:
                return False
            for row in rows:
                self.db.execute(
                    "INSERT INTO click_daily (day, day_tz, link_id, country, count) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT (day, day_tz, link_id, "
                    "country) DO UPDATE SET count = click_daily.count + excluded.count",
                    (
                        row.day.isoformat(),
                        row.day_tz,
                        row.link_id,
                        row.country,
                        row.count,
                    ),
                )
            stamp = fmt_ts(datetime.now(tz=UTC))
            self.db.execute(
                "INSERT INTO click_batches (lo_id, hi_id, applied_at, bucket_count, "
                "total_count) VALUES (?, ?, ?, ?, ?)",
                (lo, hi, stamp, len(rows), total),
            )
        return True

    def record_dead_letter(self, lo: int, hi: int, reason: str, now: datetime) -> None:
        if len(reason) > _REASON_MAX or not reason.isprintable():
            raise ValueError("dead-letter reason is too long or not printable")
        stamp = fmt_ts(now)
        self.db.execute(
            "INSERT INTO click_dead_letters (lo_id, hi_id, reason, first_seen, "
            "last_seen, attempts) VALUES (?, ?, ?, ?, ?, 1) "
            "ON CONFLICT (lo_id, hi_id) DO UPDATE SET attempts = "
            "click_dead_letters.attempts + 1, last_seen = excluded.last_seen, "
            "reason = excluded.reason WHERE click_dead_letters.resolved_at IS NULL",
            (lo, hi, reason, stamp, stamp),
        )

    def dead_letters(self, *, open_only: bool = True) -> list[DeadLetter]:
        rows = self.db.query(_DEAD_LETTERS_OPEN if open_only else _DEAD_LETTERS_ALL)
        out: list[DeadLetter] = []
        for r in rows:
            out.append(
                DeadLetter(
                    int(cast(int, r[0])),
                    int(cast(int, r[1])),
                    _text(r[2]),
                    parse_ts(_text(r[3])),
                    parse_ts(_text(r[4])),
                    int(cast(int, r[5])),
                    None if r[6] is None else parse_ts(_text(r[6])),
                    None
                    if r[7] is None
                    else cast(Literal["applied", "skipped"], _text(r[7])),
                )
            )
        return out

    def resolve_dead_letter(
        self, lo: int, hi: int, now: datetime, how: Literal["applied", "skipped"]
    ) -> None:
        if how not in ("applied", "skipped"):
            raise ValueError("how must be 'applied' or 'skipped'")
        self.db.execute(
            "UPDATE click_dead_letters SET resolved_at = ?, resolution = ? "
            "WHERE lo_id = ? AND hi_id = ? AND resolved_at IS NULL",
            (fmt_ts(now), how, lo, hi),
        )

    def clicks(self, since: date) -> list[ClickDay]:
        rows = self.db.query(
            "SELECT day, day_tz, link_id, country, count FROM click_daily "
            "WHERE day >= ? ORDER BY day, day_tz, link_id, country",
            (since.isoformat(),),
        )
        return [
            ClickDay(
                date.fromisoformat(_text(r[0])),
                _text(r[1]),
                _text(r[2]),
                _text(r[3]),
                int(cast(int, r[4])),
            )
            for r in rows
        ]
