# SPDX-License-Identifier: Apache-2.0
"""The Bluesky provider of the fediverse collector (slice 2, #922).

Reads one public account on ``public.api.bsky.app`` with no login: one
``getProfile`` call and up to twelve ``getAuthorFeed`` pages (13 calls at most),
and reduces the account's own posts of the last 30 days to rows of three whole
numbers (reposts, likes, replies). See ``docs/design/fediverse-collector.md``.

What leaves this module is counts and fixed words. No post id, handle, text or
time is kept: the parser holds one page at a time, remembers the post addresses
of the current run only to drop duplicates, and returns ``InteractionRow``
values that have no field for anything else.

The window uses ``indexedAt``, the server's clock, never the author-supplied
``createdAt``. Reposts of other accounts' posts, replies to others and posts by
any other author are not counted. A read that cannot be completed in full is a
``NotProvided`` with a fixed reason word: it keeps the prior rows and is never
a zero. A real zero needs a read that finished cleanly.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import quote

from reach_around.config.fediverse import MAX_INTERACTIONS, FediverseEntry
from reach_around.config.import_guard import (
    ImportGuardError,
    check_names,
    check_value,
)
from reach_around.counts.fediverse_rows import (
    EGRESS_REASONS,
    InteractionRow,
    NotProvided,
    keep_prior,
)
from reach_around.counts.fediverse_window import (
    classify,
    post_time,
    server_clock_skewed,
)
from reach_around.net.egress import Transport
from reach_around.net.fetch import FetchError, fetch_json_view

HOST = "public.api.bsky.app"
PAGE_LIMIT = 50
MAX_PAGES = 12
MAX_CALLS = 1 + MAX_PAGES  # one profile call and the pages: 13 at most
PLATFORM_DEADLINE = 90.0  # seconds for the whole platform
PAUSE = 1.0  # seconds between two calls to the host, plus up to one more of jitter
MAX_COUNT = 10**12  # the profile's own follower and post counts

_PIN = "app.bsky.feed.defs#reasonPin"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
# A handle is two or more dot-joined labels and the last one starts with a letter.
_HANDLE = re.compile(rf"^(?:{_LABEL}\.)+[A-Za-z](?:[A-Za-z0-9-]{{0,61}}[A-Za-z0-9])?$")
_DID = re.compile(r"^did:(?:plc|web):[A-Za-z0-9._:-]{1,120}$")
_CURSOR = re.compile(r"^[A-Za-z0-9._:+=-]{1,128}$")


def check_actor(value: object) -> str:
    """The handle or DID, or ``ValueError``.

    A handle is letters, digits and hyphens in dot-separated labels (two or
    more, 253 characters at most); a DID is ``did:plc:`` or ``did:web:`` with a
    short plain tail. No scheme, no path, no ``@``, no query, no space.
    """
    if type(value) is not str or not value:
        raise ValueError("the Bluesky actor is a handle or a DID")
    if len(value) <= 253 and _HANDLE.fullmatch(value):
        return value
    if _DID.fullmatch(value):
        return value
    raise ValueError("the Bluesky actor is a handle like name.example.com or a DID")


@dataclass(frozen=True)
class BlueskyRead:
    """The outcome of one run: profile counts and the rows or an incomplete marker."""

    followers: int | None
    posts: int | None
    rows: tuple[InteractionRow, ...] | NotProvided
    calls: int
    dropped_future: int = 0

    @property
    def complete(self) -> bool:
        return not isinstance(self.rows, NotProvided)

    @property
    def reason(self) -> str | None:
        return self.rows.reason if isinstance(self.rows, NotProvided) else None


class _Stop(Exception):
    """Internal: the run ends here with this fixed reason word."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _reason_of(error: FetchError, *, later_page: bool) -> str:
    """Map a fetch failure to the closed list of reason words."""
    kind = error.kind
    if kind in EGRESS_REASONS:
        mapped = kind
    elif kind == "rate_limited":
        mapped = "rate_limited"
    elif kind in ("http_401", "http_403"):
        mapped = "needs_token"
    elif kind in ("http_400", "http_404"):
        mapped = "account_not_found"
    elif kind == "server_error":
        mapped = "server_error"
    elif kind in ("content_type", "bad_json"):
        mapped = kind
    elif kind == "too_large":
        mapped = "partial_pages"
    else:
        mapped = "bad_shape"
    if later_page and mapped not in ("rate_limited", "needs_token"):
        return "partial_pages"
    return mapped


def _int(value: object, limit: int) -> int | None:
    """A plain whole number from 0 to ``limit`` (no bool, no float, no string)."""
    if type(value) is int and 0 <= value <= limit:
        return value
    return None


