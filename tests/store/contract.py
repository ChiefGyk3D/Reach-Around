# SPDX-License-Identifier: Apache-2.0
"""Backend-independent store tests. A backend's test module subclasses
``StoreContract`` and supplies the ``store`` fixture."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from reach_around.store import (
    Access,
    ClickDay,
    Coverage,
    Kind,
    Observation,
    Run,
    SeriesKey,
    Source,
    Store,
    StoreError,
    local_day,
)

NY = "America/New_York"
KEY = SeriesKey("bluesky", "example", "followers")
AGG_KEY = SeriesKey("bluesky", "example", "stream_minutes")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def snap(
    value: float = 10,
    observed: str = "2026-10-23T14:00:00Z",
    *,
    day: date | None = None,
    tz: str = NY,
    metric: str = "followers",
    **kw: Any,
) -> Observation:
    instant = at(observed)
    return Observation(
        platform="bluesky",
        account="example",
        metric=metric,
        kind=kw.pop("kind", Kind.SNAPSHOT),
        day=day if day is not None else local_day(instant, tz),
        day_tz=tz,
        observed_at=instant,
        value=value,
        **kw,
    )


def agg(
    value: float, day: str, observed: str, *, tz: str = NY, **kw: Any
) -> Observation:
    return snap(
        value,
        observed,
        day=date.fromisoformat(day),
        tz=tz,
        metric=kw.pop("metric", "stream_minutes"),
        kind=Kind.AGGREGATE,
        **kw,
    )


NOW = at("2026-10-23T19:00:00Z")


class StoreContract:
    # -- row counts -----------------------------------------------------------

    def test_execute_returns_affected_row_count(self, store: Store) -> None:
        store.kv_set("a", "1")
        assert store.db.execute("UPDATE kv SET value = '2' WHERE key = ?", ("a",)) == 1
        assert store.db.execute("UPDATE kv SET value = '2' WHERE key = ?", ("zz",)) == 0

    # -- snapshots ------------------------------------------------------------

    def test_write_same_value_twice_is_unchanged_not_inserted(
        self, store: Store
    ) -> None:
        first = store.write([snap(10)], now=NOW)
        second = store.write([snap(10, "2026-10-23T15:00:00Z")], now=NOW)
        assert (first.inserted, second.inserted, second.unchanged) == (1, 0, 1)

    def test_later_snapshot_same_day_replaces_on_read_and_keeps_history(
        self, store: Store
    ) -> None:
        store.write([snap(10, "2026-10-23T14:00:00Z")], now=NOW)
        store.write([snap(99, "2026-10-23T18:00:00Z")], now=NOW)
        assert [o.value for o in store.series(KEY)] == [99]
        assert len(store.revisions(KEY, date(2026, 10, 23))) == 2

    def test_replacement_is_deterministic_regardless_of_write_order(
        self, store: Store
    ) -> None:
        store.write([snap(99, "2026-10-23T18:00:00Z")], now=NOW)
        store.write([snap(10, "2026-10-23T14:00:00Z")], now=NOW)
        assert [o.value for o in store.series(KEY)] == [99]

    def test_snapshot_for_a_closed_day_is_refused(self, store: Store) -> None:
        result = store.write(
            [snap(5, "2026-10-22T14:00:00Z")], now=at("2026-10-23T14:00:00Z")
        )
        assert [r.reason for r in result.refused] == ["day_closed"]
        assert result.inserted == 0
        assert store.series(KEY) == []

    def test_future_day_refused(self, store: Store) -> None:
        result = store.write(
            [snap(5, "2026-10-25T14:00:00Z")], now=at("2026-10-23T14:00:00Z")
        )
        assert [r.reason for r in result.refused] == ["future_day"]
        assert store.series(KEY) == []

    def test_imported_backfill_into_a_closed_day_is_allowed(self, store: Store) -> None:
        old = snap(5, "2026-10-01T14:00:00Z", source=Source.IMPORTED)
        result = store.write([old], now=NOW)
        assert (result.inserted, result.refused) == (1, ())

    def test_fetched_snapshot_day_must_match_observed_at_local_day(self) -> None:
        with pytest.raises(ValueError):
            snap(5, "2026-10-23T03:00:00Z", day=date(2026, 10, 23))

    # -- daylight saving ------------------------------------------------------

    def test_local_day_boundaries_spring_forward(self) -> None:
        assert local_day(at("2027-03-14T04:59:59Z"), NY) == date(2027, 3, 13)
        assert local_day(at("2027-03-14T05:00:00Z"), NY) == date(2027, 3, 14)
        assert local_day(at("2027-03-15T03:59:59Z"), NY) == date(2027, 3, 14)
        assert local_day(at("2027-03-15T04:00:00Z"), NY) == date(2027, 3, 15)

    def test_local_day_boundaries_fall_back(self) -> None:
        assert local_day(at("2026-11-01T03:59:59Z"), NY) == date(2026, 10, 31)
        assert local_day(at("2026-11-01T04:00:00Z"), NY) == date(2026, 11, 1)
        assert local_day(at("2026-11-02T04:59:59Z"), NY) == date(2026, 11, 1)
        assert local_day(at("2026-11-02T05:00:00Z"), NY) == date(2026, 11, 2)

    def test_open_day_closes_at_account_local_midnight_spring(
        self, store: Store
    ) -> None:
        reading = snap(1, "2027-03-14T15:00:00Z")
        assert store.write([reading], now=at("2027-03-15T03:59:59Z")).inserted == 1
        later = snap(2, "2027-03-14T16:00:00Z")
        refused = store.write([later], now=at("2027-03-15T04:00:00Z")).refused
        assert [r.reason for r in refused] == ["day_closed"]

    def test_open_day_closes_at_account_local_midnight_fall(self, store: Store) -> None:
        reading = snap(1, "2026-11-01T15:00:00Z")
        assert store.write([reading], now=at("2026-11-02T04:59:59Z")).inserted == 1
        later = snap(2, "2026-11-01T16:00:00Z")
        refused = store.write([later], now=at("2026-11-02T05:00:00Z")).refused
        assert [r.reason for r in refused] == ["day_closed"]

    def test_day_tz_is_stored_and_returned_on_every_row(self, store: Store) -> None:
        store.write([snap(1), snap(2, metric="posts", tz="UTC")], now=NOW)
        zones = {o.metric: o.day_tz for k in store.keys() for o in store.series(k)}
        assert zones == {"followers": NY, "posts": "UTC"}

    def test_timezone_change_keeps_old_rows_and_both_zones_readable(
        self, store: Store
    ) -> None:
        old = snap(1, "2026-10-22T14:00:00Z", tz="UTC", source=Source.IMPORTED)
        store.write([old], now=NOW)
        store.write([snap(2, "2026-10-23T14:00:00Z", tz=NY)], now=NOW)
        rows = store.series(KEY)
        assert [(o.day, o.day_tz) for o in rows] == [
            (date(2026, 10, 22), "UTC"),
            (date(2026, 10, 23), NY),
        ]

    # -- aggregates -----------------------------------------------------------

    def test_aggregate_for_an_open_day_is_refused(self, store: Store) -> None:
        result = store.write([agg(5, "2026-10-23", "2026-10-23T14:00:00Z")], now=NOW)
        assert [r.reason for r in result.refused] == ["day_incomplete"]

    def test_aggregate_utc_day_completeness_uses_its_own_zone(
        self, store: Store
    ) -> None:
        reading = agg(7, "2026-10-23", "2026-10-23T23:59:59Z", tz="UTC")
        early = store.write([reading], now=at("2026-10-23T23:59:59Z"))
        assert [r.reason for r in early.refused] == ["day_incomplete"]
        reading2 = agg(7, "2026-10-23", "2026-10-24T00:00:00Z", tz="UTC")
        assert store.write([reading2], now=at("2026-10-24T00:00:00Z")).inserted == 1

    def test_aggregate_revision_within_settle_window_replaces_and_keeps_history(
        self, store: Store
    ) -> None:
        first = agg(100, "2026-10-22", "2026-10-23T14:00:00Z")
        store.write([first], now=at("2026-10-23T14:00:00Z"))
        second = agg(160, "2026-10-22", "2026-10-24T14:00:00Z")
        store.write([second], now=at("2026-10-24T14:00:00Z"))
        assert [o.value for o in store.series(AGG_KEY)] == [160]
        assert len(store.revisions(AGG_KEY, date(2026, 10, 22))) == 2

    def test_aggregate_after_settle_window_is_sealed(self, store: Store) -> None:
        now = at("2026-10-23T14:00:00Z")
        edge = agg(5, "2026-10-20", "2026-10-23T14:00:00Z")
        assert store.write([edge], now=now, settle_days=3).inserted == 1
        old = agg(5, "2026-10-19", "2026-10-23T14:00:00Z")
        sealed = store.write([old], now=now, settle_days=3)
        assert [r.reason for r in sealed.refused] == ["day_sealed"]
        assert sealed.inserted == 0

    def test_aggregate_same_value_is_unchanged(self, store: Store) -> None:
        store.write([agg(5, "2026-10-22", "2026-10-23T14:00:00Z")], now=NOW)
        again = store.write([agg(5, "2026-10-22", "2026-10-23T15:00:00Z")], now=NOW)
        assert (again.inserted, again.unchanged) == (0, 1)

    def test_metric_cannot_change_kind(self, store: Store) -> None:
        store.write([snap(5, metric="thing")], now=NOW)
        clash = agg(5, "2026-10-22", "2026-10-23T14:00:00Z", metric="thing")
        with pytest.raises(StoreError):
            store.write([clash], now=NOW)

    def test_imported_aggregate_for_today_or_future_is_refused(
        self, store: Store
    ) -> None:
        today = agg(5, "2026-10-23", "2026-10-23T14:00:00Z", source=Source.IMPORTED)
        future = agg(5, "2026-10-24", "2026-10-23T14:00:00Z", source=Source.TYPED)
        reasons = [r.reason for r in store.write([today, future], now=NOW).refused]
        assert reasons == ["day_incomplete", "future_day"]

    def test_imported_aggregate_for_any_past_day_is_accepted(
        self, store: Store
    ) -> None:
        old = agg(5, "2026-01-02", "2026-10-23T14:00:00Z", source=Source.IMPORTED)
        assert store.write([old], now=NOW).inserted == 1

    def test_partial_coverage_is_stored_and_returned(self, store: Store) -> None:
        part = agg(
            5,
            "2026-10-22",
            "2026-10-23T14:00:00Z",
            coverage=Coverage.PARTIAL,
            coverage_note="pages=10 cap",
        )
        store.write([part], now=NOW)
        row = store.series(AGG_KEY)[0]
        assert (row.coverage, row.coverage_note) == (Coverage.PARTIAL, "pages=10 cap")

    def test_coverage_note_rejects_control_characters_and_over_120(self) -> None:
        with pytest.raises(ValueError):
            snap(1, coverage_note="a\x07b")
        with pytest.raises(ValueError):
            snap(1, coverage_note="x" * 121)
        assert snap(1, coverage_note="x" * 120).coverage_note == "x" * 120

    # -- precedence and reads -------------------------------------------------

    def test_precedence_typed_over_imported_over_fetched(self, store: Store) -> None:
        store.write([snap(1, "2026-10-23T14:00:00Z")], now=NOW)
        imported = snap(2, "2026-10-23T10:00:00Z", source=Source.IMPORTED)
        store.write([imported], now=NOW)
        assert [o.value for o in store.series(KEY)] == [2]
        typed = snap(3, "2026-10-23T09:00:00Z", source=Source.TYPED)
        store.write([typed], now=NOW)
        assert [(o.value, o.source) for o in store.series(KEY)] == [(3, Source.TYPED)]

    def test_series_orders_by_day_and_honors_since(self, store: Store) -> None:
        for day, value in (("23", 3), ("21", 1), ("22", 2)):
            row = snap(value, f"2026-10-{day}T14:00:00Z", source=Source.IMPORTED)
            store.write([row], now=NOW)
        assert [o.value for o in store.series(KEY)] == [1, 2, 3]
        assert [o.value for o in store.series(KEY, since=date(2026, 10, 22))] == [2, 3]
        latest = store.latest(KEY)
        assert latest is not None and latest.value == 3
        assert store.latest(SeriesKey("x", "y", "z")) is None

    def test_series_returns_one_row_per_day(self, store: Store) -> None:
        for hour in (10, 12, 14):
            store.write([snap(hour, f"2026-10-23T{hour}:00:00Z")], now=NOW)
        assert len(store.series(KEY)) == 1

    # -- runs -----------------------------------------------------------------

    def test_failed_run_is_stale_not_zero(self, store: Store) -> None:
        store.write([snap(5, "2026-10-20T14:00:00Z")], now=at("2026-10-20T15:00:00Z"))
        ok = Run("bluesky", "example", at("2026-10-20T14:00:00Z"), True, None)
        bad = Run("bluesky", "example", at("2026-10-22T14:00:00Z"), False, "http 500")
        store.record_run(ok)
        store.record_run(bad)
        assert store.stale_since("bluesky", "example") == date(2026, 10, 20)
        assert [o.value for o in store.series(KEY)] == [5]

    def test_partial_run_counts_as_success_for_staleness(self, store: Store) -> None:
        bad = Run("bluesky", "example", at("2026-10-20T14:00:00Z"), False, "x")
        part = Run(
            "bluesky", "example", at("2026-10-21T14:00:00Z"), True, None, partial=True
        )
        store.record_run(bad)
        store.record_run(part)
        assert store.stale_since("bluesky", "example") is None

    def test_stale_since_none_after_recovery(self, store: Store) -> None:
        for day, ok in ((20, True), (21, False), (22, True)):
            when = at(f"2026-10-{day}T14:00:00Z")
            store.record_run(Run("bluesky", "example", when, ok, None if ok else "x"))
        assert store.stale_since("bluesky", "example") is None
        assert store.last_ok("bluesky", "example") == at("2026-10-22T14:00:00Z")
        last = store.last_run("bluesky", "example")
        assert last is not None and last.ok

    def test_never_ok_stale_since_is_first_failure_date(self, store: Store) -> None:
        for day in (21, 22):
            when = at(f"2026-10-{day}T14:00:00Z")
            store.record_run(Run("bluesky", "example", when, False, "x"))
        assert store.stale_since("bluesky", "example") == date(2026, 10, 21)
        assert store.last_ok("bluesky", "example") is None
        assert store.stale_since("nobody", "nothing") is None

    # -- constraints and retention -------------------------------------------

    @pytest.mark.parametrize(
        "kw",
        [
            {"value": float("nan")},
            {"value": float("inf")},
            {"value": -1},
            {"value": True},
            {"platform": "Blue Sky"},
            {"account": "../x"},
            {"metric": ""},
            {"day_tz": "Mars/Base"},
            {"day_tz": "../etc/localtime"},
            {"observed_at": datetime(2026, 10, 23, 14, 0)},
            {"observed_at": datetime.fromisoformat("2026-10-23T14:00:00-04:00")},
        ],
    )
    def test_observation_rejects_nan_inf_negative_bad_slugs_bad_tz_and_naive_instants(
        self, kw: dict[str, Any]
    ) -> None:
        base: dict[str, Any] = {
            "platform": "bluesky",
            "account": "example",
            "metric": "followers",
            "kind": Kind.SNAPSHOT,
            "day": date(2026, 10, 23),
            "day_tz": "UTC",
            "observed_at": at("2026-10-23T14:00:00Z"),
            "value": 1,
        }
        assert Observation(**base).value == 1
        with pytest.raises(ValueError):
            Observation(**{**base, **kw})

    def test_expired_owner_row_purged_and_public_counts_survive(
        self, store: Store
    ) -> None:
        now = at("2026-09-20T15:00:00Z")
        owner = snap(
            4,
            "2026-09-20T14:00:00Z",
            metric="reach",
            access=Access.OWNER,
            expires_at=date(2026, 10, 1),
        )
        public = snap(9, "2026-09-20T14:00:00Z")
        store.write([owner, public], now=now)
        assert store.purge_expired(date(2026, 10, 23)) == 1
        assert store.series(SeriesKey("bluesky", "example", "reach")) == []
        assert [o.value for o in store.series(KEY)] == [9]

    def test_owner_row_without_expiry_is_kept(self, store: Store) -> None:
        store.write([snap(4, metric="reach", access=Access.OWNER)], now=NOW)
        assert store.purge_expired(date(2030, 1, 1)) == 0

    def test_owner_row_not_yet_expired_is_kept(self, store: Store) -> None:
        row = snap(
            4, metric="reach", access=Access.OWNER, expires_at=date(2026, 10, 23)
        )
        store.write([row], now=NOW)
        assert store.purge_expired(date(2026, 10, 23)) == 0
        assert store.purge_expired(date(2026, 10, 24)) == 1

    def test_purge_never_touches_imported_or_typed_public_rows(
        self, store: Store
    ) -> None:
        old = snap(1, "2026-01-01T10:00:00Z", source=Source.IMPORTED)
        newer = snap(2, "2026-01-02T10:00:00Z", source=Source.TYPED)
        store.write([old], now=NOW)
        store.write([newer], now=NOW)
        assert store.purge_expired(date(2099, 1, 1)) == 0
        assert len(store.series(KEY)) == 2

    @pytest.mark.parametrize(
        ("source", "access", "origin"),
        [
            (Source.FETCHED, Access.PUBLIC, "public_api"),
            (Source.FETCHED, Access.OWNER, "owner_authorized_api"),
            (Source.IMPORTED, Access.PUBLIC, "imported"),
            (Source.IMPORTED, Access.OWNER, "imported"),
            (Source.TYPED, Access.PUBLIC, "typed"),
            (Source.TYPED, Access.OWNER, "typed"),
        ],
    )
    def test_origin_tag_for_each_source_and_access(
        self, source: Source, access: Access, origin: str
    ) -> None:
        assert snap(1, source=source, access=access).origin == origin

    def test_public_observation_with_expiry_refused(self) -> None:
        with pytest.raises(ValueError):
            snap(1, access=Access.PUBLIC, expires_at=date(2026, 12, 1))

    def test_kv_basic_and_cas_semantics(self, store: Store) -> None:
        assert store.kv_get("k") is None
        assert store.kv_cas("k", None, "a") is True
        assert store.kv_cas("k", None, "b") is False
        assert store.kv_cas("k", "zzz", "b") is False
        assert store.kv_cas("k", "a", "b") is True
        assert store.kv_get("k") == "b"
        store.kv_set("k", "c")
        assert store.kv_get("k") == "c"

    def test_kv_cas_has_exactly_one_winner(self, store: Store) -> None:
        gate = threading.Barrier(8)
        results: list[bool] = []
        errors: list[BaseException] = []

        def race() -> None:
            try:
                gate.wait()
                results.append(store.kv_cas("k", None, "x"))
            except BaseException as exc:  # reported by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=race) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert results.count(True) == 1 and len(results) == 8

    def test_append_only_the_only_observation_delete_is_purge(self) -> None:
        import reach_around

        root = Path(reach_around.__file__).parent
        deletes: list[str] = []
        updates: list[str] = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "DELETE FROM observations" in text:
                deletes.append(path.name)
            if "UPDATE observations" in text:
                updates.append(path.name)
        assert deletes == ["base.py"]
        assert updates == []

    # -- clicks ---------------------------------------------------------------

    @staticmethod
    def _rows() -> list[ClickDay]:
        return [
            ClickDay(date(2026, 10, 22), NY, "docs", "US", 3),
            ClickDay(date(2026, 10, 22), NY, "docs", "", 2),
            ClickDay(date(2026, 10, 23), NY, "blog", "DE", 5),
        ]

    def test_apply_click_batch_adds_counts_moves_cursor_and_records_the_window(
        self, store: Store
    ) -> None:
        assert store.click_cursor() == 0
        assert store.apply_click_batch(self._rows(), lo=0, hi=1000, source_total=10)
        assert store.click_cursor() == 1000
        rows = store.db.query(
            "SELECT lo_id, hi_id, bucket_count, total_count FROM click_batches"
        )
        assert rows == [(0, 1000, 3, 10)]
        assert sum(c.count for c in store.clicks(date(2026, 10, 1))) == 10
        assert [c.count for c in store.clicks(date(2026, 10, 23))] == [5]

    def test_apply_click_batch_rejects_a_stale_cursor(self, store: Store) -> None:
        assert store.apply_click_batch(self._rows(), lo=0, hi=1000, source_total=10)
        again = store.apply_click_batch(self._rows(), lo=0, hi=1000, source_total=10)
        assert again is False
        assert sum(c.count for c in store.clicks(date(2026, 10, 1))) == 10
        assert store.click_cursor() == 1000

    def test_apply_click_batch_adds_to_existing_counts(self, store: Store) -> None:
        store.apply_click_batch(self._rows(), lo=0, hi=10, source_total=10)
        store.apply_click_batch(self._rows(), lo=10, hi=20, source_total=10)
        assert sum(c.count for c in store.clicks(date(2026, 10, 1))) == 20

    def test_apply_click_batch_atomic(self, store: Store) -> None:
        bad = [*self._rows(), ClickDay(date(2026, 10, 23), NY, "blog", "DE", -1)]
        with pytest.raises(ValueError):
            store.apply_click_batch(bad, lo=0, hi=10, source_total=9)
        assert store.clicks(date(2026, 1, 1)) == []
        assert store.click_cursor() == 0

    def test_apply_click_batch_rejects_counts_that_do_not_sum_to_source_total(
        self, store: Store
    ) -> None:
        with pytest.raises(ValueError):
            store.apply_click_batch(self._rows(), lo=0, hi=10, source_total=11)
        assert store.click_cursor() == 0
        assert store.clicks(date(2026, 1, 1)) == []

    def test_dead_letter_upsert_counts_attempts_and_resolves_once(
        self, store: Store
    ) -> None:
        t1 = at("2026-10-23T10:00:00Z")
        t2 = at("2026-10-23T11:00:00Z")
        t3 = at("2026-10-23T12:00:00Z")
        store.record_dead_letter(0, 50, "bad bucket", t1)
        store.record_dead_letter(0, 50, "bad bucket again", t2)
        (letter,) = store.dead_letters()
        assert (letter.attempts, letter.first_seen, letter.last_seen) == (2, t1, t2)
        store.resolve_dead_letter(0, 50, t3, "applied")
        store.resolve_dead_letter(0, 50, t3 + timedelta(hours=1), "skipped")
        assert store.dead_letters() == []
        (done,) = store.dead_letters(open_only=False)
        assert (done.resolution, done.resolved_at) == ("applied", t3)
