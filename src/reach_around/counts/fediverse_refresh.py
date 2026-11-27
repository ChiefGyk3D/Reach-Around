# SPDX-License-Identifier: Apache-2.0
"""Run the configured fediverse collectors once (#940, daily from ``counts refresh``).

Rules, the same as the numbers refresh: a source that cannot be read in full keeps
its previous snapshot untouched and is reported by a fixed reason word; nothing is
written as zero. Every snapshot passes its provider's guard (``build_snapshot``,
which runs ``check_snapshot``) before it can be stored, and the history aggregates
are taken from that guarded snapshot, never from the raw read. Time, transport and
sleep are injected so a test never opens a socket or waits.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from reach_around.config.fediverse_sources import (
    BlueskyFediverseSource,
    FediverseSource,
    MastodonFediverseSource,
)
from reach_around.counts import bluesky_feed, mastodon_feed
from reach_around.counts.refresh import Outcome
from reach_around.net.egress import Transport

CollectBluesky = Callable[..., bluesky_feed.BlueskyRead]
CollectMastodon = Callable[..., mastodon_feed.MastodonRead]


@dataclass(frozen=True)
class FediverseResult:
    """What one run produced: snapshots to store, aggregates to append, outcomes."""

    snapshots: dict[str, dict[str, object]]
    aggregates: dict[str, dict[str, int]]
    outcomes: list[Outcome]


def platform_key(kind: str) -> str:
    """The key of a fediverse source in the status file (never a handle)."""
    return f"fediverse-{kind}"


def _failed(kind: str, word: str) -> Outcome:
    return Outcome(platform_key(kind), "fediverse", "error", None, word)


def _read_bluesky(
    source: BlueskyFediverseSource,
    now: datetime,
    transport: Transport | None,
    sleep: Callable[[float], None],
    collect: CollectBluesky,
) -> tuple[dict[str, object] | None, dict[str, int] | None, str | None]:
    read = collect(source.actor, now=now, transport=transport, sleep=sleep)
    snapshot = bluesky_feed.build_snapshot(read, as_of=bluesky_feed.utc_day(now))
    sums = None if snapshot is None else bluesky_feed.snapshot_aggregates(snapshot)
    return snapshot, sums, read.reason


def _read_mastodon(
    source: MastodonFediverseSource,
    now: datetime,
    transport: Transport | None,
    sleep: Callable[[float], None],
    collect: CollectMastodon,
) -> tuple[dict[str, object] | None, dict[str, int] | None, str | None]:
    read = collect(
        source.host,
        source.acct,
        account_id=source.account_id,
        now=now,
        transport=transport,
        sleep=sleep,
    )
    snapshot = mastodon_feed.build_snapshot(read, as_of=mastodon_feed.utc_day(now))
    sums = None if snapshot is None else mastodon_feed.snapshot_aggregates(snapshot)
    return snapshot, sums, read.reason


def run(
    sources: list[FediverseSource],
    *,
    now: datetime,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    collect_bluesky: CollectBluesky = bluesky_feed.collect,
    collect_mastodon: CollectMastodon = mastodon_feed.collect,
) -> FediverseResult:
    """Collect each source. Never raises for a remote failure or a bad read."""
    snapshots: dict[str, dict[str, object]] = {}
    aggregates: dict[str, dict[str, int]] = {}
    outcomes: list[Outcome] = []
    for source in sources:
        kind = source.kind
        try:
            if isinstance(source, BlueskyFediverseSource):
                snapshot, sums, reason = _read_bluesky(
                    source, now, transport, sleep, collect_bluesky
                )
            else:
                snapshot, sums, reason = _read_mastodon(
                    source, now, transport, sleep, collect_mastodon
                )
        except Exception as exc:  # the class only; text could hold anything
            outcomes.append(_failed(kind, type(exc).__name__))
            continue
        if snapshot is None:
            outcomes.append(_failed(kind, "guard"))
        elif reason is not None:
            outcomes.append(_failed(kind, reason))  # incomplete: the prior is kept
        elif sums is None:
            outcomes.append(_failed(kind, "bad_shape"))
        else:
            snapshots[kind] = snapshot
            aggregates[kind] = sums
            outcomes.append(
                Outcome(platform_key(kind), "fediverse", "ok", sums["posts_30d"], None)
            )
    return FediverseResult(snapshots, aggregates, outcomes)
