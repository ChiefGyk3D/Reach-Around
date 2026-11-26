# SPDX-License-Identifier: Apache-2.0
"""Slice 2 of the fediverse collector (#922): the Bluesky provider.

Hermetic: a fake transport serves recorded-style fixtures; nothing opens a
socket and no live host is called. Handles and ids are synthetic."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from fakenet import FakeTransport, reply
from reach_around.config.fediverse import fediverse_measures
from reach_around.config.import_guard import ImportGuardError
from reach_around.counts import bluesky_feed as bsky
from reach_around.counts.fediverse_rows import InteractionRow, NotProvided
from reach_around.net.fetch import FetchError, fetch_json_view

NOW = datetime(2026, 11, 26, 12, 0, 0, tzinfo=UTC)
ACTOR = "alice.example.com"
DID = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER = "did:plc:bbbbbbbbbbbbbbbbbbbbbbbb"
DATE_OK = {"date": "Thu, 26 Nov 2026 12:00:00 GMT"}
PROFILE = "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile"
FEED = "https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed"
START = NOW - timedelta(days=30)


def stamp(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def post(
    n: int,
    when: datetime,
    *,
    likes: int = 1,
    reposts: int = 0,
    replies: int = 0,
    did: str = DID,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "post": {
            "uri": f"at://{did}/app.bsky.feed.post/p{n}",
            "author": {"did": did, "handle": "alice.example.com"},
            "record": {"text": "hello @friend", "createdAt": stamp(when)},
            "likeCount": likes,
            "repostCount": reposts,
            "replyCount": replies,
            "indexedAt": stamp(when),
            **extra,
        }
    }


def profile(**over: Any) -> dict[str, Any]:
    base = {"did": DID, "followersCount": 321, "postsCount": 77}
    base.update(over)
    return base


def feed_page(items: list[Any], cursor: str | None = None) -> dict[str, Any]:
    page: dict[str, Any] = {"feed": items}
    if cursor is not None:
        page["cursor"] = cursor
    return page


class Pages:
    """Serves the profile and a list of feed pages, keyed by the cursor."""

    def __init__(
        self,
        pages: list[Any],
        *,
        prof: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.pages = pages
        self.headers = DATE_OK if headers is None else headers
        self.prof = profile() if prof is None else prof
        self.transport = FakeTransport(
            {
                PROFILE: lambda r: self._answer(self.prof),
                FEED: self._feed,
            }
        )

    def _answer(self, payload: Any) -> Any:
        if hasattr(payload, "status"):
            return payload
        return reply(payload, headers=self.headers)

    def _feed(self, request: Any) -> Any:
        query = parse_qs(urlsplit(f"https://x{request.path}").query)
        cursor = query.get("cursor", [None])[0]
        index = 0 if cursor is None else int(cursor[1:])
        return self._answer(self.pages[index])


def run(pages: Pages, **kw: Any) -> bsky.BlueskyRead:
    sleeps: list[float] = kw.pop("sleeps", [])
    return bsky.collect(
        "alice.example.com",
        now=NOW,
        transport=pages.transport,
        sleep=sleeps.append,
        jitter=lambda: 0.0,
        **kw,
    )


def rows_of(read: bsky.BlueskyRead) -> list[tuple[int, int, int]]:
    assert isinstance(read.rows, tuple)
    return [(r.boosts, r.favorites, r.replies) for r in read.rows]


# --- the happy path and the endpoints ------------------------------------------


def test_two_pages_become_sorted_rows_and_the_endpoints_are_exact() -> None:
    p = Pages(
        [
            feed_page([post(1, NOW - timedelta(days=1), likes=9, reposts=2)], "c1"),
            feed_page([post(2, NOW - timedelta(days=2), likes=3, replies=4)]),
        ]
    )
    read = run(p)
    assert read.complete and read.reason is None
    assert (read.followers, read.posts, read.calls) == (321, 77, 3)
    assert rows_of(read) == [(0, 3, 4), (2, 9, 0)]  # sorted by value, no timeline
    urls = p.transport.urls
    assert {c.host for c in p.transport.calls} == {"public.api.bsky.app"}
    assert urls[0] == f"{PROFILE}?actor=alice.example.com"
    q1 = parse_qs(urlsplit(urls[1]).query)
    assert q1 == {
        "actor": [DID],
        "limit": ["50"],
        "filter": ["posts_no_replies"],
    }
    assert parse_qs(urlsplit(urls[2]).query)["cursor"] == ["c1"]
    assert all(c.method == "GET" and c.body is None for c in p.transport.calls)


def test_a_pause_sits_between_calls_and_none_before_the_first() -> None:
    p = Pages(
        [feed_page([post(1, NOW - timedelta(days=1))], "c1"), feed_page([])],
    )
    sleeps: list[float] = []
    run(p, sleeps=sleeps)
    assert sleeps == [bsky.PAUSE, bsky.PAUSE]  # three calls, two pauses


# --- the window, by indexedAt ---------------------------------------------------


def test_window_edges_by_indexed_at() -> None:
    items = [
        post(1, NOW + timedelta(minutes=10), likes=1),  # at the skew limit: in
        post(2, NOW + timedelta(minutes=10, seconds=1), likes=2),  # beyond: dropped
        post(3, NOW, likes=3),
        post(4, START, likes=4),  # exactly the start: in
        post(5, START - timedelta(seconds=1), likes=5),  # one second before: out
        post(6, NOW - timedelta(days=1), likes=6),  # after an old one: still in
    ]
    read = run(Pages([feed_page(items)]))
    assert read.complete
    assert sorted(f for _, f, _ in rows_of(read)) == [1, 3, 4, 6]
    assert read.dropped_future == 1


def test_posts_behind_old_pages_are_counted_when_the_cursor_chain_ends() -> None:
    # #922 round 3: the walk never ends early on old posts. An account whose
    # in-window post sits on page 3 behind two old pages (a re-indexed backlog,
    # a pin-like out-of-order feed) is counted completely when the cursor chain
    # ends inside the budget.
    p = Pages(
        [
            feed_page([post(1, START - timedelta(days=1))], "c1"),
            feed_page([post(2, START - timedelta(days=2))], "c2"),
            feed_page([post(3, NOW - timedelta(days=1), likes=9)]),
        ]
    )
    read = run(p)
    assert read.complete and read.calls == 4  # the profile and three pages
    assert rows_of(read) == [(0, 9, 0)]
    assert bsky.history_aggregates(read) == {
        "posts_30d": 1,
        "boosts_30d": 0,
        "favorites_30d": 9,
        "replies_30d": 0,
    }


def test_every_page_is_requested_even_when_all_of_them_are_old() -> None:
    # No order check and no run of old pages shortens the walk: all pages up to
    # the end of the chain are requested, and an all-old chain is a true zero.
    p = Pages(
        [
            feed_page([post(1, START - timedelta(days=1))], "c1"),
            feed_page([post(2, START - timedelta(days=2))], "c2"),
            feed_page([post(3, START - timedelta(days=3))], "c3"),
            feed_page([post(4, START - timedelta(days=4))]),
        ]
    )
    read = run(p)
    assert read.complete and read.rows == () and read.calls == 5
    assert any("cursor=c3" in c.path for c in p.transport.calls)


def test_the_page_budget_ending_first_is_incomplete_never_a_zero() -> None:
    # The same account, but the chain is longer than the budget: the in-window
    # post is never reached, and the read must not look like a finished window.
    pages = [feed_page([post(1, START - timedelta(days=1))], "c1")]
    pages += [
        feed_page([post(i + 1, START - timedelta(days=i + 1))], f"c{i + 1}")
        for i in range(1, 12)
    ]
    pages.append(feed_page([post(99, NOW - timedelta(days=1), likes=9)]))
    read = run(Pages(pages))
    assert isinstance(read.rows, NotProvided) and read.reason == "page_cap"
    assert bsky.history_aggregates(read) is None
    prior = [InteractionRow(1, 2, 3)]
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None and snap["complete"] is False
    assert snap["reason"] == "page_cap" and snap["interactions"] == [
        {"boosts": 1, "favorites": 2, "replies": 3}
    ]


def test_the_deadline_ending_first_is_incomplete_never_a_zero() -> None:
    # Two old pages, then a cursor to an in-window page the time budget never
    # reaches (20 s per request on the fake clock).
    pages = [
        feed_page([post(1, START - timedelta(days=1))], "c1"),
        feed_page([post(2, START - timedelta(days=2))], "c2"),
        feed_page([post(3, START - timedelta(days=3))], "c3"),
        feed_page([post(4, NOW - timedelta(days=1), likes=9)]),
    ]
    read, slow, _ = _collect_slow(pages, Clock(), [20.0])
    assert isinstance(read.rows, NotProvided) and read.reason == "timeout"
    assert len(slow.deadlines) == 5  # the profile and four pages; the last one late
    assert bsky.history_aggregates(read) is None
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None and snap["complete"] is False
    assert "interactions" not in snap


def test_two_old_pages_with_a_next_cursor_never_yield_a_complete_zero() -> None:
    # The round 3 reproduction: two ordered old pages and a cursor that proves
    # an unread page. Nothing is recorded as a verified zero.
    p = Pages(
        [
            feed_page([post(1, START - timedelta(days=1))], "c1"),
            feed_page([post(2, START - timedelta(days=2))], "c2"),
            feed_page([post(3, NOW - timedelta(days=1), likes=9)]),
        ]
    )
    read = run(p)
    assert any("cursor=c2" in c.path for c in p.transport.calls)  # c2 was followed
    assert rows_of(read) == [(0, 9, 0)]  # the page behind the cursor was counted
    # and when that page cannot be read, the read is incomplete, not zero
    broken = Pages(
        [
            feed_page([post(1, START - timedelta(days=1))], "c1"),
            feed_page([post(2, START - timedelta(days=2))], "c2"),
            reply({}, status=500),
        ]
    )
    cut = run(broken)
    assert not cut.complete and cut.reason == "partial_pages"
    assert bsky.history_aggregates(cut) is None


def test_author_supplied_created_at_never_decides_the_window() -> None:
    backdated = post(1, NOW - timedelta(days=1), likes=7)
    backdated["post"]["record"]["createdAt"] = stamp(NOW - timedelta(days=400))
    recent_claim = post(2, START - timedelta(days=2), likes=8)
    recent_claim["post"]["record"]["createdAt"] = stamp(NOW)
    read = run(Pages([feed_page([backdated, recent_claim])]))
    assert rows_of(read) == [(0, 7, 0)]


def test_a_pinned_old_post_neither_counts_nor_ends_the_walk() -> None:
    pin_old = post(9, START - timedelta(days=90), likes=99)
    pin_old["reason"] = {"$type": "app.bsky.feed.defs#reasonPin"}
    pin_new = post(8, NOW - timedelta(days=3), likes=5)
    pin_new["reason"] = {"$type": "app.bsky.feed.defs#reasonPin"}
    items = [pin_old, pin_new, post(1, NOW - timedelta(days=1), likes=2)]
    read = run(Pages([feed_page(items)]))
    assert sorted(f for _, f, _ in rows_of(read)) == [2, 5]


def test_a_post_listed_twice_in_one_run_counts_once() -> None:
    pin = post(1, NOW - timedelta(days=2), likes=4)
    pin["reason"] = {"$type": "app.bsky.feed.defs#reasonPin"}
    again = post(1, NOW - timedelta(days=2), likes=4)
    read = run(Pages([feed_page([pin, again, post(2, NOW - timedelta(days=3))])]))
    assert len(rows_of(read)) == 2


# --- own posts only -------------------------------------------------------------


def test_reposts_other_authors_and_replies_are_never_counted() -> None:
    repost = post(1, NOW - timedelta(days=1), did=OTHER, likes=500)
    repost["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    stranger = post(2, NOW - timedelta(days=1), did=OTHER, likes=600)  # no reason
    reply_item = post(3, NOW - timedelta(days=1), likes=700)
    reply_item["reply"] = {"parent": {}}
    reply_record = post(4, NOW - timedelta(days=1), likes=800)
    reply_record["post"]["record"]["reply"] = {"parent": {"uri": "x"}}
    own = post(6, NOW - timedelta(days=1), likes=3)
    read = run(Pages([feed_page([repost, stranger, reply_item, reply_record, own])]))
    assert rows_of(read) == [(0, 3, 0)]


@pytest.mark.parametrize(
    "reason",
    [
        {"$type": "app.bsky.feed.defs#somethingNew"},  # an unknown type
        {"$type": 5},
        {"x": "no type"},
        {},
        "garbage",
        "app.bsky.feed.defs#reasonRepost",  # right word, wrong shape (not an object)
        None,  # present but null
        7,
        ["app.bsky.feed.defs#reasonPin"],
    ],
)
def test_an_unknown_or_malformed_reason_makes_the_read_incomplete(
    reason: Any,
) -> None:
    # #922 round 3: only a missing reason (a plain post) and the two known
    # reasons are understood. Any other value is never a silent skip of what
    # may be an own post: the read ends bad_shape and keeps the prior rows.
    item = post(1, NOW - timedelta(days=1), likes=900)
    item["reason"] = reason
    read = run(Pages([feed_page([item, post(2, NOW - timedelta(days=2))])]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert bsky.history_aggregates(read) is None
    prior = [InteractionRow(1, 2, 3)]
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None and snap["complete"] is False
    assert snap["interactions"] == [{"boosts": 1, "favorites": 2, "replies": 3}]


def test_the_known_reasons_and_a_missing_reason_still_work() -> None:
    repost = post(1, NOW - timedelta(days=1), did=OTHER, likes=500)
    repost["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    pinned = post(2, NOW - timedelta(days=2), likes=5)
    pinned["reason"] = {"$type": "app.bsky.feed.defs#reasonPin"}
    plain = post(3, NOW - timedelta(days=3), likes=2)  # no reason key at all
    read = run(Pages([feed_page([repost, pinned, plain])]))
    assert read.complete and sorted(f for _, f, _ in rows_of(read)) == [2, 5]


# --- incomplete reads keep prior values and are never zero ----------------------


def test_a_failed_second_page_is_partial_and_keeps_the_prior_rows() -> None:
    p = Pages(
        [feed_page([post(1, NOW - timedelta(days=1))], "c1"), reply({}, status=500)]
    )
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "partial_pages"
    assert (read.followers, read.posts) == (321, 77)  # the profile call was whole
    prior = [InteractionRow(1, 2, 3)]
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None
    assert snap["complete"] is False and snap["reason"] == "partial_pages"
    assert snap["interactions"] == [{"boosts": 1, "favorites": 2, "replies": 3}]
    assert bsky.history_aggregates(read) is None  # nothing written to history
    assert (
        bsky.entry_from_snapshot(snap, actor=ACTOR) is None
    )  # never replaces a typed entry


def test_an_incomplete_read_with_no_prior_has_no_interactions_at_all() -> None:
    read = run(Pages([reply({}, status=500)]))
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None and "interactions" not in snap
    assert snap["complete"] is False


@pytest.mark.parametrize(
    ("response", "word"),
    [
        (reply({"error": "x"}, status=404), "account_not_found"),
        (reply({"error": "x"}, status=400), "account_not_found"),
        (reply({}, status=401), "needs_token"),
        (reply({}, status=403), "needs_token"),
        (reply({}, status=429), "rate_limited"),
        (reply({}, status=503), "server_error"),
        (reply({}, headers={"content-type": "text/html"}), "content_type"),
        (reply(body=b"{not json"), "bad_json"),
        (reply(body=b"[]"), "bad_json"),
        (reply({}, status=418), "bad_shape"),
    ],
)
def test_profile_failures_use_the_fixed_words(response: Any, word: str) -> None:
    p = Pages([feed_page([])], prof=response)
    read = run(p)
    assert read.reason == word
    assert len(p.transport.calls) in (1, 3)  # only the profile; 429 and 5xx retry
    assert all(
        c.path.startswith("/xrpc/app.bsky.actor.getProfile") for c in p.transport.calls
    )
    assert (read.followers, read.posts) == (None, None)


def test_first_page_failure_keeps_its_own_word_later_pages_are_partial() -> None:
    assert run(Pages([reply({}, status=401)])).reason == "needs_token"
    later = Pages([feed_page([post(1, NOW)], "c1"), reply({}, status=429)])
    assert run(later).reason == "rate_limited"
    big = Pages([feed_page([post(1, NOW)], "c1"), reply(body=b" " * 1_100_000)])
    assert run(big).reason == "partial_pages"


def test_a_page_that_is_not_a_feed_is_bad_shape() -> None:
    shapes: list[dict[str, Any]] = [
        {"feed": "nope"},
        {"nofeed": []},
        {"feed": [1]},
        {"feed": [{}]},
    ]
    for page in shapes:
        assert run(Pages([page])).reason == "bad_shape"
    assert run(Pages([{"feed": [{"post": {}}] * 101}])).reason == "bad_shape"


def test_a_profile_without_a_did_is_bad_shape_and_counts_are_optional() -> None:
    assert run(Pages([feed_page([])], prof=profile(did="alice"))).reason == "bad_shape"
    read = run(Pages([feed_page([])], prof=profile(followersCount="9", postsCount=-1)))
    assert read.complete and (read.followers, read.posts) == (None, None)


# --- the budget ------------------------------------------------------------------


def test_the_page_cap_stops_at_thirteen_calls_and_is_incomplete() -> None:
    pages = [
        feed_page([post(i, NOW - timedelta(hours=i))], f"c{i + 1}") for i in range(30)
    ]
    p = Pages(pages)
    read = run(p)
    assert len(p.transport.calls) == 13 == bsky.MAX_CALLS
    assert read.calls == 13 and read.reason == "page_cap"
    assert (read.followers, read.posts) == (321, 77)


def test_a_cursor_that_does_not_move_or_is_odd_stops_the_walk() -> None:
    same = Pages([feed_page([post(1, NOW)], "c0"), feed_page([post(2, NOW)], "c0")])
    # c0 -> page 0 again: the same cursor twice in a row
    assert run(same).reason == "partial_pages"
    for bad in ("../x", "a b", "x" * 200, "c1\n", 5):
        page: dict[str, Any] = {"feed": [], "cursor": bad}
        assert run(Pages([page])).reason == "bad_shape"


def test_the_platform_deadline_ends_the_run_with_timeout() -> None:
    ticks = iter([0.0, 0.0, 200.0, 200.0, 200.0, 200.0])
    p = Pages([feed_page([post(1, NOW)], "c1"), feed_page([])])
    read = bsky.collect(
        "alice.example.com",
        now=NOW,
        transport=p.transport,
        sleep=lambda s: None,
        jitter=lambda: 0.0,
        clock=lambda: next(ticks, 200.0),
    )
    assert read.reason == "timeout" and len(p.transport.calls) < 3


def test_a_link_header_or_next_url_in_a_response_is_never_followed() -> None:
    h = {**DATE_OK, "link": '<https://evil.example/next>; rel="next"'}
    body = feed_page([post(1, NOW)], None)
    body["next"] = "https://evil.example/n"
    p = Pages([body], headers=h)
    run(p)
    assert {c.host for c in p.transport.calls} == {"public.api.bsky.app"}


# --- hostile items ---------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(likeCount="5"),
        lambda p: p.update(likeCount=True),
        lambda p: p.update(likeCount=-1),
        lambda p: p.update(likeCount=1.5),
        lambda p: p.update(likeCount=None),
        lambda p: p.update(likeCount={"n": 1}),
        lambda p: p.update(likeCount=[1]),
        lambda p: p.update(likeCount=10**10),
        lambda p: p.update(repostCount="x"),
        lambda p: p.update(replyCount=-5),
        lambda p: p.pop("replyCount"),
        lambda p: p.update(uri=7),
        lambda p: p.update(uri=""),
        lambda p: p.update(indexedAt="yesterday"),
        lambda p: p.update(indexedAt="2026-11-25T12:00:00"),  # no offset
        lambda p: p.pop("indexedAt"),
    ],
)
def test_a_malformed_counted_post_rejects_the_whole_read(mutate: Any) -> None:
    good = post(1, NOW - timedelta(days=1), likes=4)
    bad = post(2, NOW - timedelta(days=2))
    mutate(bad["post"])
    read = run(Pages([feed_page([good, bad])]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"


def test_a_number_over_eighteen_digits_is_refused_by_the_parser() -> None:
    bad = post(1, NOW)
    bad["post"]["likeCount"] = 10**30
    assert run(Pages([feed_page([bad])])).reason == "bad_json"


def test_hostile_extra_keys_and_text_never_reach_a_row_or_the_snapshot() -> None:
    sentinel = "SENTINEL_TEXT_77"
    item = post(1, NOW - timedelta(days=1), likes=2)
    item["post"]["record"]["text"] = f"{sentinel} bob@example.com 203.0.113.9 @carol"
    item["post"]["author"].update({"displayName": sentinel, "email": "bob@example.com"})
    item["post"]["labels"] = [{"val": sentinel}]
    item["post"]["embed"] = {"nested": {"deep": [sentinel]}}
    item["extra"] = {"likeCount": 10**6}
    read = run(Pages([feed_page([item])]))
    assert rows_of(read) == [(0, 2, 0)]
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26))
    blob = repr(read) + json.dumps(snap)
    for leaked in (sentinel, "bob@", "203.0.113", "carol", "p1", DID, "at://"):
        assert leaked not in blob


def test_a_deleted_post_is_absent_from_the_feed_so_nothing_is_to_be_found() -> None:
    # A deleted post simply is not listed: a clean read counts the others.
    read = run(Pages([feed_page([post(2, NOW - timedelta(days=1), likes=3)])]))
    assert read.complete and rows_of(read) == [(0, 3, 0)]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.pop("author"),  # no author: it could be the account's own
        lambda p: p.update(author=None),
        lambda p: p.update(author="did:plc:aaaaaaaaaaaaaaaaaaaaaaaa"),
        lambda p: p.update(author=[DID]),
        lambda p: p.update(author={}),
        lambda p: p.update(author={"handle": "alice.example.com"}),
        lambda p: p.update(author={"did": None}),
        lambda p: p.update(author={"did": 7}),
        lambda p: p.update(author={"did": "not-a-did"}),
        lambda p: p.update(author={"did": "did:plc:"}),
        lambda p: p.clear(),  # an empty post object
        lambda p: (p.clear(), p.update({"$type": "app.bsky.feed.defs#notFoundPost"})),
    ],
)
def test_a_malformed_or_authorless_entry_is_incomplete_never_absence(
    mutate: Any,
) -> None:
    # #922 round 3: an entry whose author cannot be read could be the account's
    # own, so it is never turned into "no post" (a lower or zero count).
    bad = post(1, NOW - timedelta(days=1), likes=5)
    mutate(bad["post"])
    good = post(2, NOW - timedelta(days=2), likes=4)
    read = run(Pages([feed_page([good, bad])]))
    assert isinstance(read.rows, NotProvided) and read.reason == "bad_shape"
    assert bsky.history_aggregates(read) is None
    prior = [InteractionRow(1, 2, 3)]
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None and snap["complete"] is False
    assert snap["interactions"] == [{"boosts": 1, "favorites": 2, "replies": 3}]


def test_a_foreign_author_with_a_valid_did_is_still_skipped_cleanly() -> None:
    stranger = post(1, NOW - timedelta(days=1), did=OTHER, likes=600)
    read = run(Pages([feed_page([stranger])]))
    assert read.complete and read.rows == ()
    # a well formed did:web author is also a clean skip
    web = post(2, NOW - timedelta(days=1), did="did:web:example.com", likes=7)
    assert run(Pages([feed_page([web])])).complete


# --- empty accounts and zeros ----------------------------------------------------


def test_an_empty_account_is_a_real_zero_only_when_the_read_finished() -> None:
    read = run(Pages([feed_page([])]))
    assert read.complete and read.rows == ()
    assert bsky.history_aggregates(read) == {
        "posts_30d": 0,
        "boosts_30d": 0,
        "favorites_30d": 0,
        "replies_30d": 0,
    }
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26))
    assert snap is not None and snap["interactions"] == [] and snap["complete"] is True
    # the same empty page on a broken run is not a zero
    broken = run(Pages([feed_page([])], headers={}))
    assert isinstance(broken.rows, NotProvided)
    assert bsky.history_aggregates(broken) is None


def test_old_only_posts_are_an_honest_empty_window() -> None:
    read = run(Pages([feed_page([post(1, START - timedelta(days=5))])]))
    assert read.complete and read.rows == ()


def test_history_aggregates_are_window_sums() -> None:
    items = [
        post(1, NOW - timedelta(days=1), likes=10, reposts=1, replies=2),
        post(2, NOW - timedelta(days=2), likes=20, reposts=3, replies=4),
    ]
    agg = bsky.history_aggregates(run(Pages([feed_page(items)])))
    assert agg == {
        "posts_30d": 2,
        "boosts_30d": 4,
        "favorites_30d": 30,
        "replies_30d": 6,
    }


def test_more_than_two_hundred_posts_is_window_truncated_and_keeps_prior() -> None:
    pages = [
        feed_page(
            [post(i * 50 + j, NOW - timedelta(minutes=i * 50 + j)) for j in range(50)],
            f"c{i + 1}",
        )
        for i in range(4)
    ] + [
        feed_page(
            [post(900 + j, NOW - timedelta(hours=1, minutes=j)) for j in range(5)]
        )
    ]
    read = run(Pages(pages))
    assert read.complete and len(rows_of(read)) == 205
    prior = [InteractionRow(0, 0, 0)]
    snap = bsky.build_snapshot(read, as_of=date(2026, 11, 26), prior=prior)
    assert snap is not None
    assert snap["reason"] == "window_truncated" and snap["complete"] is False
    assert snap["interactions"] == [{"boosts": 0, "favorites": 0, "replies": 0}]


# --- clock skew ------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"date": "Thu, 26 Nov 2026 13:00:00 GMT"},  # an hour ahead
        {"date": "Thu, 26 Nov 2026 11:00:00 GMT"},  # an hour behind
        {"date": "garbage"},
        {},  # no Date at all: an untrusted clock
    ],
)
def test_a_skewed_or_missing_server_date_is_clock_skew(headers: dict[str, str]) -> None:
    p = Pages([feed_page([post(1, NOW)])], headers=headers)
    read = run(p)
    assert read.reason == "clock_skew" and len(p.transport.calls) == 1


def test_a_small_clock_difference_is_fine() -> None:
    h = {"date": "Thu, 26 Nov 2026 12:05:00 GMT"}
    assert run(Pages([feed_page([])], headers=h)).complete


# --- actor validation ------------------------------------------------------------


@pytest.mark.parametrize(
    "good",
    [
        "alice.example.com",
        "a-b.example.co",
        "x.y",
        "did:plc:abcdefghijklmnopqrstuvwx",
        "did:web:example.com",
    ],
)
def test_good_actors_pass(good: str) -> None:
    assert bsky.check_actor(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "alice",
        "@alice.example.com",
        "https://alice.example.com",
        "alice.example.com/x",
        "alice.example.com?x=1",
        "alice.example.com#f",
        "alice .example.com",
        "alice.example.com\n",
        "-a.example.com",
        "a-.example.com",
        "a..example.com",
        "alice.example.com.",
        "alice.123",
        "a_b.example.com",
        "é.example.com",
        ("a" * 64) + ".example.com",
        "a." * 130 + "com",
        "did:plc:",
        "did:key:abc",
        "did:plc:a/b",
        "did:plc:" + "a" * 200,
        None,
        7,
        b"alice.example.com",
    ],
)
def test_bad_actors_are_refused(bad: Any) -> None:
    with pytest.raises(ValueError):
        bsky.check_actor(bad)
    with pytest.raises(ValueError):
        bsky.collect(bad, now=NOW, transport=FakeTransport())


def test_a_bad_actor_makes_no_call_and_a_naive_now_is_refused() -> None:
    t = FakeTransport()
    with pytest.raises(ValueError):
        bsky.collect("alice.example.com", now=datetime(2026, 11, 26), transport=t)
    assert t.calls == []


# --- snapshot, guard and the kit block -------------------------------------------


def test_the_guard_refuses_person_level_keys_and_values() -> None:
    base: dict[str, Any] = {
        "platform": "bluesky",
        "as_of": "2026-11-26",
        "complete": True,
        "interactions": [],  # a complete snapshot always carries its rows
    }
    bsky.check_snapshot(base)
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**base, "handle": "x"})
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**base, "extra": 1})
    with pytest.raises(ImportGuardError):  # the link is not part of a snapshot
        bsky.check_snapshot(
            {**base, "profile_url": "https://bsky.app/profile/alice.example.com"}
        )
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**base, "reason": "write bob@example.com"})
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**base, "interactions": [{"boosts": 1, "handle": 2}]})
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**base, "interactions": [{"boosts": "1"}]})
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**base, "interactions": "x"})


def test_a_guard_hit_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    read = run(Pages([feed_page([post(1, NOW)])]))
    monkeypatch.setattr(
        bsky, "keep_prior", lambda rows, prior: (None, "contact bob@example.com")
    )
    assert bsky.build_snapshot(read, as_of=date(2026, 11, 26)) is None


def test_a_complete_snapshot_feeds_the_existing_kit_block() -> None:
    items = [
        post(1, NOW - timedelta(days=1), likes=10, reposts=1, replies=0),
        post(2, NOW - timedelta(days=2), likes=30, reposts=3, replies=2),
        post(3, NOW - timedelta(days=3), likes=20, reposts=2, replies=1),
    ]
    read = run(Pages([feed_page(items)]))
    snap = bsky.build_snapshot(read, as_of=bsky.utc_day(NOW))
    assert snap is not None
    entry = bsky.entry_from_snapshot(snap, actor=ACTOR)
    assert entry is not None and entry.platform == "bluesky"
    assert entry.profile_url == "https://bsky.app/profile/alice.example.com"
    assert (entry.followers, entry.posts) == (321, 77)
    measures = {m.key: m.value for m in fediverse_measures([entry])}
    assert measures["median_favorites_30d"] == 20
    assert measures["median_boosts_30d"] == 2
    assert measures["median_replies_30d"] == 1


# --- Daybreak round 1 fixes (#922) --------------------------------------------------


def _strings(value: Any) -> list[str]:
    """Every key and string leaf of a nested structure (and its repr's text)."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            found.append(str(key))
            found.extend(_strings(inner))
    elif isinstance(value, (list, tuple)):
        for inner in value:
            found.extend(_strings(inner))
    elif isinstance(value, str):
        found.append(value)
    return found


@pytest.mark.parametrize("actor", [ACTOR, DID])
def test_no_stored_structure_holds_the_actor_or_anything_identifying(
    actor: str,
) -> None:
    p = Pages([feed_page([post(1, NOW - timedelta(days=1), likes=3)])])
    read = bsky.collect(
        actor, now=NOW, transport=p.transport, sleep=lambda s: None, jitter=lambda: 0.0
    )
    assert read.complete
    prior = [InteractionRow(1, 2, 3)]
    complete = bsky.build_snapshot(read, as_of=date(2026, 11, 26))
    partial = bsky.build_snapshot(
        run(Pages([reply({}, status=500)])), as_of=date(2026, 11, 26), prior=prior
    )
    aggregates = bsky.history_aggregates(read)
    assert complete is not None and partial is not None and aggregates is not None
    for stored in (complete, partial, aggregates, [vars(read)]):
        for text in _strings(stored):
            for needle in (
                actor,
                DID,
                "alice",
                "example.com",
                "bsky.app",
                "at://",
                "did:",
                "http",
                "hello",
                "p1",
            ):
                assert needle not in text, (needle, text)
    for stored in (complete, partial, aggregates):
        for value in _walk_numbers(stored):
            assert type(value) is int
    assert "profile_url" not in complete and "actor" not in complete
    assert "actor" not in bsky.build_snapshot.__annotations__


def _walk_numbers(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [n for inner in value.values() for n in _walk_numbers(inner)]
    if isinstance(value, (list, tuple)):
        return [n for inner in value for n in _walk_numbers(inner)]
    if isinstance(value, (bool, str)) or value is None:
        return []
    return [value]


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
    flaky = Flaky([feed_page([post(1, NOW - timedelta(days=1))])])
    read = bsky.collect(
        ACTOR, now=NOW, transport=flaky, sleep=lambda s: None, jitter=lambda: 0.0
    )
    assert read.complete and len(rows_of(read)) == 1
    assert flaky.requests == 4 == read.calls  # two operations, one retry each


def test_retries_draw_on_the_same_thirteen_request_budget() -> None:
    pages = [
        feed_page([post(i, NOW - timedelta(hours=i))], f"c{i + 1}") for i in range(30)
    ]
    flaky = Flaky(pages)
    read = bsky.collect(
        ACTOR, now=NOW, transport=flaky, sleep=lambda s: None, jitter=lambda: 0.0
    )
    assert flaky.requests == 13 == bsky.MAX_CALLS  # never 13 operations x 2 tries
    assert read.calls == 13
    assert isinstance(read.rows, NotProvided) and read.reason == "page_cap"


class Clock:
    """A fake monotonic clock; its sleep only moves the time."""

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


def _collect_slow(
    pages: list[Any], clock: Clock, costs: list[float]
) -> tuple[bsky.BlueskyRead, Slow, list[float]]:
    slow = Slow(Pages(pages).transport, clock, costs)
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.sleep(seconds)

    read = bsky.collect(
        ACTOR, now=NOW, transport=slow, sleep=sleep, jitter=lambda: 0.0, clock=clock
    )
    return read, slow, sleeps


def test_one_absolute_deadline_caps_every_request_and_the_pauses() -> None:
    pages = [
        feed_page([post(i, NOW - timedelta(hours=i))], f"c{i + 1}") for i in range(30)
    ]
    clock = Clock()
    read, slow, _ = _collect_slow(pages, clock, [40.0])
    assert read.reason == "timeout"
    assert all(d <= bsky.PLATFORM_DEADLINE for d in slow.deadlines)
    # the third request started at 82 s with 8 s left: its deadline is the run's
    assert slow.deadlines[2] == bsky.PLATFORM_DEADLINE
    assert len(slow.deadlines) == 3  # no fourth request after the deadline


def test_a_pause_that_would_cross_the_deadline_is_not_taken() -> None:
    clock = Clock()
    read, slow, sleeps = _collect_slow(
        [feed_page([post(1, NOW)], "c1"), feed_page([])], clock, [89.5]
    )
    assert read.reason == "timeout" and len(slow.deadlines) == 1
    assert sleeps == []  # the one-second pause would have ended at 90.5 s


def test_a_retry_wait_that_would_cross_the_deadline_ends_the_run() -> None:
    clock = Clock()
    slow = Slow(lambda r: reply({}, status=503), clock, [89.5])
    read = bsky.collect(
        ACTOR,
        now=NOW,
        transport=slow,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        clock=clock,
    )
    assert read.reason == "timeout" and len(slow.deadlines) == 1  # no second try


def test_an_old_item_in_the_middle_does_not_end_the_walk() -> None:
    items = [
        post(1, NOW - timedelta(days=1), likes=1),
        post(2, START - timedelta(days=3), likes=50),  # out of order
        post(3, NOW - timedelta(days=2), likes=2),
    ]
    read = run(Pages([feed_page(items)]))
    assert read.complete and sorted(f for _, f, _ in rows_of(read)) == [1, 2]


def test_an_old_item_first_then_recent_ones_on_the_next_page_still_count() -> None:
    pin = post(9, START - timedelta(days=90), likes=99)  # not marked as a pin
    p = Pages(
        [
            feed_page([pin, post(1, NOW - timedelta(days=1), likes=4)], "c1"),
            feed_page([post(2, NOW - timedelta(days=2), likes=6)]),
        ]
    )
    read = run(p)
    assert read.complete and sorted(f for _, f, _ in rows_of(read)) == [4, 6]


def test_a_budget_stop_while_old_items_keep_coming_is_incomplete() -> None:
    pages = [
        feed_page(
            [
                post(i, NOW - timedelta(hours=i)),
                post(500 + i, START - timedelta(days=2)),
            ],
            f"c{i + 1}",
        )
        for i in range(30)
    ]
    read = run(Pages(pages))
    assert isinstance(read.rows, NotProvided) and read.reason == "page_cap"
    assert bsky.history_aggregates(read) is None  # never a complete undercount


def test_a_page_of_reposts_with_a_cursor_is_not_the_end_of_the_window() -> None:
    repost = post(7, NOW - timedelta(days=1))
    repost["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    p = Pages(
        [feed_page([repost], "c1"), feed_page([post(1, NOW - timedelta(days=1))])]
    )
    assert len(rows_of(run(p))) == 1


def test_a_cursor_seen_earlier_stops_the_walk_not_only_a_repeat() -> None:
    pages = [
        feed_page([post(1, NOW - timedelta(hours=1))], "c1"),
        feed_page([post(2, NOW - timedelta(hours=2))], "c2"),
        feed_page([post(3, NOW - timedelta(hours=3))], "c1"),  # A, B, A
        feed_page([post(4, NOW - timedelta(hours=4))]),
    ]
    p = Pages(pages)
    read = run(p)
    assert isinstance(read.rows, NotProvided) and read.reason == "partial_pages"
    assert len(p.transport.calls) == 4  # the profile and three pages, no more


def _nest(leaf: Any, depth: int = 5) -> Any:
    value = leaf
    for level in range(depth):
        value = {"k": value} if level % 2 else [value]
    return value


_BASE: dict[str, Any] = {
    "platform": "bluesky",
    "as_of": "2026-11-26",
    "complete": True,
    "interactions": [],  # a complete snapshot always carries its rows
}


@pytest.mark.parametrize(
    "leaf",
    [
        "at://did:plc:aaaaaaaaaaaaaaaaaaaaaaaa/app.bsky.feed.post/p1",
        "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa",
        "alice.example.com",
        "https://bsky.app/profile/alice.example.com",
        "bafyreigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
        "a long piece of free text that is not a fixed reason word at all",
        "bob@example.com",
        "@carol",
        1.5,
        None,
        b"bytes",
    ],
)
@pytest.mark.parametrize("key", ["followers", "posts", "interactions", "reason"])
def test_hostile_values_nested_five_deep_are_refused(key: str, leaf: Any) -> None:
    bsky.check_snapshot(_BASE)  # the control: a clean snapshot passes
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**_BASE, key: _nest(leaf)})


