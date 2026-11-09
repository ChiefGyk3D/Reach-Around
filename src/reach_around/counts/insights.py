# SPDX-License-Identifier: Apache-2.0
"""Optional aggregate views and engagement numbers (issues 394 and 395).

Runs only for a source whose ``insights`` setting is exactly ``True`` (off by
default; the vendor behavior is partly UNVERIFIED, see docs/engagement-sources.md)
and only when the follower read of the same source succeeded in this run.

Every figure is a whole number over a stated window with a stated formula, stored
as a number in the history file and nowhere else. These metrics are OWNER REPORT
ONLY (``OWNER_REPORT_ONLY``): no kit, link page or public file may show them until
the display work (#397) lands with its floors, and the build never reads them.
Failures are fixed words; no token, URL or response text reaches a log.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from reach_around.config.numbers_sources import ThreadsSource, TiktokSource
from reach_around.counts.meta_user import MetaError, threads_insights
from reach_around.counts.tiktok_user import TiktokError, video_stats
from reach_around.net.fetch import FetchError

Fetch = Callable[..., dict[str, object]]

# metric -> (what it adds up, window); the text goes into the history note
METRICS: dict[tuple[str, str], str] = {
    ("tiktok", "videos_28d"): "count of public videos posted in the last 28 UTC days",
    (
        "tiktok",
        "video_views_28d",
    ): "sum of lifetime view_count of public videos posted in the last 28 UTC days",
    (
        "tiktok",
        "video_engagement_28d",
    ): "sum of lifetime likes+comments+shares, videos posted in last 28 UTC days",
    ("threads", "views_7d"): "sum of daily views, 7 UTC days before the run day",
    (
        "threads",
        "engagement_7d",
    ): "likes+replies+reposts+quotes, 7 UTC days before the run day",
}
OWNER_REPORT_ONLY = frozenset(name for _, name in METRICS)

_WORDS = frozenset(
    {
        "bad_credential_name",
        "bad_auto_refresh",
        "bad_source_setting",
        "missing_credential",
        "invalid_credential",
        "token_expired",
        "token_rejected",
        "scope_missing",
        "api_error",
        "missing_field",
        "window_incomplete",
        "refresh_failed",
        "redirect",
        "too_large",
        "bad_json",
        "content_type",
        "rate_limited",
        "server_error",
        "bad_host",
    }
)

_HTTP_WORD = re.compile(r"http_[45][0-9]{2}", re.ASCII)


@dataclass(frozen=True)
class Figure:
    platform: str
    metric: str
    value: int
    note: str


@dataclass(frozen=True)
class InsightFailure:
    platform: str
    word: str


def _word(exc: Exception) -> str:
    kind = getattr(exc, "kind", "")
    if type(kind) is str and (kind in _WORDS or _HTTP_WORD.fullmatch(kind)):
        return kind
    return "fetch_failed"


def collect(
    sources: list[Any],
    ok_platforms: frozenset[str],
    env: Mapping[str, str],
    fetch: Fetch,
    now: datetime,
) -> tuple[list[Figure], list[InsightFailure]]:
    """Figures for each opted-in source whose follower read succeeded."""
    figures: list[Figure] = []
    failures: list[InsightFailure] = []
    stamp = now.astimezone(UTC)
    for source in sources:
        if not isinstance(source, TiktokSource | ThreadsSource):
            continue
        if getattr(source, "insights", False) is not True:
            continue
        if source.platform not in ok_platforms:
            continue
        try:
            if isinstance(source, TiktokSource):
                numbers = video_stats(source, env, fetch, int(stamp.timestamp()))
            else:
                numbers = threads_insights(source, env, fetch, stamp.date())
        except (FetchError, TiktokError, MetaError) as exc:
            failures.append(InsightFailure(source.platform, _word(exc)))
            continue
        except Exception:
            failures.append(InsightFailure(source.platform, "fetch_failed"))
            continue
        for metric, value in sorted(numbers.items()):
            note = METRICS.get((source.kind, metric))
            if note is not None:
                figures.append(Figure(source.platform, metric, value, note))
    return figures, failures


__all__ = ["OWNER_REPORT_ONLY", "Figure", "InsightFailure", "collect"]
