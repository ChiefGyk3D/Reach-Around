# SPDX-License-Identifier: Apache-2.0
"""The Mastodon provider of the fediverse collector (slice 3, #923).

Reads one public account on a Mastodon-API instance named in config, with no
login: one ``accounts/lookup`` call (skipped when a numeric ``account_id`` is
configured) and up to twelve ``accounts/:id/statuses`` pages, 13 HTTP requests
at most with retries and redirect hops counted. The account's own posts of the
last 30 days become rows of three whole numbers (boosts, favorites, replies).
See ``docs/design/fediverse-collector.md``.

What leaves this module is counts and fixed words. No post id, handle, text or
time is kept: one page is parsed at a time, the ids of the current run are
remembered only to drop duplicates and to build the next cursor, and the result
is ``InteractionRow`` values that have no field for anything else.

The next page URL is built from the configured host and a numeric ``max_id``
cursor. A ``Link`` header, a ``next`` value or any URL in a body is never
followed. The window uses ``created_at``, the server's clock. Boosts, replies to
other accounts, posts of any other author and non-public posts are not counted.
A read that cannot be completed in full is a ``NotProvided`` with a fixed reason
word: it keeps the prior rows and is never a zero. A real zero needs a read that
finished cleanly.

Capacity: the walk never trusts ordering to stop early. It ends at an empty page
(then every post was read) or when the budget ends it. An account with more
statuses than the page budget can hold (12 pages of 40, so 480 at best) reports
``page_cap`` incomplete by design; the prior values are kept.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from urllib.parse import quote

from reach_around.config.fediverse import (
    MAX_INTERACTIONS,
    MAX_POSTS,
    FediverseEntry,
)
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
    EgressBlocked,
    HttpsTransport,
    RawResponse,
    Transport,
    TransportRequest,
    validate_hostname,
)
from reach_around.net.fetch import (
    TIMEOUT,
    FetchedArray,
    FetchError,
    fetch_json,
    fetch_json_array,
)

PAGE_LIMIT = 40  # the documented maximum for the statuses endpoint
MAX_PAGES = 12
# One shared budget of HTTP requests for the whole run: the lookup, the pages,
# and every retry or redirect hop all draw from it (13 at most).
MAX_CALLS = 1 + MAX_PAGES
# One absolute monotonic deadline for the whole run: the pauses, the retry
# waits and every request's own timeout all sit inside it.
PLATFORM_DEADLINE = 90.0
PAUSE = 1.0  # seconds between two calls to the host, plus up to one more of jitter
MAX_COUNT = 10**12  # the profile's own follower and post counts
LOW_RATE_LIMIT = 5  # stop when the server says fewer calls than this remain

_ID = re.compile(r"[0-9]{1,20}")
_ACCT = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,28}[A-Za-z0-9_])?")
_VISIBLE = frozenset({"public", "unlisted"})
_HIDDEN = frozenset({"private", "direct"})


def check_host(value: object) -> str:
    """The instance host as the egress guard normalizes it, or ``ValueError``.

    Public DNS name only: no IP literal in any notation, no ``localhost`` or
    local suffix, no port, userinfo, path or query, no trailing dot, no
    whitespace. Uppercase and non-ASCII spellings are refused rather than
    rewritten, so the host that is checked is the host that is used (give an
    internationalized name in its ``xn--`` form).
    """
    if type(value) is not str or not value:
        raise ValueError("the instance host is a public host name")
    try:
        canonical = validate_hostname(value)
    except EgressBlocked:
        raise ValueError("the instance host is a public host name") from None
    if canonical != value:
        raise ValueError("the instance host is lower-case ASCII, no trailing dot")
    return value


def check_acct(value: object) -> str:
    """The local account name, or ``ValueError``.

    ASCII letters, digits, underscore, dot and hyphen (30 at most), no ``@``,
    no instance part, no space, no leading or trailing dot or hyphen.
    """
    if type(value) is not str or not _ACCT.fullmatch(value):
        raise ValueError("the account is the local name, like alice, without @")
    return value


def check_account_id(value: object) -> str:
    """A numeric account id as a string of digits, or ``ValueError``."""
    if type(value) is int and not isinstance(value, bool) and value >= 0:
        value = str(value)
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError("the account id is a string of digits")
    return value


@dataclass(frozen=True)
class MastodonRead:
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
    elif kind == "http_404":
        mapped = "account_not_found"
    elif kind == "http_410":
        mapped = "account_suspended"
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
    host: str,
    acct: str | None = None,
    *,
    account_id: str | int | None = None,
    now: datetime,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = _default_jitter,
    clock: Callable[[], float] = time.monotonic,
) -> MastodonRead:
    """Read the account and return counts. Never raises for a remote failure.

    ``host`` and ``acct`` (or a numeric ``account_id``, which skips the lookup
    and so reads no follower count) are validated here too (``ValueError`` for
    a bad one); ``now`` is the run instant, aware. Remote failures come back as
    a ``NotProvided`` with a fixed reason word, and a profile that was read
    keeps its two counts.
    """
    host = check_host(host)
    if account_id is None:
        acct = check_acct(acct)
        known_id: str | None = None
    else:
        known_id = check_account_id(account_id)
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
        response = send(replace(request, deadline=min(request.deadline, deadline)))
        if clock() >= deadline:
            # A response that arrived at or after the deadline is discarded, so
            # a late page is never merged into the result (the transport is not
            # trusted to enforce the deadline itself).
            ended.append("timeout")
            raise _BudgetEnded
        return response

    def bounded_sleep(seconds: float) -> None:
        """A pause or a retry wait that would cross the deadline ends the run."""
        if clock() + seconds >= deadline:
            ended.append("timeout")
            raise _BudgetEnded
        sleep(seconds)
        if clock() >= deadline:  # the pause itself ran long
            ended.append("timeout")
            raise _BudgetEnded

    def get(
        path: str, *, later_page: bool, array: bool
    ) -> dict[str, object] | FetchedArray:
        if calls >= MAX_CALLS:
            raise _Stop("page_cap")
        try:
            if calls:
                bounded_sleep(PAUSE + jitter())  # the pause is inside the deadline
            remaining = deadline - clock()
            if remaining <= 0:
                raise _Stop("timeout")
            url = f"https://{host}{path}"
            result: dict[str, object] | FetchedArray
            if array:
                result = fetch_json_array(
                    url,
                    transport=counted,
                    sleep=bounded_sleep,
                    jitter=jitter,
                    clock=clock,
                    now=lambda: now,
                    timeout=min(TIMEOUT, remaining),
                )
            else:
                result = fetch_json(
                    url,
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
        if clock() >= deadline:  # nothing is accepted after the absolute deadline
            raise _Stop("timeout")
        if isinstance(result, FetchedArray) and server_clock_skewed(
            result.headers.get("date"), now
        ):
            raise _Stop("clock_skew")
        return result

    try:
        if known_id is None:
            if acct is None:
                raise ValueError("an account name or an account id is required")
            found = get(
                "/api/v1/accounts/lookup?acct=" + quote(acct, safe=""),
                later_page=False,
                array=False,
            )
            if not isinstance(found, dict):
                raise _Stop("bad_shape")
            account = found.get("id")
            if type(account) is not str or not _ID.fullmatch(account):
                raise _Stop("bad_shape")
            if found.get("moved") is not None:
                raise _Stop("account_moved")  # a move is never followed
            if found.get("suspended") is True:
                raise _Stop("account_suspended")
            followers = _int(found.get("followers_count"), MAX_COUNT)
            posts = _int(found.get("statuses_count"), MAX_COUNT)
            known_id = account

        rows: list[InteractionRow] = []
        seen: set[str] = set()
        cursor: int | None = None
        for page in range(1, MAX_PAGES + 1):
            query = (
                f"/api/v1/accounts/{known_id}/statuses?limit={PAGE_LIMIT}"
                "&exclude_replies=true&exclude_reblogs=true"
            )
            if cursor is not None:
                query += f"&max_id={cursor}"
            fetched = get(query, later_page=page > 1, array=True)
            if not isinstance(fetched, FetchedArray):
                raise _Stop("bad_shape")
            items = fetched.items
            if len(items) > PAGE_LIMIT * 2:
                raise _Stop("bad_shape")
            # Ordering is never trusted, so nothing ends the walk early: not an
            # old post, not a whole old page. The walk ends only at an empty page
            # (every post was read, so the window is read in full) or when a
            # budget ends it (``page_cap`` or ``timeout``: incomplete, never a
            # count). A malformed item, or a value the walk does not know, ends
            # it as ``bad_shape`` inside ``_item``. The ``Link`` header is
            # exposed by the fetch layer and deliberately ignored here.
            if not items:
                break
            smallest: int | None = None
            for item in items:
                key, outcome = _item(item, known_id, now)
                if smallest is None or key < smallest:
                    smallest = key
                if outcome == "future":
                    dropped += 1
                elif isinstance(outcome, InteractionRow):
                    if str(key) not in seen:
                        seen.add(str(key))
                        rows.append(outcome)
            if smallest is None:
                raise _Stop("bad_shape")
            if cursor is not None and smallest >= cursor:
                raise _Stop("partial_pages")  # no progress: the cursor must fall
            cursor = smallest
            remaining_calls = fetched.headers.get("x-ratelimit-remaining", "")
            if remaining_calls.isdigit() and int(remaining_calls) < LOW_RATE_LIMIT:
                raise _Stop("rate_limited")
        else:
            raise _Stop("page_cap")
        by_value = tuple(sorted(rows, key=lambda r: (r.boosts, r.favorites, r.replies)))
        return MastodonRead(followers, posts, by_value, calls, dropped)
    except _Stop as stop:
        return MastodonRead(followers, posts, NotProvided(stop.reason), calls, dropped)


def _item(
    item: object, account_id: str, now: datetime
) -> tuple[int, InteractionRow | str | None]:
    """One status: its numeric id and a row, ``future``, or None (not counted).

    None means the status is, by a well formed answer, not a counted post of the
    account: a boost, a post by another author, a reply to another account, a
    non-public post, or a post before the window. Anything that cannot be read
    as one of those or as an own post (not an object, a bad id, a bad shape of
    boost, author, reply or visibility, a bad time, a bad count) raises
    ``_Stop('bad_shape')``: a malformed status is never absence.
    """
    if not isinstance(item, dict):
        raise _Stop("bad_shape")
    raw_id = item.get("id")
    if type(raw_id) is not str or not _ID.fullmatch(raw_id):
        raise _Stop("bad_shape")
    key = int(raw_id)
    boosted = item.get("reblog")
    if boosted is not None:
        if not isinstance(boosted, dict):
            raise _Stop("bad_shape")
        return key, None  # a boost is not the account's own post
    author = item.get("account")
    author_id = author.get("id") if isinstance(author, dict) else None
    if type(author_id) is not str or not _ID.fullmatch(author_id):
        raise _Stop("bad_shape")  # who wrote it is unknown: it could be ours
    if author_id != account_id:
        return key, None
    parent = item.get("in_reply_to_account_id")
    if parent is not None and (type(parent) is not str or not _ID.fullmatch(parent)):
        raise _Stop("bad_shape")
    if item.get("in_reply_to_id") is not None and parent is None:
        raise _Stop("bad_shape")  # a reply whose target author is unknown
    if parent is not None and parent != account_id:
        return key, None  # a reply to another account is not counted
    visibility = item.get("visibility")
    if visibility in _HIDDEN:
        return key, None  # never counted, whatever the server returned
    if visibility not in _VISIBLE:
        raise _Stop("bad_shape")
    when = post_time(item, "created_at")
    placed = classify(when, now)
    if placed == "invalid":
        raise _Stop("bad_shape")
    if placed == "before":
        return (
            key,
            None,
        )  # out of the window (a pin comes out of order, still not counted)
    if placed == "future":
        return key, "future"
    boosts = _int(item.get("reblogs_count"), MAX_INTERACTIONS)
    favorites = _int(item.get("favourites_count"), MAX_INTERACTIONS)
    replies = _int(item.get("replies_count"), MAX_INTERACTIONS)
    if boosts is None or favorites is None or replies is None:
        raise _Stop("bad_shape")
    return key, InteractionRow(boosts, favorites, replies)


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


def profile_url(host: str, acct: str) -> str:
    """The owner's public profile link, for the presentation boundary only.

    Built from the checked config values when a block is rendered; it is never
    part of a stored snapshot.
    """
    return f"https://{check_host(host)}/@{check_acct(acct)}"


def _walk(value: object, depth: int) -> None:
    """Apply the guard to every key and leaf, however deep (nested dict, list, tuple).

    Raises ``ImportGuardError`` for a person-level key, a string that has an
    identifying shape (a URL, a handle or host with dots, an email, an @handle,
    an address, free text), a float, ``None`` or an object of any other type.
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
        if (
            len(value) > MAX_WORD
            or any(ch.isspace() for ch in value)
            or "://" in value
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
    exactly the three counts. A third pass checks that the fields agree with
    each other: the only legal combinations are

    ====================  ===============  =====================================
    complete              reason           interactions
    ====================  ===============  =====================================
    true                  absent           present, a list of 0 to 200 valid rows
                                           (an empty list means a read that
                                           finished and found no own post in the
                                           window, the one true zero)
    false                 present (closed  absent (no prior rows kept)
                          word)
    false                 present (closed  present, 1 to 200 valid rows (the prior
                          word)            rows, kept and stale; never an empty
                                           list, which would read as fresh zeros)
    ====================  ===============  =====================================

    Every other combination is refused. Raises ``ImportGuardError``.
    """
    _walk(snapshot, 0)
    check_names(snapshot.keys(), _TOP_KEYS)
    if snapshot.get("platform") != "mastodon":
        raise ImportGuardError(NOT_ANONYMOUS)
    as_of = snapshot.get("as_of")
    if type(as_of) is not str or not _AS_OF.fullmatch(as_of):
        raise ImportGuardError(NOT_ANONYMOUS)
    complete = snapshot.get("complete")
    if type(complete) is not bool:
        raise ImportGuardError(NOT_ANONYMOUS)
    for key in ("followers", "posts"):
        if key in snapshot and not _count_ok(snapshot[key], MAX_COUNT):
            raise ImportGuardError(NOT_ANONYMOUS)
    if "reason" in snapshot and not is_reason(snapshot["reason"]):
        raise ImportGuardError(NOT_ANONYMOUS)
    rows = snapshot.get("interactions", [])
    if not isinstance(rows, list) or len(rows) > MAX_POSTS:
        raise ImportGuardError(NOT_ANONYMOUS)
    for row in rows:
        if not isinstance(row, dict):
            raise ImportGuardError(NOT_ANONYMOUS)
        check_names(row.keys(), _ROW_KEYS)
        if set(row) != _ROW_KEYS or not all(
            _count_ok(v, MAX_INTERACTIONS) for v in row.values()
        ):
            raise ImportGuardError(NOT_ANONYMOUS)
    # The consistency pass: the table in the docstring, nothing else is legal.
    if complete:
        if "reason" in snapshot or "interactions" not in snapshot:
            raise ImportGuardError(NOT_ANONYMOUS)
    elif "reason" not in snapshot or ("interactions" in snapshot and not rows):
        raise ImportGuardError(NOT_ANONYMOUS)


def build_snapshot(
    read: MastodonRead,
    *,
    as_of: date,
    prior: Sequence[InteractionRow] | None = None,
) -> dict[str, object] | None:
    """The snapshot entry for the platform, or None when the guard refuses it.

    Anonymous by construction: no host, account, id or link goes in. An
    incomplete read keeps ``prior`` as the interactions (or leaves the key out
    when there is none) with ``complete`` false and the reason word; a complete
    read replaces them. Nothing here is a made-up zero.
    """
    kept, reason = keep_prior(
        read.rows if isinstance(read.rows, NotProvided) else list(read.rows), prior
    )
    snapshot: dict[str, object] = {
        "platform": "mastodon",
        "as_of": as_of.isoformat(),
        "complete": reason is None and read.complete,
    }
    if read.followers is not None:
        snapshot["followers"] = read.followers
    if read.posts is not None:
        snapshot["posts"] = read.posts
    # A kept-prior empty list on an incomplete read is left out: an incomplete
    # snapshot never carries an empty list that could read as fresh zeros.
    if kept is not None and (reason is None or len(kept) > 0):
        snapshot["interactions"] = [row.as_dict() for row in kept]
    if reason is not None:
        snapshot["reason"] = reason
    try:
        check_snapshot(snapshot)
    except ImportGuardError:
        return None
    return snapshot


def history_aggregates(read: MastodonRead) -> dict[str, int] | None:
    """The window sums for the history store, only for a complete read.

    ``posts_30d`` and the three sums (design 4.2). An incomplete read returns
    None: it writes nothing, and a zero is only ever a finished empty window.
    """
    if isinstance(read.rows, NotProvided):
        return None
    # Re-checked, not trusted from construction: a bad or oversized row list is
    # a refusal, never a sum.
    kept, reason = keep_prior(list(read.rows), None)
    if reason is not None or kept is None:
        return None
    return _sums(kept)


def _sums(rows: Sequence[InteractionRow]) -> dict[str, int]:
    return {
        "posts_30d": len(rows),
        "boosts_30d": sum(r.boosts for r in rows),
        "favorites_30d": sum(r.favorites for r in rows),
        "replies_30d": sum(r.replies for r in rows),
    }


def snapshot_aggregates(snapshot: Mapping[str, object]) -> dict[str, int] | None:
    """The window sums of a stored snapshot, or None.

    Refuses an incomplete snapshot and any contradictory one (the whole
    consistency table of ``check_snapshot``): only a complete, consistent
    snapshot is summed, so a stale or half-written one is never a zero.
    """
    try:
        check_snapshot(snapshot)
    except ImportGuardError:
        return None
    rows = snapshot.get("interactions")
    if snapshot.get("complete") is not True or not isinstance(rows, list):
        return None
    return _sums(
        [InteractionRow(r["boosts"], r["favorites"], r["replies"]) for r in rows]
    )


def entry_from_snapshot(
    snapshot: Mapping[str, object], *, host: str, acct: str
) -> FediverseEntry | None:
    """The ``kit.fediverse`` entry for a fresh complete snapshot, else None.

    ``host`` and ``acct`` come from config, not from the snapshot (which is
    anonymous): the re-check link is built here, at the presentation boundary.
    An incomplete snapshot returns None, so it never replaces a hand-kept entry.
    """
    try:
        check_snapshot(snapshot)  # the whole consistency table, not one flag
    except ImportGuardError:
        return None
    rows = snapshot.get("interactions")
    if snapshot.get("complete") is not True or not isinstance(rows, list):
        return None
    payload: dict[str, object] = {
        key: snapshot[key]
        for key in ("platform", "as_of", "followers", "posts")
        if key in snapshot
    }
    payload["profile_url"] = profile_url(host, acct)
    payload["interactions"] = rows
    return FediverseEntry.model_validate(payload)


def utc_day(now: datetime) -> date:
    """The UTC date of the run instant (the snapshot's ``as_of``)."""
    return now.astimezone(UTC).date()
