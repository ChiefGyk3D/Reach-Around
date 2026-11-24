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
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from reach_around.config.numbers_sources import (
    MAX_SOURCES,
    InstagramSource,
    ThreadsSource,
    TiktokSource,
    YoutubeSource,
)
from reach_around.counts.history import (
    ANALYTICS_PROVIDER,
    INSTAGRAM_INSIGHTS_PROVIDER,
    MAX_VALUE,
)
from reach_around.counts.instagram_insights import (
    InstagramInsightsError,
    collect_instagram,
)
from reach_around.counts.meta_user import MetaError, threads_insights
from reach_around.counts.tiktok_user import TiktokError, video_stats
from reach_around.counts.youtube_analytics import (
    YoutubeAnalyticsError,
    collect_youtube,
)
from reach_around.net.fetch import TIMEOUT as FETCH_TIMEOUT
from reach_around.net.fetch import FetchError

Fetch = Callable[..., dict[str, object]]

# Per-run caps, checked where the work happens (a source built without model
# validation must not widen them): sources looked at, requests sent, seconds.
MAX_REQUESTS = 300
DEADLINE_SECONDS = 600.0

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
    (
        "threads",
        "views_7d",
    ): "sum of daily views, end_time checked within 7 UTC days before the run day",
    (
        "threads",
        "views_vendor_window",
    ): "views total_value; window vendor-defined (since/until sent, unverified)",
    (
        "threads",
        "engagement_7d",
    ): "likes+replies+reposts+quotes totals; since/until sent, window vendor-defined",
}
OWNER_REPORT_ONLY = frozenset(name for _, name in METRICS)
# YouTube Analytics (#391) is the owner's authorized data: private, never shown
# publicly. Its figure names (views_1d, watch_minutes_1d, demo_*, geo_*) are
# produced by youtube_analytics.py; its notes state each window and the folding
# (which reduces noise and does not stop differencing across stored windows).
# Instagram insights (#393) are private in the same way: figures from
# instagram_insights.py carry the provider tag ``instagram_insights``.


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
        "window_mismatch",
        "bad_aggregate",
        "refresh_failed",
        "redirect",
        "too_large",
        "bad_json",
        "content_type",
        "rate_limited",
        "server_error",
        "bad_host",
        "no_data",
        "bad_report",
        "bad_row",
        "refresh_pending",
        "budget_exhausted",
    }
)

_HTTP_WORD = re.compile(r"http_[45][0-9]{2}", re.ASCII)


@dataclass(frozen=True)
class Figure:
    platform: str
    metric: str
    value: int
    note: str
    # A figure about a past UTC day carries that day; None means the run time.
    as_of_utc: str | None = None
    # Provenance, set by the collector that made the figure. It is never inferred
    # from ``platform``, which is a display slug the owner may rename.
    api_provider: str | None = None


@dataclass(frozen=True)
class InsightFailure:
    platform: str
    word: str


def _word(exc: Exception) -> str:
    kind = getattr(exc, "kind", "")
    if type(kind) is str and (kind in _WORDS or _HTTP_WORD.fullmatch(kind)):
        return kind
    return "fetch_failed"


def _youtube(
    source: YoutubeSource,
    ok_platforms: frozenset[str],
    env: Mapping[str, str],
    fetch: Fetch,
    stamp: datetime,
    figures: list[Figure],
    failures: list[InsightFailure],
) -> None:
    """Private YouTube Analytics figures; opt-in by ``analytics: true``."""
    if source.analytics is not True or source.platform not in ok_platforms:
        return
    try:
        measures, words = collect_youtube(source, env, fetch, stamp.date())
    except YoutubeAnalyticsError as exc:
        failures.append(InsightFailure(source.platform, _word(exc)))
        return
    except Exception:
        failures.append(InsightFailure(source.platform, "fetch_failed"))
        return
    failures.extend(InsightFailure(source.platform, word) for word in words)
    figures.extend(
        Figure(
            source.platform,
            m.metric,
            m.value,
            m.note,
            m.as_of_utc,
            ANALYTICS_PROVIDER,  # always, whatever the platform slug is
        )
        for m in measures
    )


def _instagram(
    source: InstagramSource,
    ok_platforms: frozenset[str],
    env: Mapping[str, str],
    fetch: Fetch,
    stamp: datetime,
    figures: list[Figure],
    failures: list[InsightFailure],
) -> None:
    """Private Instagram insights; opt-in by ``analytics: true`` (#393)."""
    if source.analytics is not True or source.platform not in ok_platforms:
        return
    try:
        measures, words = collect_instagram(source, env, fetch, stamp.date())
    except InstagramInsightsError as exc:
        failures.append(InsightFailure(source.platform, _word(exc)))
        return
    except Exception:
        failures.append(InsightFailure(source.platform, "fetch_failed"))
        return
    failures.extend(InsightFailure(source.platform, word) for word in words)
    figures.extend(
        Figure(
            source.platform,
            m.metric,
            m.value,
            m.note,
            m.as_of_utc,
            INSTAGRAM_INSIGHTS_PROVIDER,  # always, whatever the platform slug is
        )
        for m in measures
    )