def _default_jitter() -> float:
    return random.SystemRandom().random()


def collect(
    actor: str,
    *,
    now: datetime,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _default_jitter,
    clock: Callable[[], float] = time.monotonic,
) -> BlueskyRead:
    """Read the account and return counts. Never raises for a remote failure.

    ``actor`` is validated here too (``ValueError`` for a bad one); ``now`` is
    the run instant, aware. Remote failures come back as a ``NotProvided`` with
    a fixed reason word, and a profile that was read keeps its two counts.
    """
    actor = check_actor(actor)
    if now.tzinfo is None:
        raise ValueError("the run instant must be timezone aware")
    started = clock()
    calls = 0
    followers: int | None = None
    posts: int | None = None
    dropped = 0

    def get(path: str, *, later_page: bool) -> Mapping[str, object]:
        nonlocal calls
        if clock() - started > PLATFORM_DEADLINE:
            raise _Stop("timeout")
        if calls >= MAX_CALLS:
            raise _Stop("page_cap")
        if calls:
            sleep(PAUSE + jitter())
        calls += 1
        try:
            fetched = fetch_json_view(
                f"https://{HOST}/xrpc/{path}",
                transport=transport,
                sleep=sleep,
                jitter=jitter,
                clock=clock,
                now=lambda: now,
            )
        except FetchError as error:
            raise _Stop(_reason_of(error, later_page=later_page)) from None
        if server_clock_skewed(fetched.headers.get("date"), now):
            raise _Stop("clock_skew")
        return fetched.data

    try:
        profile = get(
            "app.bsky.actor.getProfile?actor=" + quote(actor, safe=""),
            later_page=False,
        )
        did = profile.get("did")
        if type(did) is not str or not _DID.fullmatch(did):
            raise _Stop("bad_shape")
        followers = _int(profile.get("followersCount"), MAX_COUNT)
        posts = _int(profile.get("postsCount"), MAX_COUNT)

        rows: list[InteractionRow] = []
        seen: set[str] = set()
        cursor: str | None = None
        for page in range(1, MAX_PAGES + 1):
            query = (
                "app.bsky.feed.getAuthorFeed?actor="
                + quote(did, safe="")
                + f"&limit={PAGE_LIMIT}&filter=posts_no_replies"
            )
            if cursor is not None:
                query += "&cursor=" + quote(cursor, safe="")
            data = get(query, later_page=page > 1)
            feed = data.get("feed")
            if not isinstance(feed, list) or len(feed) > 100:
                raise _Stop("bad_shape")
            done = False
            for item in feed:
                outcome = _item(item, did, now)
                if outcome == "stop":
                    done = True
                    break
                if outcome == "future":
                    dropped += 1
                elif isinstance(outcome, tuple):
                    key, row = outcome
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
            if done:
                break
            nxt = data.get("cursor")
            if nxt is None or nxt == "":
                break  # the end of the feed: the window was read in full
            if type(nxt) is not str or not _CURSOR.fullmatch(nxt):
                raise _Stop("bad_shape")
            if nxt == cursor:
                raise _Stop("partial_pages")
            cursor = nxt
        else:
            raise _Stop("page_cap")
        ordered = tuple(sorted(rows, key=lambda r: (r.boosts, r.favorites, r.replies)))
        return BlueskyRead(followers, posts, ordered, calls, dropped)
    except _Stop as stop:
        return BlueskyRead(followers, posts, NotProvided(stop.reason), calls, dropped)


def _item(
    item: object, did: str, now: datetime
) -> tuple[str, InteractionRow] | str | None:
    """One feed entry: a row, ``stop`` (older than the window), ``future``, or None.

    None means the entry is not the account's own plain post. A malformed entry
    that would have been counted raises ``_Stop('bad_shape')``.
    """
    if not isinstance(item, dict):
        raise _Stop("bad_shape")
    post = item.get("post")
    if not isinstance(post, dict):
        raise _Stop("bad_shape")
    reason = item.get("reason")
    pinned = False
    if reason is not None:
        kind = reason.get("$type") if isinstance(reason, dict) else None
        if kind != _PIN:
            return None  # a repost (or anything unknown) is not the account's post
        pinned = True
    author = post.get("author")
    if not isinstance(author, dict) or author.get("did") != did:
        return None
    record = post.get("record")
    if item.get("reply") is not None or (
        isinstance(record, dict) and record.get("reply") is not None
    ):
        return None  # a reply to someone else is not counted
    placed = classify(post_time(post, "indexedAt"), now)
    if placed == "invalid":
        raise _Stop("bad_shape")
    if placed == "before":
        return None if pinned else "stop"  # pins come first and out of order
    if placed == "future":
        return "future"
    uri = post.get("uri")
    if type(uri) is not str or not 0 < len(uri) <= 512:
        raise _Stop("bad_shape")
    reposts = _int(post.get("repostCount"), MAX_INTERACTIONS)
    likes = _int(post.get("likeCount"), MAX_INTERACTIONS)
    replies = _int(post.get("replyCount"), MAX_INTERACTIONS)
    if reposts is None or likes is None or replies is None:
        raise _Stop("bad_shape")
    return uri, InteractionRow(reposts, likes, replies)


