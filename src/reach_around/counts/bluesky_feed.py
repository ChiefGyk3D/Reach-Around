# SPDX-License-Identifier: Apache-2.0
"""The Bluesky provider of the fediverse collector (slice 2, #922).

Reads one public account on ``public.api.bsky.app`` with no login: one
``getProfile`` call and up to twelve ``getAuthorFeed`` pages (13 HTTP requests
at most, retries included),
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
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from urllib.parse import quote

from reach_around.config.fediverse import MAX_INTERACTIONS, FediverseEntry
from reach_around.config.import_guard import (
    ImportGuardError,
    check_names,
    check_value,
    is_person_name,
)
from reach_around.counts.fediverse_rows import (
    EGRESS_REASONS,
    InteractionRow,
    NotProvided,
    is_reason,
    keep_prior,
)
from reach_around.counts.fediverse_window import (
    classify,
    post_time,
    server_clock_skewed,
)
from reach_around.net.egress import (
    HttpsTransport,
    RawResponse,
    Transport,
    TransportRequest,
)
from reach_around.net.fetch import TIMEOUT, FetchError, fetch_json_view

HOST = "public.api.bsky.app"
PAGE_LIMIT = 50
MAX_PAGES = 12
# One shared budget of HTTP requests for the whole run: the profile call, the
# pages, and every retry or redirect hop all draw from it (13 at most).
MAX_CALLS = 1 + MAX_PAGES
# One absolute monotonic deadline for the whole run: the pauses, the retry
# waits and every request's own timeout all sit inside it.
PLATFORM_DEADLINE = 90.0
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


class _BudgetEnded(Exception):
    """Internal: a request or a wait was refused by the shared budget."""


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
    deadline = clock() + PLATFORM_DEADLINE  # the one absolute deadline of the run
    send: Transport = transport if transport is not None else HttpsTransport()
    calls = 0  # every HTTP request: first tries, retries and redirect hops
    ended: list[str] = []  # the reason a budget ended the run, once it did
    followers: int | None = None
    posts: int | None = None
    dropped = 0

    def counted(request: TransportRequest) -> RawResponse:
        """Every request, retries included, draws on the shared budget."""
        nonlocal calls
        if deadline - clock() <= 0:
            ended.append("timeout")
            raise _BudgetEnded
        if calls >= MAX_CALLS:
            ended.append("page_cap")
            raise _BudgetEnded
        calls += 1
        # Never later than the run's deadline, whatever the fetch layer asked.
        return send(replace(request, deadline=min(request.deadline, deadline)))

    def bounded_sleep(seconds: float) -> None:
        """A pause or a retry wait that would cross the deadline ends the run."""
        if clock() + seconds >= deadline:
            ended.append("timeout")
            raise _BudgetEnded
        sleep(seconds)

    def get(path: str, *, later_page: bool) -> Mapping[str, object]:
        if calls >= MAX_CALLS:
            raise _Stop("page_cap")
        try:
            if calls:
                bounded_sleep(PAUSE + jitter())  # the pause is inside the deadline
            remaining = deadline - clock()
            if remaining <= 0:
                raise _Stop("timeout")
            fetched = fetch_json_view(
                f"https://{HOST}/xrpc/{path}",
                transport=counted,
                sleep=bounded_sleep,
                jitter=jitter,
                clock=clock,
                now=lambda: now,
                timeout=min(TIMEOUT, remaining),
            )
        except _BudgetEnded:
            raise _Stop(ended[0]) from None
        except FetchError as error:
            if ended:  # the budget stopped a request: its own word, not a fetch one
                raise _Stop(ended[0]) from None
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
        seen_cursors: set[str] = set()  # bounded by the page cap
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
            # The end of the window is never read from one item: the feed's
            # order is not guaranteed, so a single old post (a pin, a late
            # re-index) must not end the walk. The walk ends when the cursor
            # ends, when the budget does (incomplete), or after one whole page
            # that held own posts older than the window and none inside it.
            recent = old = 0
            for item in feed:
                outcome = _item(item, did, now)
                if outcome == "old":
                    old += 1
                elif outcome == "future":
                    dropped += 1
                    recent += 1
                elif isinstance(outcome, tuple):
                    recent += 1
                    key, row = outcome
                    if key not in seen:
                        seen.add(key)
                        rows.append(row)
            if old and not recent:
                break  # a whole page of out-of-window posts: the window was read
            nxt = data.get("cursor")
            if nxt is None or nxt == "":
                break  # the end of the feed: the window was read in full
            if type(nxt) is not str or not _CURSOR.fullmatch(nxt):
                raise _Stop("bad_shape")
            if nxt in seen_cursors:
                raise _Stop("partial_pages")  # any repeat, not only the last
            seen_cursors.add(nxt)
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
    """One feed entry: a row, ``old`` (before the window), ``future``, or None.

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
        return None if pinned else "old"  # pins come first and out of order
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

