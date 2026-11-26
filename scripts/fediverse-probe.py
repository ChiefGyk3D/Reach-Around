#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Read-only live probe for the fediverse collector's INFERRED assumptions.

Run by hand, by the owner, against a named public endpoint. It is never run by
CI or by the tests (the tests call ``run`` with a fake transport). Importing
this file makes no call.

Usage:
  python3 -I scripts/fediverse-probe.py mastodon --host social.example --acct example
  python3 -I scripts/fediverse-probe.py bluesky \
      --host public.api.bsky.app --actor example.bsky.example

Each probe makes at most three GET requests (one pause of 1 second between
them), no login, no token. It prints only: status codes, the NAMES of response
headers, counts, and present or absent for a fixed list of expected field
names. It never prints a body, a header value, a post id, a handle or a URL.
Paste the output into the design's INFERRED checklist (section 14).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from typing import TextIO

from reach_around.net.egress import (
    EgressBlocked,
    HttpsTransport,
    Transport,
    TransportRequest,
    validate_hostname,
)

MAX_BYTES = 1_048_576
TIMEOUT = 10.0
USER_AGENT = "reach-around-probe/1"
MASTODON_FIELDS = (
    "replies_count",
    "reblogs_count",
    "favourites_count",
    "created_at",
    "visibility",
    "in_reply_to_account_id",
    "reblog",
)
BLUESKY_FIELDS = ("likeCount", "repostCount", "replyCount", "quoteCount", "indexedAt")
SAFE_PATH_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._:"
)


def _safe(value: str, what: str) -> str:
    if not value or len(value) > 253 or not set(value) <= SAFE_PATH_CHARS:
        raise SystemExit(f"{what} has characters this probe will not send")
    return value


def _get(
    transport: Transport, host: str, path: str, out: TextIO, label: str
) -> tuple[int, object]:
    """One GET. Prints status and header names; returns (status, parsed or None)."""
    request = TransportRequest(
        host=host,
        path=path,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "identity",
        },
        deadline=time.monotonic() + TIMEOUT,
        max_bytes=MAX_BYTES,
    )
    try:
        response = transport(request)
    except EgressBlocked as exc:
        print(f"{label}: blocked {exc.reason}", file=out)
        return 0, None
    names = sorted(
        name
        for name in response.headers
        if name.isascii() and name.replace("-", "").isalnum() and len(name) <= 64
    )
    print(f"{label}: status {response.status}", file=out)
    print(f"{label}: header names {', '.join(names)}", file=out)
    parsed: object = None
    if len(response.body) <= MAX_BYTES:
        try:
            parsed = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            parsed = None
    print(f"{label}: body bytes {len(response.body)}", file=out)
    return response.status, parsed


def _field_report(
    items: Sequence[object], fields: Sequence[str], label: str, out: TextIO
) -> None:
    dicts = [item for item in items if isinstance(item, dict)]
    for name in fields:
        have = sum(1 for item in dicts if name in item)
        print(f"{label}: field {name} present in {have} of {len(dicts)}", file=out)


def probe_mastodon(
    transport: Transport,
    host: str,
    acct: str,
    out: TextIO,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    host = validate_hostname(host)
    acct = _safe(acct, "acct")
    status, account = _get(
        transport, host, f"/api/v1/accounts/lookup?acct={acct}", out, "lookup"
    )
    account_id = account.get("id") if isinstance(account, dict) else None
    numeric = (
        isinstance(account_id, str) and account_id.isdigit() and len(account_id) <= 30
    )
    if not numeric:
        print(f"lookup: usable numeric id no (status {status})", file=out)
        return
    print("lookup: usable numeric id yes", file=out)
    sleep(1.0)
    query = "limit=40&exclude_replies=true&exclude_reblogs=true"
    base = f"/api/v1/accounts/{account_id}/statuses"
    status, page = _get(transport, host, f"{base}?{query}", out, "statuses")
    if not isinstance(page, list):
        print("statuses: array no", file=out)
        return
    print(f"statuses: array yes, {len(page)} items", file=out)
    dicts = [p for p in page if isinstance(p, dict)]
    _field_report(page, MASTODON_FIELDS, "statuses", out)
    for visibility in ("public", "unlisted"):
        count = sum(1 for p in dicts if p.get("visibility") == visibility)
        print(f"statuses: visibility {visibility} {count}", file=out)
    boosts = sum(1 for p in dicts if p.get("reblog") is not None)
    replies = sum(
        1 for p in dicts if p.get("in_reply_to_account_id") not in (None, account_id)
    )
    print(f"statuses: boosts despite exclude_reblogs {boosts}", file=out)
    print(f"statuses: replies to others despite exclude_replies {replies}", file=out)
    sleep(1.0)
    _get(transport, host, f"{base}?limit=1", out, "id-only")


def probe_bluesky(
    transport: Transport,
    host: str,
    actor: str,
    out: TextIO,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    host = validate_hostname(host)
    actor = _safe(actor, "actor")
    profile = f"/xrpc/app.bsky.actor.getProfile?actor={actor}"
    _get(transport, host, profile, out, "profile")
    sleep(1.0)
    query = f"actor={actor}&limit=50&filter=posts_no_replies&includePins=true"
    status, body = _get(
        transport, host, f"/xrpc/app.bsky.feed.getAuthorFeed?{query}", out, "feed"
    )
    feed = body.get("feed") if isinstance(body, dict) else None
    if not isinstance(feed, list):
        print("feed: list no", file=out)
        return
    print(f"feed: list yes, {len(feed)} items", file=out)
    posts = [e.get("post") for e in feed if isinstance(e, dict)]
    objects = [p for p in posts if isinstance(p, dict)]
    _field_report(objects, BLUESKY_FIELDS, "feed", out)
    reasons = sum(1 for e in feed if isinstance(e, dict) and "reason" in e)
    print(f"feed: entries with a reason {reasons}", file=out)
    has_cursor = isinstance(body, dict) and "cursor" in body
    print(f"feed: cursor {'present' if has_cursor else 'absent'}", file=out)


def run(
    argv: Sequence[str],
    *,
    transport: Transport | None = None,
    out: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0] if __doc__ else ""
    )
    sub = parser.add_subparsers(dest="platform", required=True)
    masto = sub.add_parser("mastodon")
    masto.add_argument("--host", required=True)
    masto.add_argument("--acct", required=True)
    sky = sub.add_parser("bluesky")
    sky.add_argument("--host", required=True)
    sky.add_argument("--actor", required=True)
    args = parser.parse_args(list(argv))
    sink = out if out is not None else sys.stdout
    send = transport if transport is not None else HttpsTransport()
    try:
        if args.platform == "mastodon":
            probe_mastodon(send, args.host, args.acct, sink, sleep)
        else:
            probe_bluesky(send, args.host, args.actor, sink, sleep)
    except EgressBlocked as exc:
        print(f"refused: {exc.reason}", file=sink)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