def test_hostile_keys_and_containers_and_depth_are_refused() -> None:
    for hostile in (
        {"handle": 1},
        {"zzz": 1},
        {"ok": {"deeper": [{"email": 1}]}},
    ):
        with pytest.raises(ImportGuardError):
            bsky.check_snapshot({**_BASE, "followers": _nest(hostile)})
    with pytest.raises(ImportGuardError):  # a row hiding a tuple of a string
        bsky.check_snapshot(
            {**_BASE, "interactions": [{"boosts": 1, "favorites": 2, "replies": (3,)}]}
        )
    with pytest.raises(ImportGuardError):  # deeper than any legitimate shape
        bsky.check_snapshot({**_BASE, "followers": _nest(1, depth=40)})
    for bad in (True, -1, 10**13, "9"):
        with pytest.raises(ImportGuardError):
            bsky.check_snapshot({**_BASE, "followers": bad})
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot({**_BASE, "reason": "not_a_reason_word"})


# --- fetch_json_view -------------------------------------------------------------

URL = "https://public.api.bsky.app/xrpc/x"


# --- Daybreak round 2 fixes (#922) --------------------------------------------------


def test_a_terminal_page_that_returns_after_the_deadline_is_discarded() -> None:
    # The last request starts with time left, and the clock jumps past the
    # deadline while it runs; the page has no cursor, so only a clock check
    # after the response can stop it from being accepted as complete.
    clock = Clock()
    read, slow, _ = _collect_slow(
        [feed_page([post(1, NOW - timedelta(days=1), likes=5)])], clock, [1.0, 95.0]
    )
    assert len(slow.deadlines) == 2  # the profile, then the one feed page
    assert isinstance(read.rows, NotProvided) and read.reason == "timeout"
    assert not read.complete and bsky.history_aggregates(read) is None


