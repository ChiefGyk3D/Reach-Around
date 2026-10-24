# SPDX-License-Identifier: Apache-2.0
"""Run the configured sources and update the numbers and status files.

Rules: a failed source keeps its previous number untouched and is recorded in
the status file by error class and time only; a failure is never written as
zero; platforms in the numbers file that have no source (manual values) are
left exactly as they are.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reach_around.config.numbers_sources import (
    BlueskySource,
    DiscordSource,
    GithubSource,
    MastodonSource,
    MatrixSource,
    NumbersConfig,
    TwitchSource,
    YoutubeSource,
)
from reach_around.counts.providers import (
    PROVIDERS,
    Context,
    SourceError,
    SourceSkip,
)
from reach_around.net.egress import Transport
from reach_around.net.fetch import FetchError, fetch_json
from reach_around.numbers import (
    Numbers,
    NumbersError,
    PlatformCount,
    read_capped,
    utc_stamp,
)

Source = (
    BlueskySource
    | MastodonSource
    | GithubSource
    | DiscordSource
    | MatrixSource
    | YoutubeSource
    | TwitchSource
)

_FIXED_HOSTS = {
    "bluesky": "public.api.bsky.app",
    "github": "api.github.com",
    "discord": "discord.com",
    "youtube": "www.googleapis.com",
    "twitch": "id.twitch.tv, api.twitch.tv",
}


def host_of(source: Source) -> str:
    """The host(s) a source talks to. Never holds a secret."""
    if isinstance(source, MastodonSource):
        return source.host
    if isinstance(source, MatrixSource):
        return source.room.split(":", 1)[1].lower() + " (and its client API host)"
    return _FIXED_HOSTS[source.kind]


# A good number is not replaced by a suspicious one without an explicit
# --accept-drop. A zero over a positive number, or a fall of more than
# DROP_PERCENT percent from a previous value of at least DROP_MIN_PREVIOUS, is
# far more likely a glitching API than a real change.
DROP_MIN_PREVIOUS = 1000
DROP_PERCENT = 90
ZERO_SUSPECT = "zero_suspect"
DROP_SUSPECT = "drop_suspect"
SUSPECT_KINDS = (ZERO_SUSPECT, DROP_SUSPECT)


def suspect_kind(previous: PlatformCount | None, count: int) -> str | None:
    """A ``*_suspect`` word if ``count`` should not replace ``previous``."""
    if previous is None or previous.count == 0:
        return None
    if count == 0:
        return ZERO_SUSPECT
    if previous.count >= DROP_MIN_PREVIOUS and count * 100 < previous.count * (
        100 - DROP_PERCENT
    ):
        return DROP_SUSPECT
    return None


@dataclass(frozen=True)
class Outcome:
    platform: str
    kind: str
    status: str  # "ok", "error" or "skipped"
    count: int | None = None
    detail: str | None = None  # error class or skip reason, nothing else


def select(config: NumbersConfig, only: list[str] | None) -> list[Source]:
    """The sources to run; unknown ``only`` slugs raise ``ValueError``."""
    if not only:
        return list(config.sources)
    known = {source.platform for source in config.sources}
    unknown = sorted(set(only) - known)
    if unknown:
        raise ValueError(f"--only names no configured source: {', '.join(unknown)}")
    return [source for source in config.sources if source.platform in set(only)]


def refresh(
    sources: list[Source],
    previous: Numbers,
    *,
    env: Mapping[str, str],
    transport: Transport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    fetch: Callable[..., dict[str, object]] = fetch_json,
    sleep: Callable[[float], None] | None = None,
    accept_drop: frozenset[str] = frozenset(),
) -> tuple[Numbers, list[Outcome]]:
    """Fetch every source; return the new numbers and one outcome per source."""
    numbers: Numbers = dict(previous)
    outcomes: list[Outcome] = []

    def call(url: str, **kwargs: Any) -> dict[str, object]:
        if transport is not None:
            kwargs["transport"] = transport
        if sleep is not None:
            kwargs["sleep"] = sleep
        return fetch(url, **kwargs)

    context = Context(fetch=call, env=env)
    for source in sources:
        provider = PROVIDERS[source.kind]
        try:
            count = provider(source, context)
        except SourceSkip as skip:
            outcomes.append(
                Outcome(source.platform, source.kind, "skipped", None, skip.reason)
            )
        except (FetchError, SourceError) as err:
            outcomes.append(
                Outcome(source.platform, source.kind, "error", None, err.kind)
            )
        except Exception as exc:  # report the class only; text could hold anything
            outcomes.append(
                Outcome(source.platform, source.kind, "error", None, type(exc).__name__)
            )
        else:
            suspect = (
                None
                if source.platform in accept_drop
                else suspect_kind(previous.get(source.platform), count)
            )
            if suspect is not None:
                outcomes.append(
                    Outcome(source.platform, source.kind, "error", None, suspect)
                )
                continue
            numbers[source.platform] = PlatformCount(count, utc_stamp(now()))
            outcomes.append(Outcome(source.platform, source.kind, "ok", count))
    return numbers, outcomes


def load_status(path: Path) -> dict[str, dict[str, str]]:
    """The previous per-source status, or an empty mapping if absent or damaged."""
    try:
        raw = json.loads(read_capped(path))
    except (NumbersError, ValueError, RecursionError):
        return {}
    sources = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(sources, dict):
        return {}
    clean: dict[str, dict[str, str]] = {}
    for slug, entry in sources.items():
        if isinstance(entry, dict) and all(
            isinstance(entry.get(key), str) for key in ("status", "at")
        ):
            item = {"status": str(entry["status"])[:40], "at": str(entry["at"])[:30]}
            if isinstance(entry.get("detail"), str):
                item["detail"] = str(entry["detail"])[:60]
            clean[str(slug)[:40]] = item
    return clean


def render_status(
    previous: dict[str, dict[str, str]], outcomes: list[Outcome], moment: datetime
) -> str:
    """Status text: for each source only status, error class or reason, and time."""
    at = utc_stamp(moment)
    sources = dict(previous)
    for outcome in outcomes:
        item = {"status": outcome.status, "at": at}
        if outcome.detail is not None:
            item["detail"] = outcome.detail
        sources[outcome.platform] = item
    return (
        json.dumps({"version": 1, "sources": dict(sorted(sources.items()))}, indent=2)
        + "\n"
    )