class _BudgetSpent(BaseException):  # noqa: N818
    """The run's request count or time is used up; fixed word, no detail.

    A BaseException on purpose: the collectors turn every ``Exception`` into a
    fixed failure word (to keep tokens out of messages), which would report a
    spent budget as ``fetch_failed``. ``collect`` is the only place that catches
    this and reports it once.
    """

    kind = "budget_exhausted"


class _Budget:
    """Caps for one run, checked at use time (a built model may skip validation)."""

    def __init__(
        self, requests: int, seconds: float, clock: Callable[[], float]
    ) -> None:
        self.left = requests
        self.clock = clock
        self.deadline = clock() + seconds
        self.spent = False

    def check(self) -> None:
        if self.left <= 0 or self.clock() >= self.deadline:
            self.spent = True
            raise _BudgetSpent

    def wrap(self, fetch: Fetch) -> Fetch:
        def counted(*args: Any, **kwargs: Any) -> dict[str, object]:
            self.check()
            self.left -= 1
            # A request may not run past the run's deadline: its own time limit
            # is cut to what is left (the fetch layer enforces it as one total
            # limit over connect, send and read).
            own = kwargs.get("timeout", FETCH_TIMEOUT)
            kwargs["timeout"] = max(0.001, min(own, self.deadline - self.clock()))
            return fetch(*args, **kwargs)

        return counted


def collect(
    sources: list[Any],
    ok_platforms: frozenset[str],
    env: Mapping[str, str],
    fetch: Fetch,
    now: datetime,
    *,
    max_sources: int = MAX_SOURCES,
    max_requests: int = MAX_REQUESTS,
    deadline_seconds: float = DEADLINE_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[Figure], list[InsightFailure]]:
    """Figures for each opted-in source whose follower read succeeded.

    At most ``max_sources`` sources are looked at, at most ``max_requests``
    requests are sent and the run stops at the deadline; whatever is left out is
    reported as ``budget_exhausted``, and the figures already gathered are kept.
    """
    figures: list[Figure] = []
    failures: list[InsightFailure] = []
    stamp = now.astimezone(UTC)
    budget = _Budget(max_requests, deadline_seconds, clock)
    fetch = budget.wrap(fetch)
    opted = [s for s in sources if _opted_in(s)]
    for index, source in enumerate(opted):
        if index >= max_sources or budget.spent or clock() >= budget.deadline:
            failures.extend(
                InsightFailure(s.platform, "budget_exhausted")
                for s in opted[index:]
                if s.platform in ok_platforms
            )
            break
        try:
            _collect_one(source, ok_platforms, env, fetch, stamp, figures, failures)
        except _BudgetSpent:
            budget.spent = True  # reported once just below
        if budget.spent and source.platform in ok_platforms:
            failures.append(InsightFailure(source.platform, "budget_exhausted"))
    return figures, failures


def _opted_in(source: Any) -> bool:
    """True for a source type this layer collects for, with its opt-in set."""
    if isinstance(source, YoutubeSource | InstagramSource):
        return getattr(source, "analytics", False) is True
    if isinstance(source, TiktokSource | ThreadsSource):
        return getattr(source, "insights", False) is True
    return False


def _collect_one(
    source: Any,
    ok_platforms: frozenset[str],
    env: Mapping[str, str],
    fetch: Fetch,
    stamp: datetime,
    figures: list[Figure],
    failures: list[InsightFailure],
) -> None:
    if isinstance(source, YoutubeSource):
        _youtube(source, ok_platforms, env, fetch, stamp, figures, failures)
        return
    if isinstance(source, InstagramSource):
        _instagram(source, ok_platforms, env, fetch, stamp, figures, failures)
        return
    if not _opted_in(source) or source.platform not in ok_platforms:
        return
    try:
        if isinstance(source, TiktokSource):
            numbers = video_stats(source, env, fetch, int(stamp.timestamp()))
        else:
            numbers = threads_insights(source, env, fetch, stamp.date())
    except (FetchError, TiktokError, MetaError) as exc:
        failures.append(InsightFailure(source.platform, _word(exc)))
        return
    except Exception:
        failures.append(InsightFailure(source.platform, "fetch_failed"))
        return
    for metric, value in sorted(numbers.items()):
        if type(value) is not int or not 0 <= value <= MAX_VALUE:
            failures.append(InsightFailure(source.platform, "bad_aggregate"))
            continue
        note = METRICS.get((source.kind, metric))
        if note is not None:
            figures.append(Figure(source.platform, metric, value, note))


__all__ = ["OWNER_REPORT_ONLY", "Figure", "InsightFailure", "collect"]