def test_a_profile_that_returns_after_the_deadline_is_discarded() -> None:
    clock = Clock()
    read, slow, _ = _collect_slow([feed_page([])], clock, [95.0])
    assert read.reason == "timeout" and len(slow.deadlines) == 1
    assert (read.followers, read.posts) == (None, None)  # a late profile is not kept


def test_a_pause_that_runs_past_the_deadline_ends_the_run() -> None:
    clock = Clock()
    slow = Slow(Pages([feed_page([post(1, NOW)])]).transport, clock, [1.0])

    def long_sleep(seconds: float) -> None:
        clock.t += 200.0  # the pause itself overran the deadline

    read = bsky.collect(
        ACTOR,
        now=NOW,
        transport=slow,
        sleep=long_sleep,
        jitter=lambda: 0.0,
        clock=clock,
    )
    assert read.reason == "timeout" and len(slow.deadlines) == 1


def test_a_response_on_time_is_still_accepted() -> None:
    clock = Clock()
    read, _, _ = _collect_slow(
        [feed_page([post(1, NOW - timedelta(days=1))])], clock, [1.0, 80.0]
    )
    assert read.complete and len(rows_of(read)) == 1


_ROWS = [{"boosts": 1, "favorites": 2, "replies": 3}]
GONE: Any = object()  # a field left out of a test snapshot


