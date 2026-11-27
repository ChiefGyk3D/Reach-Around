# SPDX-License-Identifier: Apache-2.0
"""Slice 3 of the fediverse collector (#923): the Mastodon provider.

Hermetic: a fake transport serves recorded-style fixtures; nothing opens a
socket and no live host is called. The instance, the account and every id are
synthetic (``social.example``, ``alice``)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from fakenet import FakeTransport, reply
from reach_around.config.fediverse import fediverse_measures
from reach_around.config.import_guard import ImportGuardError
from reach_around.counts import mastodon_feed as masto
from reach_around.counts.fediverse_rows import InteractionRow, NotProvided

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
HOST = "social.example"
ACCT = "alice"
ME = "1000"
OTHER = "2000"
DATE_OK = {"date": "Thu, 26 Nov 2026 12:00:00 GMT"}
LOOKUP = f"https://{HOST}/api/v1/accounts/lookup"
STATUSES = f"https://{HOST}/api/v1/accounts/{ME}/statuses"
START = NOW - timedelta(days=30)


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def status(
    n: int,
    when: datetime,
    *,
    boosts: int = 0,
    favs: int = 1,
    replies: int = 0,
    author: str = ME,
    **extra: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(n),
        "created_at": stamp(when),
        "visibility": "public",
        "in_reply_to_id": None,
        "in_reply_to_account_id": None,
        "reblog": None,
        "account": {"id": author, "acct": "alice", "display_name": "Alice"},
        "content": "<p>hello @friend alice@example.com 192.0.2.1</p>",
        "reblogs_count": boosts,
        "favourites_count": favs,
        "replies_count": replies,
    }
    base.update(extra)
    return base


def lookup(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": ME,
        "acct": "alice",
        "followers_count": 321,
        "statuses_count": 77,
        "moved": None,
    }
    base.update(over)
    return base


class Pages:
    """Serves the lookup and pages of statuses, one page per statuses request."""

    def __init__(
        self,
        pages: list[Any],
        *,
        look: Any = None,
        headers: dict[str, str] | None = None,
        page_headers: dict[int, dict[str, str]] | None = None,
    ) -> None:
        self.pages = pages
        self.headers = DATE_OK if headers is None else headers
        self.page_headers = page_headers or {}
        self.look = lookup() if look is None else look
        self.served = 0
        self.transport = FakeTransport(
            {LOOKUP: lambda r: self._answer(self.look), STATUSES: self._page}
        )

    def _answer(self, payload: Any, extra: dict[str, str] | None = None) -> Any:
        if hasattr(payload, "status"):
            return payload
        return reply(payload, headers={**self.headers, **(extra or {})})

    def _page(self, request: Any) -> Any:
        index = self.served
        page = self.pages[index] if index < len(self.pages) else []
        if hasattr(page, "status"):
            return page  # a canned answer repeats on every retry
        self.served += 1
        return self._answer(page, self.page_headers.get(index))


def run(pages: Pages, **kw: Any) -> masto.MastodonRead:
    sleeps: list[float] = kw.pop("sleeps", [])
    kw.setdefault("acct", ACCT)
    return masto.collect(
        HOST,
        now=NOW,
        transport=pages.transport,
        sleep=sleeps.append,
        jitter=lambda: 0.0,
        **kw,
    )


def rows_of(read: masto.MastodonRead) -> list[tuple[int, int, int]]:
    assert isinstance(read.rows, tuple)
    return [(r.boosts, r.favorites, r.replies) for r in read.rows]


def day(n: float) -> datetime:
    return NOW - timedelta(days=n)


# --- the happy path and the endpoints ------------------------------------------


def test_two_pages_become_sorted_rows_and_the_endpoints_are_exact() -> None:
    p = Pages(
        [
            [status(30, day(1), favs=9, boosts=2), status(29, day(2))],
            [status(10, day(3), favs=3, replies=4)],
            [],
        ]
    )
    read = run(p)
    assert read.complete and read.reason is None
    assert (read.followers, read.posts, read.calls) == (321, 77, 4)
    assert rows_of(read) == [(0, 1, 0), (0, 3, 4), (2, 9, 0)]  # sorted by value
    urls = p.transport.urls
    assert {c.host for c in p.transport.calls} == {HOST}
    assert urls[0] == f"{LOOKUP}?acct=alice"
    q1 = parse_qs(urlsplit(urls[1]).query)
    assert urlsplit(urls[1]).path == f"/api/v1/accounts/{ME}/statuses"
    assert q1 == {
        "limit": ["40"],
        "exclude_replies": ["true"],
        "exclude_reblogs": ["true"],
    }
    # the cursor is the smallest id of the previous page, built here
    assert parse_qs(urlsplit(urls[2]).query)["max_id"] == ["29"]
    assert parse_qs(urlsplit(urls[3]).query)["max_id"] == ["10"]
    assert all(c.method == "GET" and c.body is None for c in p.transport.calls)
    assert all("Authorization" not in c.headers for c in p.transport.calls)


def test_a_pause_sits_between_calls_and_none_before_the_first() -> None:
    sleeps: list[float] = []
    p = Pages([[status(5, day(1))], []])
    run(p, sleeps=sleeps)
    assert sleeps == [masto.PAUSE] * 2  # before the 2nd and 3rd call only


def test_an_account_id_skips_the_lookup_and_reads_no_followers() -> None:
    p = Pages([[status(5, day(1))], []])
    read = run(p, acct=None, account_id=ME)
    assert read.complete and (read.followers, read.posts) == (None, None)
    assert read.calls == 2
    assert all("lookup" not in u for u in p.transport.urls)
    assert run(Pages([[]]), acct=None, account_id=int(ME)).complete


# --- the window --------------------------------------------------------------


def test_window_edges_by_created_at() -> None:
    items = [
        status(9, NOW + timedelta(minutes=10)),  # exactly the skew: in
        status(8, NOW + timedelta(minutes=10, seconds=1)),  # beyond: dropped
        status(7, START),  # exactly the start: in
        status(6, START - timedelta(seconds=1)),  # one second before: out
        status(5, NOW),
    ]
    read = run(Pages([items, []]))
    assert read.complete and len(rows_of(read)) == 3
    assert read.dropped_future == 1


def test_an_old_page_does_not_end_the_walk_and_later_posts_still_count() -> None:
    old = [status(50 - i, day(40 + i)) for i in range(5)]
    read = run(Pages([old, [status(10, day(2), favs=7)], []]))
    assert read.complete and rows_of(read) == [(0, 7, 0)]
    assert read.calls == 4


def test_the_walk_ends_only_at_an_empty_page_or_the_budget() -> None:
    p = Pages([[status(5, day(60))]])  # one old post, then (default) empty
    read = run(p)
    assert read.complete and rows_of(read) == []  # an honest finished empty window
    assert p.served == 2  # the old page did not end it: the empty page did


# --- what is and is not counted -----------------------------------------------


def test_boosts_other_authors_replies_and_non_public_are_never_counted() -> None:
    items = [
        status(20, day(1), reblog={"id": "9"}),
        status(19, day(1), author=OTHER),
        status(18, day(1), in_reply_to_id="7", in_reply_to_account_id=OTHER),
        status(17, day(1), visibility="private"),
        status(16, day(1), visibility="direct"),
        status(15, day(1), favs=4),  # public: counted
        status(14, day(1), visibility="unlisted", favs=5),  # unlisted: counted
        status(13, day(1), in_reply_to_id="12", in_reply_to_account_id=ME, favs=6),
    ]
    read = run(Pages([items, []]))
    assert read.complete and sorted(r[1] for r in rows_of(read)) == [4, 5, 6]


def test_a_pinned_old_post_neither_counts_nor_ends_the_walk() -> None:
    page = [status(3, day(400), pinned=True), status(99, day(1), favs=8)]
    read = run(Pages([page, []]))
    assert read.complete and rows_of(read) == [(0, 8, 0)]


def test_a_status_listed_twice_in_one_run_counts_once() -> None:
    read = run(Pages([[status(5, day(1))], [status(5, day(1))]]))
    # the second page repeats an id at or above the cursor: no progress, partial
    assert read.reason == "partial_pages"
    dup = run(Pages([[status(5, day(1)), status(5, day(1))], []]))
    assert dup.complete and len(rows_of(dup)) == 1


def test_a_cursor_that_does_not_fall_stops_the_walk() -> None:
    pages = [[status(9, day(1))], [status(9, day(1))], [status(9, day(1))]]
    read = run(Pages(pages))
    assert read.reason == "partial_pages"


def test_hostile_extra_keys_and_text_never_reach_a_row_or_the_snapshot() -> None:
    hostile = status(
        5,
        day(1),
        favs=2,
        extra_blob={"nested": [{"handle": "@bob", "ip": "203.0.113.9"}]},
        url="https://elsewhere.example/@bob/5",
        card={"url": "https://evil.example/"},
        mentions=[{"acct": "bob@elsewhere.example"}],
    )
    read = run(Pages([[hostile], []]))
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None
    text = json.dumps(snap)
    for needle in ("bob", "alice", "elsewhere", "203.0.113", "example", "friend"):
        assert needle not in text
    assert snap["interactions"] == [{"boosts": 0, "favorites": 2, "replies": 0}]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s.update(favourites_count="3"),
        lambda s: s.update(favourites_count=True),
        lambda s: s.update(reblogs_count=-1),
        lambda s: s.update(replies_count=1.5),
        lambda s: s.update(replies_count=None),
        lambda s: s.update(reblogs_count={"n": 1}),
        lambda s: s.update(favourites_count=10**9 + 1),
        lambda s: s.pop("favourites_count"),
        lambda s: s.pop("replies_count"),
        lambda s: s.update(created_at="yesterday"),
        lambda s: s.update(created_at="2026-11-25T10:00:00"),  # no offset
        lambda s: s.update(created_at=None),
        lambda s: s.pop("created_at"),
        lambda s: s.update(id=5),
        lambda s: s.update(id="abc"),
        lambda s: s.update(id="1" * 21),
        lambda s: s.pop("id"),
        lambda s: s.update(visibility="mystery"),
        lambda s: s.pop("visibility"),
        lambda s: s.update(reblog="yes"),
        lambda s: s.update(account=None),
        lambda s: s.update(account={"id": 1000}),
        lambda s: s.update(in_reply_to_account_id=5),
        lambda s: s.update(in_reply_to_id="7"),  # a reply to an unknown author
    ],
)
def test_a_malformed_counted_status_rejects_the_whole_read(mutate: Any) -> None:
    bad = status(5, day(1))
    mutate(bad)
    read = run(Pages([[status(6, day(1)), bad], []]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert masto.history_aggregates(read) is None


@pytest.mark.parametrize("item", [None, 5, "x", [], [1]])
def test_a_status_that_is_not_an_object_is_incomplete(item: Any) -> None:
    assert run(Pages([[item], []])).reason == "bad_shape"


def test_a_number_over_eighteen_digits_is_refused_by_the_parser() -> None:
    body = b'[{"id":"1","reblogs_count":1' + b"0" * 30 + b"}]"
    p = Pages([reply(body=body)])
    assert run(p).reason == "bad_json"


def test_a_page_that_is_not_an_array_or_too_long_is_incomplete() -> None:
    assert run(Pages([{"statuses": []}])).reason == "bad_json"
    big = [status(100 + i, day(1)) for i in range(81)]
    assert run(Pages([big])).reason == "bad_shape"


def test_old_posts_with_malformed_counts_are_incomplete_not_skipped() -> None:
    old = status(5, day(90), favourites_count="x")  # before the window, still checked
    read = run(Pages([[old], []]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


_BAD_COUNTS = ["3", True, -1, 1.5, None, {"n": 1}, [], 10**9 + 1]
_BAD_BY_FIELD: dict[str, list[Any]] = {
    "id": [5, None, "", "abc", "01", "1" * 21, ["1"], True],
    "created_at": [5, None, "", "yesterday", "2026-11-25T10:00:00", ["x"]],
    "visibility": [5, None, "", "mystery", ["public"], {"a": 1}],
    "reblog": ["yes", 1, True, ["x"], ""],
    "in_reply_to_id": [7, True, "", ["7"], {"id": "7"}, "07"],
    "in_reply_to_account_id": [5, True, "", ["x"], {"a": 1}],
    "account": [None, 5, "x", {"id": 1000}, {}, {"id": ""}],
    "reblogs_count": _BAD_COUNTS,
    "favourites_count": _BAD_COUNTS,
    "replies_count": _BAD_COUNTS,
}
_SHAPES = {  # every way a well formed status can be left out of the count
    "own": lambda: status(5, day(1)),
    "old": lambda: status(5, day(90)),
    "future": lambda: status(5, NOW + timedelta(days=3)),
    "foreign": lambda: status(5, day(1), author=OTHER),
    "hidden": lambda: status(5, day(1), visibility="private"),
    "boost": lambda: status(5, day(1), reblog={"id": "9"}),
}
_CASES = [
    (shape, field, bad)
    for shape in sorted(_SHAPES)
    for field, values in _BAD_BY_FIELD.items()
    for bad in values
]


@pytest.mark.parametrize(("shape", "field", "bad"), _CASES)
def test_every_field_of_every_kind_of_status_is_checked_one_way(
    shape: str, field: str, bad: Any
) -> None:
    item = _SHAPES[shape]()
    # The reply pair is set so only the field under test is malformed.
    if field == "in_reply_to_id":
        item["in_reply_to_account_id"] = ME
    if field == "in_reply_to_account_id":
        item["in_reply_to_id"] = "4"
    item[field] = bad
    read = run(Pages([[item], []]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


@pytest.mark.parametrize("shape", sorted(_SHAPES))
@pytest.mark.parametrize(
    "field",
    ["id", "created_at", "visibility", "account"]
    + ["reblogs_count", "favourites_count", "replies_count"],
)
def test_a_missing_required_field_of_every_kind_of_status_is_incomplete(
    shape: str, field: str
) -> None:
    item = _SHAPES[shape]()
    del item[field]
    read = run(Pages([[item], []]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


def test_a_missing_or_null_reply_or_boost_marker_is_not_a_reply_or_boost() -> None:
    item = status(5, day(1), favs=3)
    del item["reblog"]
    del item["in_reply_to_id"]
    del item["in_reply_to_account_id"]
    read = run(Pages([[item, status(4, day(1), favs=2)], []]))
    assert read.complete and sorted(rows_of(read)) == [(0, 2, 0), (0, 3, 0)]


def test_a_reply_id_and_its_target_author_go_together() -> None:
    only_target = status(5, day(1), in_reply_to_account_id=ME)
    assert run(Pages([[only_target], []])).reason == "bad_shape"
    only_id = status(5, day(1), in_reply_to_id="4")
    assert run(Pages([[only_id], []])).reason == "bad_shape"


# --- a status listed twice: one row, the elementwise maximum -------------------


@pytest.mark.parametrize("flip", [False, True])
def test_conflicting_duplicates_keep_the_elementwise_maximum_in_either_order(
    flip: bool,
) -> None:
    low = status(5, day(1), boosts=1, favs=0, replies=9)
    high = status(5, day(1), boosts=4, favs=100, replies=2)
    page = [high, low] if flip else [low, high]
    read = run(Pages([page, []]))
    assert read.complete and rows_of(read) == [(4, 100, 9)]


def test_conflicting_duplicates_across_pages_keep_the_maximum() -> None:
    def walk(first_favs: int, second_favs: int) -> list[tuple[int, int, int]]:
        first = [status(10, day(1), favs=first_favs), status(5, day(1), favs=2)]
        second = [status(10, day(1), favs=second_favs), status(3, day(1), favs=1)]
        read = run(Pages([first, second, []]))
        assert read.complete
        return sorted(rows_of(read))

    assert walk(0, 100) == walk(100, 0) == [(0, 1, 0), (0, 2, 0), (0, 100, 0)]


def test_duplicates_that_disagree_on_whether_they_count_are_incomplete() -> None:
    counted = status(5, day(1))
    hidden = status(5, day(1), visibility="private")
    for page in ([counted, hidden], [hidden, counted]):
        assert run(Pages([page, []])).reason == "bad_shape"
    a_future = status(6, NOW + timedelta(days=3))
    assert run(Pages([[a_future, a_future], []])).dropped_future == 1


# --- budgets, retries and the deadline -----------------------------------------


def test_the_page_cap_stops_at_thirteen_calls_and_is_incomplete() -> None:
    pages = [[status(1000 - i, day(1))] for i in range(30)]
    p = Pages(pages)
    read = run(p)
    assert read.calls == 13 == masto.MAX_CALLS == len(p.transport.calls)
    assert isinstance(read.rows, NotProvided) and read.reason == "page_cap"


def test_the_six_hundred_item_feed_reports_page_cap_by_design() -> None:
    # 600 posts at 40 a page is 15 pages; the budget holds 12, so the read is
    # incomplete and keeps the prior rows. This is the documented capacity limit.
    pages = [[status(600 - (40 * i + j), day(1)) for j in range(40)] for i in range(15)]
    read = run(Pages(pages))
    assert read.reason == "page_cap" and read.calls == 13
    prior = [InteractionRow(1, 2, 3)]
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None and snap["complete"] is False
    assert snap["interactions"] == [{"boosts": 1, "favorites": 2, "replies": 3}]
    assert masto.history_aggregates(read) is None


class Flaky:
    """Answers every distinct request with a 503 once, then lets it through."""

    def __init__(self, pages: list[Any]) -> None:
        self.inner = Pages(pages).transport
        self.seen: set[str] = set()
        self.requests = 0

    def __call__(self, request: Any) -> Any:
        self.requests += 1
        if request.path not in self.seen:
            self.seen.add(request.path)
            return reply({}, status=503)
        return self.inner(request)


def test_a_retry_that_succeeds_is_counted_as_a_request() -> None:
    flaky = Flaky([[status(5, day(1))], []])
    read = masto.collect(
        HOST, ACCT, now=NOW, transport=flaky, sleep=lambda s: None, jitter=lambda: 0.0
    )
    assert read.complete and len(rows_of(read)) == 1
    assert flaky.requests == 6 == read.calls  # three operations, one retry each


def test_retries_draw_on_the_same_thirteen_request_budget() -> None:
    flaky = Flaky([[status(1000 - i, day(1))] for i in range(30)])
    read = masto.collect(
        HOST, ACCT, now=NOW, transport=flaky, sleep=lambda s: None, jitter=lambda: 0.0
    )
    assert flaky.requests == 13  # never 13 operations x 2 tries
    assert read.calls == 13 and read.reason == "page_cap"


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class Slow:
    """A transport that takes ``cost`` seconds per request on the fake clock."""

    def __init__(self, inner: Any, clock: Clock, costs: list[float]) -> None:
        self.inner, self.clock, self.costs = inner, clock, costs
        self.deadlines: list[float] = []

    def __call__(self, request: Any) -> Any:
        self.deadlines.append(request.deadline)
        self.clock.t += self.costs[min(len(self.deadlines) - 1, len(self.costs) - 1)]
        return self.inner(request)


def _slow(
    pages: list[Any], clock: Clock, costs: list[float], sleep: Any = None
) -> tuple[masto.MastodonRead, Slow]:
    slow = Slow(Pages(pages).transport, clock, costs)
    read = masto.collect(
        HOST,
        ACCT,
        now=NOW,
        transport=slow,
        sleep=sleep or clock.sleep,
        jitter=lambda: 0.0,
        clock=clock,
    )
    return read, slow


def test_one_absolute_deadline_caps_every_request_and_the_pauses() -> None:
    pages = [[status(1000 - i, day(1))] for i in range(30)]
    read, slow = _slow(pages, Clock(), [40.0])
    assert read.reason == "timeout"
    assert all(d <= masto.PLATFORM_DEADLINE for d in slow.deadlines)
    assert slow.deadlines[2] == masto.PLATFORM_DEADLINE  # 8 s left at the third
    assert len(slow.deadlines) == 3  # no fourth request after the deadline


def test_a_pause_that_would_cross_the_deadline_is_not_taken() -> None:
    sleeps: list[float] = []
    clock = Clock()

    def sleep(s: float) -> None:
        sleeps.append(s)
        clock.sleep(s)

    read, slow = _slow([[status(5, day(1))], []], clock, [89.5], sleep)
    assert read.reason == "timeout" and len(slow.deadlines) == 1 and sleeps == []


def test_a_retry_wait_that_would_cross_the_deadline_ends_the_run() -> None:
    clock = Clock()
    slow = Slow(lambda r: reply({}, status=503), clock, [89.5])
    read = masto.collect(
        HOST,
        ACCT,
        now=NOW,
        transport=slow,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        clock=clock,
    )
    assert read.reason == "timeout" and len(slow.deadlines) == 1


def test_a_terminal_page_that_returns_after_the_deadline_is_discarded() -> None:
    read, slow = _slow([[status(5, day(1), favs=5)], []], Clock(), [1.0, 1.0, 95.0])
    assert len(slow.deadlines) == 3
    assert isinstance(read.rows, NotProvided) and read.reason == "timeout"
    assert not read.complete and masto.history_aggregates(read) is None


def test_a_lookup_that_returns_after_the_deadline_is_discarded() -> None:
    read, slow = _slow([[]], Clock(), [95.0])
    assert read.reason == "timeout" and len(slow.deadlines) == 1
    assert (read.followers, read.posts) == (None, None)


def test_a_pause_that_runs_past_the_deadline_ends_the_run() -> None:
    clock = Clock()

    def long_sleep(seconds: float) -> None:
        clock.t += 200.0

    read, slow = _slow([[status(5, day(1))]], clock, [1.0], long_sleep)
    assert read.reason == "timeout" and len(slow.deadlines) == 1


def test_a_response_on_time_is_still_accepted() -> None:
    read, _ = _slow([[status(5, day(1))], []], Clock(), [1.0, 1.0, 80.0])
    assert read.complete and len(rows_of(read)) == 1


# --- failures and fixed words --------------------------------------------------


@pytest.mark.parametrize(
    ("response", "word"),
    [
        (reply({"error": "x"}, status=401), "needs_token"),
        (reply({"error": "x"}, status=403), "needs_token"),
        (reply({"error": "Record not found"}, status=404), "account_not_found"),
        (reply({}, status=410), "account_suspended"),
        (reply({}, status=429, headers={"retry-after": "3600"}), "rate_limited"),
        (reply({}, status=500), "server_error"),
        (reply({}, status=422), "bad_shape"),
        (reply(body=b"<html>", headers={"content-type": "text/html"}), "content_type"),
        (reply(body=b"{nope"), "bad_json"),
    ],
)
def test_lookup_failures_use_the_fixed_words(response: Any, word: str) -> None:
    read = run(Pages([[]], look=response))
    assert isinstance(read.rows, NotProvided) and read.reason == word
    assert (read.followers, read.posts) == (None, None)


def test_a_failed_statuses_call_with_a_configured_id_keeps_its_word() -> None:
    assert run(Pages([reply({}, status=401)]), acct=None, account_id=ME).reason == (
        "needs_token"
    )
    assert run(Pages([reply({}, status=404)]), acct=None, account_id=ME).reason == (
        "account_not_found"
    )


def test_a_failed_second_page_is_partial_and_keeps_the_prior_rows() -> None:
    read = run(Pages([[status(5, day(1))], reply({}, status=500)]))
    assert read.reason == "partial_pages"
    assert (read.followers, read.posts) == (321, 77)  # the profile was complete
    prior = [InteractionRow(4, 5, 6)]
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None and snap["complete"] is False
    assert snap["interactions"] == [{"boosts": 4, "favorites": 5, "replies": 6}]
    assert snap["reason"] == "partial_pages" and snap["followers"] == 321


def test_a_rate_limit_or_token_failure_on_a_later_page_keeps_its_own_word() -> None:
    assert run(Pages([[status(5, day(1))], reply({}, status=401)])).reason == (
        "needs_token"
    )
    limited = reply({}, status=429, headers={"retry-after": "3600"})
    assert run(Pages([[status(5, day(1))], limited])).reason == "rate_limited"


def test_an_oversized_page_is_partial_after_the_first_and_too_large_is_mapped() -> None:
    huge = reply(body=b"[" + b"1," * 600_000 + b"1]")
    assert run(Pages([huge])).reason == "partial_pages"
    assert run(Pages([[status(5, day(1))], huge])).reason == "partial_pages"


def test_a_moved_or_suspended_account_is_never_followed() -> None:
    moved = lookup(moved={"id": "5", "acct": "alice@elsewhere.example"})
    p = Pages([[]], look=moved)
    assert run(p).reason == "account_moved"
    assert len(p.transport.calls) == 1  # no call for the new account
    assert run(Pages([[]], look=lookup(suspended=True))).reason == "account_suspended"


@pytest.mark.parametrize(
    "look",
    [
        lookup(id=1000),
        lookup(id="abc"),
        lookup(id=None),
        {"followers_count": 1},
        [],
    ],
)
def test_a_lookup_without_a_good_id_is_bad_shape(look: Any) -> None:
    assert run(Pages([[]], look=look)).reason in ("bad_shape", "bad_json")


@pytest.mark.parametrize("field", ["followers_count", "statuses_count"])
@pytest.mark.parametrize(
    "bad", ["9", -1, True, 1.5, None, [], {"n": 1}, 10**12 + 1, "MISSING"]
)
def test_a_malformed_or_missing_lookup_count_is_never_a_complete_read(
    field: str, bad: Any
) -> None:
    look = lookup()
    if bad == "MISSING":
        del look[field]
    else:
        look[field] = bad
    read = run(Pages([[]], look=look))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert (read.followers, read.posts) == (None, None)
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None and snap["complete"] is False


def test_a_configured_account_id_needs_no_lookup_counts() -> None:
    read = run(Pages([[]]), acct=None, account_id=ME)
    assert read.complete and (read.followers, read.posts) == (None, None)
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None and "followers" not in snap and "posts" not in snap


def test_an_incomplete_read_with_no_prior_has_no_interactions_at_all() -> None:
    read = run(Pages([reply({}, status=500)]), acct=None, account_id=ME)
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26), prior=None)
    assert snap is not None and "interactions" not in snap
    empty = masto.build_snapshot(read, as_of=date(2026, 11, 26), prior=[])
    assert empty is not None and "interactions" not in empty  # never fresh zeros


def test_an_empty_account_is_a_real_zero_only_when_the_read_finished() -> None:
    done = run(Pages([[]]))
    snap = masto.build_snapshot(done, as_of=date(2026, 11, 26))
    assert snap is not None and snap["complete"] is True
    assert snap["interactions"] == []
    assert masto.history_aggregates(done) == {
        "posts_30d": 0,
        "boosts_30d": 0,
        "favorites_30d": 0,
        "replies_30d": 0,
    }
    broken = run(Pages([reply({}, status=500)]))
    assert masto.history_aggregates(broken) is None


# --- rate-limit headers --------------------------------------------------------


def test_a_low_rate_limit_stops_the_walk_incomplete() -> None:
    p = Pages(
        [[status(5, day(1))], [status(4, day(1))]],
        page_headers={0: {"x-ratelimit-remaining": "4"}},
    )
    read = run(p)
    assert read.reason == "rate_limited" and p.served == 1  # no second page asked


def test_enough_remaining_or_no_header_is_fine() -> None:
    for value in ("5", "299", "0000000010", None):
        headers = {"x-ratelimit-remaining": value} if value else {}
        p = Pages([[status(5, day(1))], []], page_headers={0: headers, 1: headers})
        read = run(p)
        assert read.complete, value


@pytest.mark.parametrize(
    "value",
    [
        "9" * 500,  # the longest value the fetch layer lets through
        "9" * 11,
        "-3",
        "-0",
        "+5",
        "5.0",
        "1e3",
        "",
        " 7",
        "٥",  # an Arabic-Indic digit: str.isdigit is true, ASCII is not
        "lots",
    ],
)
def test_a_malformed_rate_limit_header_ends_the_read_and_never_raises(
    value: str,
) -> None:
    p = Pages(
        [[status(5, day(1))], []],
        page_headers={0: {"x-ratelimit-remaining": value}},
    )
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert p.served == 1  # nothing after the bad header was asked


def test_the_header_parser_itself_never_raises_on_a_huge_digit_string() -> None:
    # Values over 512 characters are refused before the parser runs, so this guards
    # the parser on its own: it refuses by length before any integer conversion.
    huge = {"x-ratelimit-remaining": "9" * 100_000}
    with pytest.raises(masto._Stop) as caught:
        masto._remaining(huge)
    assert caught.value.reason == "bad_shape"
    assert masto._remaining({}) is None
    assert masto._remaining({"x-ratelimit-remaining": "0"}) == 0


@pytest.mark.parametrize("size", [513, 100_000])
def test_an_over_long_rate_limit_header_is_bad_shape(size: int) -> None:
    # #938: an oversized value is no longer read as absent; it fails closed like
    # any other malformed value (more than ten characters).
    p = Pages(
        [[status(5, day(1))], []],
        page_headers={0: {"x-ratelimit-remaining": "9" * size}},
    )
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert p.served == 1  # nothing after the bad header was asked


def test_an_over_long_rate_limit_header_on_the_lookup_is_bad_shape() -> None:
    p = Pages([[status(5, day(1))], []], look=_rated("9" * 600, lookup()))
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert len(p.transport.calls) == 1


def test_an_over_long_rate_limit_header_on_an_error_answer_is_bad_shape() -> None:
    bad = reply({}, status=503, headers={"x-ratelimit-remaining": "9" * 600})
    read = run(Pages([[]], look=bad))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


@pytest.mark.parametrize("value", ["0", "4"])
def test_a_low_rate_header_on_an_error_answer_stops_the_retry(value: str) -> None:
    # #938: the low count seen on a 503 must prevent the retry of that request.
    p = Pages([[]])
    answers = iter(
        [
            reply({}, status=503, headers={"x-ratelimit-remaining": value}),
            reply([], headers=DATE_OK),
        ]
    )
    p.transport.routes[STATUSES] = lambda r: next(answers)
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "rate_limited"
    assert len(p.transport.calls) == 2  # the lookup and one status try, no retry


def test_a_low_rate_header_on_a_429_of_the_lookup_stops_the_retry() -> None:
    answers = iter(
        [
            reply({}, status=429, headers={"x-ratelimit-remaining": "1"}),
            reply(lookup(), headers=DATE_OK),
        ]
    )
    p = Pages([[]])
    p.transport.routes[LOOKUP] = lambda r: next(answers)
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "rate_limited"
    assert len(p.transport.calls) == 1


def test_a_healthy_rate_header_on_an_error_answer_still_retries() -> None:
    p = Pages([[]])
    answers = iter(
        [
            reply({}, status=503, headers={"x-ratelimit-remaining": "50"}),
            reply([], headers=DATE_OK),
        ]
    )
    p.transport.routes[STATUSES] = lambda r: next(answers)
    assert run(p).complete


def test_a_low_rate_limit_on_the_last_empty_page_does_not_spoil_a_finished_read() -> (
    None
):
    p = Pages(
        [[status(5, day(1))], []], page_headers={1: {"x-ratelimit-remaining": "0"}}
    )
    assert run(p).complete


def _rated(value: str, payload: Any) -> Any:
    return reply(payload, headers={**DATE_OK, "x-ratelimit-remaining": value})


@pytest.mark.parametrize("value", ["garbage", "-1", "", "9" * 11, "٥"])
def test_a_malformed_rate_header_on_the_lookup_is_bad_shape(value: str) -> None:
    p = Pages([[status(5, day(1))], []], look=_rated(value, lookup()))
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert len(p.transport.calls) == 1  # no status page was asked for


@pytest.mark.parametrize("value", ["0", "4"])
def test_a_low_rate_header_on_the_lookup_stops_before_the_next_request(
    value: str,
) -> None:
    p = Pages([[status(5, day(1))], []], look=_rated(value, lookup()))
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "rate_limited"
    assert len(p.transport.calls) == 1


def test_a_malformed_rate_header_on_the_last_empty_page_is_bad_shape() -> None:
    p = Pages(
        [[status(5, day(1))], []],
        page_headers={1: {"x-ratelimit-remaining": "garbage"}},
    )
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


@pytest.mark.parametrize("code", [429, 500, 503])
def test_a_malformed_rate_header_on_an_error_answer_is_bad_shape(code: int) -> None:
    bad = reply({}, status=code, headers={"x-ratelimit-remaining": "garbage"})
    read = run(Pages([[]], look=bad))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    later = run(Pages([[status(5, day(1))], bad]))
    assert isinstance(later.rows, NotProvided) and later.reason == "bad_shape"


def test_a_malformed_rate_header_on_a_retried_answer_is_bad_shape() -> None:
    p = Pages([[]])
    answers = iter(
        [
            reply({}, status=503, headers={"x-ratelimit-remaining": "garbage"}),
            reply([], headers=DATE_OK),
        ]
    )
    p.transport.routes[STATUSES] = lambda r: next(answers)
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


# --- one canonical account id everywhere ---------------------------------------

_ODD_IDS: list[Any] = [
    "01000",
    "0001000",
    " 1000",
    "1000 ",
    "1000\n",
    "١٠٠٠",
    1000,
    None,
]


@pytest.mark.parametrize("odd", ["00", "01", "01000", "٧", "7\n", " 7", "+7", "7.0"])
def test_a_noncanonical_configured_account_id_is_refused(odd: str) -> None:
    with pytest.raises(ValueError):
        masto.check_account_id(odd)
    t = FakeTransport()
    with pytest.raises(ValueError):
        masto.collect(HOST, None, account_id=odd, now=NOW, transport=t)
    assert t.calls == []


@pytest.mark.parametrize("odd", _ODD_IDS)
def test_a_noncanonical_lookup_id_is_bad_shape_never_a_complete_read(odd: Any) -> None:
    p = Pages([[status(5, day(1))], []], look=lookup(id=odd))
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert len(p.transport.calls) == 1


@pytest.mark.parametrize("odd", _ODD_IDS)
def test_a_noncanonical_status_author_is_bad_shape_never_a_zero(odd: Any) -> None:
    for author in (ME, OTHER):
        item = status(5, day(1), author=author)
        item["account"]["id"] = odd
        read = run(Pages([[item], []]))
        assert isinstance(read.rows, NotProvided), (author, odd)
        assert read.reason == "bad_shape"
        snap = masto.build_snapshot(read, as_of=date(2026, 11, 26))
        assert snap is not None and snap["complete"] is False


@pytest.mark.parametrize("odd", _ODD_IDS[:-1])  # None is the no-reply marker
def test_a_noncanonical_reply_target_account_is_bad_shape(odd: Any) -> None:
    item = status(5, day(1), in_reply_to_id="4", in_reply_to_account_id=odd)
    read = run(Pages([[item], []]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


def test_a_padded_author_id_cannot_hide_an_own_status_as_a_complete_zero() -> None:
    item = status(5, day(1), favs=9, author="01000")
    read = run(Pages([[item], []]), acct=None, account_id=ME)
    assert not read.complete and read.reason == "bad_shape"


def test_canonical_ids_still_count_an_own_status_and_skip_a_foreign_one() -> None:
    p = Pages([[status(5, day(1), favs=9), status(4, day(1), author=OTHER)], []])
    assert rows_of(run(p)) == [(0, 9, 0)]
    assert run(Pages([[status(3, day(1), author="0")], []])).complete  # "0" is fine


# --- Link header and cursors are never followed --------------------------------


@pytest.mark.parametrize(
    "link",
    [
        '<https://evil.example/api/v1/accounts/1000/statuses?max_id=1>; rel="next"',
        f'<https://{HOST}/other/path?max_id=1>; rel="next"',
        '<http://social.example/api/v1/accounts/1000/statuses?max_id=1>; rel="next"',
        '<javascript:alert(1)>; rel="next"',
        '<https://192.0.2.1/x>; rel="next"',
        "<" + "a" * 100_000 + '>; rel="next"',
        "garbage",
    ],
)
def test_a_link_header_is_never_followed(link: str) -> None:
    p = Pages(
        [[status(5, day(1))], []],
        page_headers={0: {"link": link}, 1: {"link": link}},
    )
    read = run(p)
    assert read.complete
    assert {c.host for c in p.transport.calls} == {HOST}
    paths = [urlsplit(u).path for u in p.transport.urls]
    assert paths == [
        "/api/v1/accounts/lookup",
        f"/api/v1/accounts/{ME}/statuses",
        f"/api/v1/accounts/{ME}/statuses",
    ]
    assert parse_qs(urlsplit(p.transport.urls[2]).query)["max_id"] == ["5"]


def test_a_next_url_inside_a_body_is_never_followed() -> None:
    page = [
        status(5, day(1), url="https://evil.example/@x", uri="https://evil.example/5")
    ]
    p = Pages([page, []])
    assert run(p).complete and {c.host for c in p.transport.calls} == {HOST}


# --- clock skew ----------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"date": "garbage"},
        {"date": "Thu, 26 Nov 2026 12:11:00 GMT"},
        {"date": "Thu, 26 Nov 2026 11:49:00 GMT"},
    ],
)
def test_a_skewed_or_missing_server_date_is_clock_skew(headers: dict[str, str]) -> None:
    read = run(Pages([[status(5, day(1))], []], headers=headers))
    assert isinstance(read.rows, NotProvided) and read.reason == "clock_skew"
    assert masto.history_aggregates(read) is None


def test_a_small_clock_difference_is_fine() -> None:
    ok = {"date": "Thu, 26 Nov 2026 12:05:00 GMT"}
    assert run(Pages([[status(5, day(1))], []], headers=ok)).complete


# --- host and account validation -----------------------------------------------


@pytest.mark.parametrize(
    "good",
    ["social.example", "mastodon.social", "a-b.example.co.uk", "xn--bcher-kva.example"],
)
def test_good_hosts_pass(good: str) -> None:
    assert masto.check_host(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        5,
        b"social.example",
        "Social.Example",
        "social.example.",
        "bücher.example",
        "https://social.example",
        "social.example:443",
        "social.example:8080",
        "user@social.example",
        "social.example/path",
        "social.example?x=1",
        "social.example#f",
        "social .example",
        "social.example\n",
        "localhost",
        "intranet",
        "host.local",
        "host.internal",
        "127.0.0.1",
        "10.0.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "2130706433",
        "0x7f.0.0.1",
        "127.1",
        "[::1]",
        "::1",
        "a" * 64 + ".example",
        "-a.example",
        "a..example",
        "evil.example%2f",
        "a" * 300,
    ],
)
def test_bad_hosts_are_refused_and_make_no_call(bad: Any) -> None:
    with pytest.raises(ValueError):
        masto.check_host(bad)
    t = FakeTransport()
    with pytest.raises(ValueError):
        masto.collect(bad, ACCT, now=NOW, transport=t)
    assert t.calls == []


@pytest.mark.parametrize("good", ["alice", "a", "Alice_1", "a.b-c", "x" * 30, "_x"])
def test_good_accounts_pass(good: str) -> None:
    assert masto.check_acct(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        None,
        5,
        "@alice",
        "alice@social.example",
        "alice bob",
        "alice\n",
        "alice/../x",
        "alice?x=1",
        "alice#x",
        "alice%2e",
        ".alice",
        "alice.",
        "-alice",
        "alice-",
        "aliçe",
        "ａlice",
        "x" * 31,
        "alice:1",
    ],
)
def test_bad_accounts_are_refused_and_make_no_call(bad: Any) -> None:
    with pytest.raises(ValueError):
        masto.check_acct(bad)
    t = FakeTransport()
    with pytest.raises(ValueError):
        masto.collect(HOST, bad, now=NOW, transport=t)
    assert t.calls == []


@pytest.mark.parametrize(
    "bad", ["", "abc", "12a", "-1", " 1", "1" * 21, True, None, 1.5, -1]
)
def test_bad_account_ids_are_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        masto.check_account_id(bad)
    t = FakeTransport()
    with pytest.raises(ValueError):
        masto.collect(HOST, None, account_id=bad, now=NOW, transport=t)
    assert t.calls == []


def test_a_naive_run_instant_is_refused() -> None:
    with pytest.raises(ValueError):
        masto.collect(HOST, ACCT, now=datetime(2026, 11, 26), transport=FakeTransport())


def test_an_account_name_is_percent_encoded_in_the_lookup() -> None:
    p = Pages([[]])
    run(p, acct="a.b-c_1")
    assert p.transport.urls[0].endswith("?acct=a.b-c_1")


def test_a_redirect_off_the_host_is_refused() -> None:
    off = reply({}, status=302, headers={"location": "https://evil.example/x"})
    read = run(Pages([[]], look=off))
    assert read.reason == "bad_shape"  # the fetch layer's own word: redirect


def test_egress_refusals_keep_their_closed_egress_word() -> None:
    from reach_around.net.egress import EgressBlocked

    def blocked(request: Any) -> Any:
        raise EgressBlocked("non_public_address", HOST)

    read = masto.collect(
        HOST, ACCT, now=NOW, transport=blocked, sleep=lambda s: None, jitter=lambda: 0.0
    )
    assert read.reason == "egress_non_public_address"


# --- the snapshot, guard and kit entry ------------------------------------------

_ROWS = [{"boosts": 1, "favorites": 2, "replies": 3}]
GONE: Any = object()


def _snap(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "platform": "mastodon",
        "as_of": "2026-11-26",
        "complete": True,
        "interactions": [],
    }
    base.update(fields)
    return {k: v for k, v in base.items() if v is not GONE}


def _nest(leaf: Any, depth: int = 5) -> Any:
    value = leaf
    for level in range(depth):
        value = {"k": value} if level % 2 else [value]
    return value


@pytest.mark.parametrize(
    "leaf",
    [
        "https://social.example/@alice",
        "social.example",
        "alice@social.example",
        "@alice",
        "a long piece of free text that is not a fixed reason word at all",
        "1.2.3.4",
        1.5,
        None,
        b"bytes",
    ],
)
@pytest.mark.parametrize("key", ["followers", "posts", "interactions", "reason"])
def test_hostile_values_nested_five_deep_are_refused(key: str, leaf: Any) -> None:
    masto.check_snapshot(_snap())  # the control
    with pytest.raises(ImportGuardError):
        masto.check_snapshot({**_snap(), key: _nest(leaf)})


def test_hostile_keys_containers_and_depth_are_refused() -> None:
    for hostile in ({"handle": 1}, {"zzz": 1}, {"ok": {"deeper": [{"email": 1}]}}):
        with pytest.raises(ImportGuardError):
            masto.check_snapshot(_snap(followers=_nest(hostile)))
    with pytest.raises(ImportGuardError):
        masto.check_snapshot(
            _snap(interactions=[{"boosts": 1, "favorites": 2, "replies": (3,)}])
        )
    with pytest.raises(ImportGuardError):
        masto.check_snapshot(_snap(followers=_nest(1, depth=40)))
    for bad in (True, -1, 10**13, "9"):
        with pytest.raises(ImportGuardError):
            masto.check_snapshot(_snap(followers=bad))
    with pytest.raises(ImportGuardError):
        masto.check_snapshot(_snap(reason="not_a_reason_word"))
    with pytest.raises(ImportGuardError):
        masto.check_snapshot(_snap(platform="bluesky"))
    with pytest.raises(ImportGuardError):
        masto.check_snapshot(_snap(as_of="26/11/2026"))


LEGAL = [
    _snap(),
    _snap(interactions=_ROWS),
    _snap(complete=False, reason="partial_pages", interactions=GONE),
    _snap(complete=False, reason="page_cap", interactions=_ROWS),
    _snap(complete=False, reason="egress_timeout", interactions=_ROWS),
    _snap(complete=False, reason="account_moved", interactions=GONE),
]
ILLEGAL = {
    "complete with a reason": _snap(reason="partial_pages"),
    "complete with a reason and rows": _snap(reason="timeout", interactions=_ROWS),
    "complete without interactions": _snap(interactions=GONE),
    "incomplete without a reason": _snap(complete=False, interactions=_ROWS),
    "incomplete without a reason or rows": _snap(complete=False, interactions=GONE),
    "incomplete with an empty list (fresh zeros)": _snap(
        complete=False, reason="timeout", interactions=[]
    ),
    "incomplete with a word off the list": _snap(
        complete=False, reason="whatever", interactions=_ROWS
    ),
    "complete with a non-bool flag": _snap(complete=1),
    "complete with None interactions": _snap(interactions=None),
    "more than 200 rows": _snap(interactions=_ROWS * 201),
}


@pytest.mark.parametrize("snapshot", LEGAL)
def test_every_legal_state_combination_passes(snapshot: dict[str, Any]) -> None:
    masto.check_snapshot(snapshot)


@pytest.mark.parametrize("name", list(ILLEGAL))
def test_every_illegal_state_combination_is_refused_everywhere(name: str) -> None:
    snapshot = ILLEGAL[name]
    with pytest.raises(ImportGuardError):
        masto.check_snapshot(snapshot)
    assert masto.entry_from_snapshot(snapshot, host=HOST, acct=ACCT) is None
    assert masto.snapshot_aggregates(snapshot) is None


def test_built_snapshots_always_satisfy_the_table() -> None:
    broken = run(Pages([reply({}, status=500)]))
    kept = masto.build_snapshot(
        broken, as_of=date(2026, 11, 26), prior=[InteractionRow(1, 2, 3)]
    )
    bare = masto.build_snapshot(broken, as_of=date(2026, 11, 26), prior=[])
    fresh = masto.build_snapshot(run(Pages([[]])), as_of=date(2026, 11, 26))
    for snap in (kept, bare, fresh):
        assert snap is not None
        masto.check_snapshot(snap)
    assert bare is not None and "interactions" not in bare
    assert fresh is not None and fresh["interactions"] == []


def test_snapshot_aggregates_sum_only_a_complete_consistent_snapshot() -> None:
    assert masto.snapshot_aggregates(_snap(interactions=_ROWS)) == {
        "posts_30d": 1,
        "boosts_30d": 1,
        "favorites_30d": 2,
        "replies_30d": 3,
    }
    stale = _snap(complete=False, reason="timeout", interactions=_ROWS)
    assert masto.snapshot_aggregates(stale) is None


def test_history_aggregates_are_window_sums_and_refuse_a_bad_row_list() -> None:
    good = run(Pages([[status(5, day(1), boosts=1, favs=2, replies=3)], []]))
    assert masto.history_aggregates(good) == {
        "posts_30d": 1,
        "boosts_30d": 1,
        "favorites_30d": 2,
        "replies_30d": 3,
    }
    too_many = masto.MastodonRead(1, 1, (InteractionRow(1, 1, 1),) * 201, 3)
    assert masto.history_aggregates(too_many) is None


def test_more_than_two_hundred_posts_is_window_truncated_and_keeps_prior() -> None:
    pages = [
        [status(1000 - (40 * i + j), day(1)) for j in range(40)] for i in range(6)
    ] + [[]]
    read = run(Pages(pages))
    assert read.complete and len(rows_of(read)) == 240
    prior = [InteractionRow(1, 2, 3)]
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None and snap["complete"] is False
    assert snap["reason"] == "window_truncated"
    assert snap["interactions"] == [{"boosts": 1, "favorites": 2, "replies": 3}]
    assert masto.history_aggregates(read) is None  # no sum over a truncated window


def test_a_complete_snapshot_feeds_the_existing_kit_block() -> None:
    read = run(Pages([[status(5, day(1), boosts=1, favs=2, replies=3)], []]))
    snap = masto.build_snapshot(read, as_of=masto.utc_day(NOW))
    assert snap is not None
    entry = masto.entry_from_snapshot(snap, host=HOST, acct=ACCT)
    assert entry is not None and entry.platform == "mastodon"
    assert entry.profile_url == f"https://{HOST}/@{ACCT}"
    assert (entry.followers, entry.posts) == (321, 77)
    assert [(r.boosts, r.favorites, r.replies) for r in entry.interactions] == [
        (1, 2, 3)
    ]
    measures = {m.key: m.value for m in fediverse_measures([entry])}
    assert measures["median_favorites_30d"] == 2
    assert measures["median_boosts_30d"] == 1
    assert measures["median_replies_30d"] == 3
    stale = masto.build_snapshot(
        run(Pages([reply({}, status=500)])), as_of=masto.utc_day(NOW), prior=[]
    )
    assert stale is not None
    assert masto.entry_from_snapshot(stale, host=HOST, acct=ACCT) is None


def test_the_profile_link_is_built_from_checked_config_only() -> None:
    assert masto.profile_url(HOST, ACCT) == "https://social.example/@alice"
    with pytest.raises(ValueError):
        masto.profile_url("127.0.0.1", ACCT)
    with pytest.raises(ValueError):
        masto.profile_url(HOST, "alice@evil.example")


def _strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k), *_strings(v)]]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def test_no_stored_structure_holds_the_host_account_or_anything_identifying() -> None:
    read = run(Pages([[status(5, day(1), boosts=1, favs=2, replies=3)], []]))
    snap = masto.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None
    stored = [snap, masto.history_aggregates(read), read.rows]
    text = " ".join(" ".join(_strings(s)) for s in stored if isinstance(s, dict))
    text += repr(read)
    for needle in (HOST, ACCT, "alice", "example", "friend", "1000", "192.0.2"):
        assert needle not in text, needle
    assert set(snap) <= {
        "platform",
        "as_of",
        "followers",
        "posts",
        "interactions",
        "complete",
        "reason",
    }


def test_every_reason_word_the_provider_can_return_is_on_the_closed_list() -> None:
    from reach_around.counts.fediverse_rows import is_reason

    words = {
        run(Pages([[]], look=reply({}, status=401))).reason,
        run(Pages([[]], look=reply({}, status=404))).reason,
        run(Pages([[]], look=reply({}, status=410))).reason,
        run(Pages([[]], look=reply({}, status=500))).reason,
        run(Pages([[]], look=lookup(moved={"id": "1"}))).reason,
        run(Pages([[]], look=lookup(suspended=True))).reason,
        run(Pages([[status(5, day(1))], reply({}, status=500)])).reason,
        run(Pages([[{"id": "x"}]])).reason,
        run(Pages([[]], headers={})).reason,
        run(Pages([[status(1000 - i, day(1))] for i in range(30)])).reason,
        run(Pages([[status(9, day(1))], [status(9, day(1))]])).reason,
        run(
            Pages(
                [[status(5, day(1))]], page_headers={0: {"x-ratelimit-remaining": "0"}}
            )
        ).reason,
        _slow([[]], Clock(), [95.0])[0].reason,
    }
    assert None not in words and all(is_reason(w) for w in words)
    assert {
        "needs_token",
        "account_not_found",
        "account_suspended",
        "server_error",
        "account_moved",
        "partial_pages",
        "bad_shape",
        "clock_skew",
        "page_cap",
        "rate_limited",
        "timeout",
    } <= words