# --- snapshot, guard pass, history aggregates and the kit entry -----------------

_TOP_KEYS = frozenset(
    {
        "platform",
        "profile_url",
        "as_of",
        "followers",
        "posts",
        "interactions",
        "complete",
        "reason",
    }
)
_ROW_KEYS = frozenset({"boosts", "favorites", "replies"})


def profile_url(actor: str) -> str:
    """The owner's public profile link, built from the checked config value."""
    return "https://bsky.app/profile/" + check_actor(actor)


def check_snapshot(snapshot: Mapping[str, object]) -> None:
    """The import guard over every key and string of a snapshot (design 4.1).

    Raises ``ImportGuardError`` for a person-level key, a key outside the closed
    set, or a string value that looks like an email, @handle, IP address or id.
    """
    check_names(snapshot.keys(), _TOP_KEYS)
    for value in snapshot.values():
        if isinstance(value, str):
            check_value(value)
    rows = snapshot.get("interactions", [])
    if not isinstance(rows, list):
        raise ImportGuardError("not an aggregate")
    for row in rows:
        if not isinstance(row, dict):
            raise ImportGuardError("not an aggregate")
        check_names(row.keys(), _ROW_KEYS)
        for value in row.values():
            if type(value) is not int:
                raise ImportGuardError("not an aggregate")


def build_snapshot(
    read: BlueskyRead,
    *,
    actor: str,
    as_of: date,
    prior: Sequence[InteractionRow] | None = None,
) -> dict[str, object] | None:
    """The snapshot entry for the platform, or None when the guard refuses it.

    An incomplete read keeps ``prior`` as the interactions (or leaves the key
    out when there is none) with ``complete`` false and the reason word; a
    complete read replaces them. Nothing here is a made-up zero.
    """
    kept, reason = keep_prior(
        read.rows if isinstance(read.rows, NotProvided) else list(read.rows), prior
    )
    snapshot: dict[str, object] = {
        "platform": "bluesky",
        "profile_url": profile_url(actor),
        "as_of": as_of.isoformat(),
        "complete": reason is None and read.complete,
    }
    if read.followers is not None:
        snapshot["followers"] = read.followers
    if read.posts is not None:
        snapshot["posts"] = read.posts
    if kept is not None:
        snapshot["interactions"] = [row.as_dict() for row in kept]
    if reason is not None:
        snapshot["reason"] = reason
    try:
        check_snapshot(snapshot)
    except ImportGuardError:
        return None
    return snapshot


def history_aggregates(read: BlueskyRead) -> dict[str, int] | None:
    """The window sums for the history store, only for a complete read.

    ``posts_30d`` and the three sums (design 4.2). An incomplete read returns
    None: it writes nothing, and a zero is only ever a finished empty window.
    """
    if isinstance(read.rows, NotProvided):
        return None
    return {
        "posts_30d": len(read.rows),
        "boosts_30d": sum(r.boosts for r in read.rows),
        "favorites_30d": sum(r.favorites for r in read.rows),
        "replies_30d": sum(r.replies for r in read.rows),
    }


def entry_from_snapshot(snapshot: Mapping[str, object]) -> FediverseEntry | None:
    """The ``kit.fediverse`` entry for a fresh complete snapshot, else None.

    This is what lets collected rows feed the existing block: the entry is the
    same ``FediverseEntry`` the owner would type. An incomplete snapshot returns
    None, so it never replaces a hand-kept entry. The hand-kept path is
    unchanged.
    """
    if snapshot.get("complete") is not True:
        return None
    rows = snapshot.get("interactions", [])
    if not isinstance(rows, list):
        return None
    payload: dict[str, object] = {
        key: snapshot[key]
        for key in ("platform", "profile_url", "as_of", "followers", "posts")
        if key in snapshot
    }
    payload["interactions"] = rows
    return FediverseEntry.model_validate(payload)


def utc_day(now: datetime) -> date:
    """The UTC date of the run instant (the snapshot's ``as_of``)."""
    return now.astimezone(UTC).date()
