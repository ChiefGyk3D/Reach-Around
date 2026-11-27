# SPDX-License-Identifier: Apache-2.0
"""The stored fediverse snapshots, the kit feed and the egress check (#940).

``numbers.fediverse.json`` sits beside the numbers file and holds, per platform,
one anonymous snapshot (the three counts of each public post in the window, two
profile counts, a date and a flag: see ``bluesky_feed.build_snapshot``). Only a
complete snapshot is ever stored; an incomplete read leaves the stored one exactly
as it was, with its own date. Every snapshot passes its provider's guard on the
way in and on the way out, so a damaged or hand-edited file is dropped, never
trusted. The account and the instance are config, never part of the file.

``apply_collected`` feeds ``kit.fediverse`` from the stored snapshots. The hand-kept
entries stay as they are unless a fresh snapshot exists for the same platform.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from pathlib import Path

from reach_around.config import ConfigError, SiteConfig
from reach_around.config.fediverse import WINDOW_DAYS, FediverseEntry
from reach_around.config.fediverse_sources import (
    BlueskyFediverseSource,
    FediverseSource,
    MastodonFediverseSource,
)
from reach_around.config.import_guard import ImportGuardError
from reach_around.config.loader import _read
from reach_around.counts import bluesky_feed, mastodon_feed

MAX_SNAPSHOT_BYTES = 512 * 1024
VERSION = 1
KINDS = ("bluesky", "mastodon")
BLUESKY_ENDPOINT = f"{bluesky_feed.HOST}:443"

Snapshots = dict[str, dict[str, object]]

_CHECK: dict[str, Callable[[Mapping[str, object]], None]] = {
    "bluesky": bluesky_feed.check_snapshot,
    "mastodon": mastodon_feed.check_snapshot,
}


def snapshot_path(numbers: Path) -> Path:
    """The snapshot file that belongs to a numbers file."""
    return numbers.with_name(f"{numbers.stem}.fediverse.json")


def _valid(kind: str, snapshot: object) -> bool:
    """True only for a snapshot of this platform that passes its guard."""
    if kind not in _CHECK or not isinstance(snapshot, dict):
        return False
    try:
        _CHECK[kind](snapshot)
    except ImportGuardError:
        return False
    return snapshot.get("platform") == kind and snapshot.get("complete") is True


def load_snapshots(path: Path) -> Snapshots:
    """The stored snapshots; absent, damaged or hostile content gives ``{}`` or
    keeps only the entries that pass the guard."""
    try:
        raw = json.loads(_read(path, MAX_SNAPSHOT_BYTES))
    except (ConfigError, ValueError, RecursionError):
        return {}
    platforms = raw.get("platforms") if isinstance(raw, dict) else None
    if not isinstance(platforms, dict) or raw.get("version") != VERSION:
        return {}
    return {
        kind: snapshot
        for kind, snapshot in platforms.items()
        if type(kind) is str and _valid(kind, snapshot)
    }


def dump_snapshots(snapshots: Mapping[str, Mapping[str, object]]) -> str:
    """The file text. Refuses (``ConfigError``) any snapshot that is not complete
    and clean, so what is written is exactly what ``load_snapshots`` accepts."""
    for kind, snapshot in snapshots.items():
        if not _valid(kind, snapshot):
            raise ConfigError(
                "the fediverse snapshots to write",
                None,
                "a snapshot is not a complete, anonymous aggregate",
            )
    body = {
        "version": VERSION,
        "platforms": {kind: dict(snapshots[kind]) for kind in sorted(snapshots)},
    }
    return json.dumps(body, indent=2) + "\n"


def _entry(source: FediverseSource, snapshot: Mapping[str, object]) -> FediverseEntry:
    if isinstance(source, BlueskyFediverseSource):
        found = bluesky_feed.entry_from_snapshot(snapshot, actor=source.actor)
    else:
        found = mastodon_feed.entry_from_snapshot(
            snapshot, host=source.host, acct=source.acct
        )
    if found is None:
        raise ValueError("not a fresh complete snapshot")
    return found


def apply_collected(config: SiteConfig, path: Path, today: date) -> SiteConfig:
    """``config`` with ``kit.fediverse`` fed from the stored snapshots.

    A platform with a configured source and a fresh complete snapshot (its
    ``as_of`` no more than ``WINDOW_DAYS`` days before ``today`` and not in the
    future) gets the collected entry; ``show_mean`` is kept from the typed entry.
    Any other platform keeps its typed entry unchanged, and so does every platform
    when there is no source, no file or a damaged one: the hand-kept path wins
    when no collected data exists.
    """
    numbers = config.numbers
    if numbers is None or not numbers.fediverse or config.kit is None:
        return config
    stored = load_snapshots(path)
    if not stored:
        return config
    entries = list(config.kit.fediverse)
    changed = False
    for source in numbers.fediverse:
        snapshot = stored.get(source.kind)
        if snapshot is None:
            continue
        try:
            fresh = _entry(source, snapshot)
        except ValueError:
            continue
        age = (today - fresh.as_of).days
        if age < 0 or age > WINDOW_DAYS:
            continue
        typed = next((e for e in entries if e.platform == source.kind), None)
        if typed is not None and typed.show_mean:
            fresh = fresh.model_copy(update={"show_mean": True})
        # Set after validation, never from the config: the label of the numbers.
        fresh._collected = True  # noqa: SLF001 (the model's own private flag)
        entries = [e for e in entries if e.platform != source.kind] + [fresh]
        changed = True
    if not changed:
        return config
    kit = config.kit.model_copy(update={"fediverse": entries})
    return config.model_copy(update={"kit": kit})


def feed_kit(
    config: SiteConfig,
    config_path: Path,
    numbers_path: Path | None,
    today: date,
    *,
    published: Path | None = None,
) -> SiteConfig:
    """``apply_collected`` with the snapshot file that sits beside the numbers file
    the build uses (``numbers.json`` beside the config by default). A file inside
    the published folder is never read."""
    base = (
        numbers_path
        if numbers_path is not None
        else config_path.parent / "numbers.json"
    )
    path = snapshot_path(base)
    if published is not None:
        out = published.resolve()
        resolved = path.resolve()
        if resolved == out or out in resolved.parents:
            return config
    return apply_collected(config, path, today)


_ENDPOINT_SPLIT = re.compile(r"[\s,]+")


def required_endpoints(sources: Iterable[FediverseSource]) -> tuple[str, ...]:
    """The host and port each configured source needs, ``host:443``, sorted."""
    needed: set[str] = set()
    for source in sources:
        if isinstance(source, BlueskyFediverseSource):
            needed.add(BLUESKY_ENDPOINT)
        elif isinstance(source, MastodonFediverseSource):
            needed.add(f"{source.host}:443")
    return tuple(sorted(needed))


def missing_endpoints(
    sources: Iterable[FediverseSource], allowed_text: str
) -> tuple[str, ...]:
    """The needed endpoints that ``allowed_text`` (the workflow's
    ``allowed-endpoints`` value: spaces, commas or newlines between entries)
    does not list. Exact match, no wildcard, case-insensitive."""
    allowed = {
        part.lower() for part in _ENDPOINT_SPLIT.split(allowed_text) if part.strip()
    }
    return tuple(e for e in required_endpoints(sources) if e not in allowed)