def _snap(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "platform": "bluesky",
        "as_of": "2026-11-26",
        "complete": True,
        "interactions": [],
    }
    base.update(fields)
    return {k: v for k, v in base.items() if v is not GONE}


LEGAL = [
    _snap(),  # a finished empty window
    _snap(interactions=_ROWS),
    _snap(complete=False, reason="partial_pages", interactions=GONE),  # no prior
    _snap(complete=False, reason="page_cap", interactions=_ROWS),
    _snap(complete=False, reason="egress_timeout", interactions=_ROWS),
]
ILLEGAL = {
    "complete with a reason": _snap(reason="partial_pages"),
    "complete with a reason and rows": _snap(reason="timeout", interactions=_ROWS),
    "complete without interactions": _snap(interactions=GONE),
    "complete without interactions, with a reason": _snap(
        interactions=GONE, reason="partial_pages"
    ),
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
    bsky.check_snapshot(snapshot)


@pytest.mark.parametrize("name", list(ILLEGAL))
def test_every_illegal_state_combination_is_refused_everywhere(name: str) -> None:
    snapshot = ILLEGAL[name]
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot(snapshot)
    assert bsky.entry_from_snapshot(snapshot, actor=ACTOR) is None
    assert bsky.snapshot_aggregates(snapshot) is None


def test_the_reviewers_contradictory_snapshot_is_not_a_completed_zero() -> None:
    snapshot = {
        "platform": "bluesky",
        "as_of": "2026-11-26",
        "complete": True,
        "reason": "partial_pages",
    }
    with pytest.raises(ImportGuardError):
        bsky.check_snapshot(snapshot)
    assert bsky.entry_from_snapshot(snapshot, actor=ACTOR) is None
    assert bsky.snapshot_aggregates(snapshot) is None


def test_snapshot_aggregates_sum_only_a_complete_consistent_snapshot() -> None:
    assert bsky.snapshot_aggregates(_snap(interactions=_ROWS)) == {
        "posts_30d": 1,
        "boosts_30d": 1,
        "favorites_30d": 2,
        "replies_30d": 3,
    }
    empty = bsky.snapshot_aggregates(_snap())
    assert empty is not None and empty["posts_30d"] == 0
    stale = _snap(complete=False, reason="timeout", interactions=_ROWS)
    assert bsky.snapshot_aggregates(stale) is None  # prior rows are not a fresh sum


def test_built_snapshots_always_satisfy_the_table() -> None:
    empty_prior: list[InteractionRow] = []
    broken = run(Pages([reply({}, status=500)]))
    kept = bsky.build_snapshot(
        broken, as_of=date(2026, 11, 26), prior=[InteractionRow(1, 2, 3)]
    )
    bare = bsky.build_snapshot(broken, as_of=date(2026, 11, 26), prior=empty_prior)
    fresh = bsky.build_snapshot(run(Pages([feed_page([])])), as_of=date(2026, 11, 26))
    for snap in (kept, bare, fresh):
        assert snap is not None
        bsky.check_snapshot(snap)
    assert bare is not None and "interactions" not in bare  # never an empty list
    assert fresh is not None and fresh["interactions"] == []


def test_history_aggregates_refuse_a_bad_row_list() -> None:
    good = run(Pages([feed_page([post(1, NOW)])]))
    assert bsky.history_aggregates(good) is not None
    too_many = bsky.BlueskyRead(1, 1, (InteractionRow(1, 1, 1),) * 201, 3)
    assert bsky.history_aggregates(too_many) is None  # window_truncated is no sum


def test_fetch_json_view_returns_an_object_and_a_header_view_only() -> None:
    t = FakeTransport(
        {URL: reply({"a": 1}, headers={"date": "d", "set-cookie": "SECRET"})}
    )
    got = fetch_json_view(URL, transport=t, sleep=lambda s: None)
    assert got.data == {"a": 1}
    assert got.headers["date"] == "d" and "set-cookie" not in got.headers
    assert t.calls[0].max_bytes == 1_048_576
    writable: Any = got.headers
    with pytest.raises(TypeError):
        writable["x"] = "y"


@pytest.mark.parametrize(
    ("answer", "word"),
    [
        (reply([1]), "bad_json"),
        (reply(body=b'{"a": NaN}'), "bad_json"),
        (reply(body=b'{"a": 1234567890123456789}'), "bad_json"),
        (reply({}, headers={"content-type": "text/plain"}), "content_type"),
        (reply({}, status=404), "http_404"),
    ],
)
def test_fetch_json_view_refuses_what_the_array_path_refuses(
    answer: Any, word: str
) -> None:
    with pytest.raises(FetchError) as caught:
        fetch_json_view(
            URL, transport=FakeTransport({URL: answer}), sleep=lambda s: None
        )
    assert caught.value.kind == word