#
# A snapshot is anonymous: the three counts of each row, two profile counts, a
# date, a flag and a fixed reason word. Which account it belongs to is config
# (the config names the account); it is never stored in the snapshot, in the
# history aggregates or in a row.
_TOP_KEYS = frozenset(
    {"platform", "as_of", "followers", "posts", "interactions", "complete", "reason"}
)
_ROW_KEYS = frozenset({"boosts", "favorites", "replies"})
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOTTED = re.compile(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")  # a handle or host shape
MAX_WORD = 40  # the longest legitimate string is a reason word or a date
MAX_DEPTH = 8
NOT_ANONYMOUS = "the snapshot holds a value that is not an anonymous aggregate"


def profile_url(actor: str) -> str:
    """The owner's public profile link, for the presentation boundary only.

    Built from the checked config value when a block is rendered; it is never
    part of a stored snapshot.
    """
    return "https://bsky.app/profile/" + check_actor(actor)


def _walk(value: object, depth: int) -> None:
    """Apply the guard to every key and leaf, however deep (nested dict, list, tuple).

    Raises ``ImportGuardError`` for a person-level key, a string that has an
    identifying shape (an ``at://`` address, a DID, a URL, a handle or host with
    dots, an email, an @handle, an address, free text), a float, ``None`` or an
    object of any other type.
    """
    if depth > MAX_DEPTH:
        raise ImportGuardError(NOT_ANONYMOUS)
    if isinstance(value, dict):
        for key, inner in value.items():
            if type(key) is not str or is_person_name(key):
                raise ImportGuardError(NOT_ANONYMOUS)
            _walk(inner, depth + 1)
    elif isinstance(value, (list, tuple)):
        for inner in value:
            _walk(inner, depth + 1)
    elif isinstance(value, str):
        lowered = value.lower()
        if (
            len(value) > MAX_WORD
            or any(ch.isspace() for ch in value)
            or "://" in value
            or "did:" in lowered
            or "@" in value
            or "/" in value
            or _DOTTED.search(value)
        ):
            raise ImportGuardError(NOT_ANONYMOUS)
        check_value(value)
    elif type(value) is not int and type(value) is not bool:
        raise ImportGuardError(NOT_ANONYMOUS)


def _count_ok(value: object, limit: int) -> bool:
    return type(value) is int and 0 <= value <= limit


def check_snapshot(snapshot: Mapping[str, object]) -> None:
    """The import guard over the whole snapshot (design 4.1).

    Two passes. The recursive pass walks every key and every leaf at any depth
    and refuses a person-level key or an identifying string. The schema pass
    then requires the exact shape: fixed platform, ISO date, a boolean, counts
    that are plain bounded integers, a reason from the closed list, and rows of
    exactly the three counts. Raises ``ImportGuardError``.
    """
    _walk(snapshot, 0)
    check_names(snapshot.keys(), _TOP_KEYS)
    if snapshot.get("platform") != "bluesky":
        raise ImportGuardError(NOT_ANONYMOUS)
    as_of = snapshot.get("as_of")
    if type(as_of) is not str or not _AS_OF.fullmatch(as_of):
        raise ImportGuardError(NOT_ANONYMOUS)
    if type(snapshot.get("complete")) is not bool:
        raise ImportGuardError(NOT_ANONYMOUS)
    for key in ("followers", "posts"):
        if key in snapshot and not _count_ok(snapshot[key], MAX_COUNT):
            raise ImportGuardError(NOT_ANONYMOUS)
    if "reason" in snapshot and not is_reason(snapshot["reason"]):
        raise ImportGuardError(NOT_ANONYMOUS)
    rows = snapshot.get("interactions", [])
    if not isinstance(rows, list):
        raise ImportGuardError(NOT_ANONYMOUS)
    for row in rows:
        if not isinstance(row, dict):
            raise ImportGuardError(NOT_ANONYMOUS)
        check_names(row.keys(), _ROW_KEYS)
        if set(row) != _ROW_KEYS or not all(
            _count_ok(v, MAX_INTERACTIONS) for v in row.values()
        ):
            raise ImportGuardError(NOT_ANONYMOUS)


def build_snapshot(
    read: BlueskyRead,
    *,
    as_of: date,
    prior: Sequence[InteractionRow] | None = None,
) -> dict[str, object] | None:
    """The snapshot entry for the platform, or None when the guard refuses it.

    Anonymous by construction: no actor, handle, DID or link goes in. An
    incomplete read keeps ``prior`` as the interactions (or leaves the key out
    when there is none) with ``complete`` false and the reason word; a complete
    read replaces them. Nothing here is a made-up zero.
    """
    kept, reason = keep_prior(
        read.rows if isinstance(read.rows, NotProvided) else list(read.rows), prior
    )
    snapshot: dict[str, object] = {
        "platform": "bluesky",
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


def entry_from_snapshot(
    snapshot: Mapping[str, object], *, actor: str
) -> FediverseEntry | None:
    """The ``kit.fediverse`` entry for a fresh complete snapshot, else None.

    This is what lets collected rows feed the existing block: the entry is the
    same ``FediverseEntry`` the owner would type. ``actor`` comes from config,
    not from the snapshot (which is anonymous): the re-check link is built here,
    at the presentation boundary. An incomplete snapshot returns None, so it
    never replaces a hand-kept entry. The hand-kept path is unchanged.
    """
    if snapshot.get("complete") is not True:
        return None
    rows = snapshot.get("interactions", [])
    if not isinstance(rows, list):
        return None
    payload: dict[str, object] = {
        key: snapshot[key]
        for key in ("platform", "as_of", "followers", "posts")
        if key in snapshot
    }
    payload["profile_url"] = profile_url(actor)
    payload["interactions"] = rows
    return FediverseEntry.model_validate(payload)


def utc_day(now: datetime) -> date:
    """The UTC date of the run instant (the snapshot's ``as_of``)."""
    return now.astimezone(UTC).date()
