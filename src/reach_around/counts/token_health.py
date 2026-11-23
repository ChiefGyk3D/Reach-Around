# SPDX-License-Identifier: Apache-2.0
"""One fixed-vocabulary token health report for the daily counts refresh.

Collectors already say what they know: Instagram and Threads carry the
non-secret ``token_expires`` day in the config; every collector ends a source
with a fixed error word (``token_expired``, ``token_rejected`` and so on) that
the status file keeps; YouTube has only a key, so a rejected key shows as the
fetch words ``http_400`` or ``http_401``. This module turns those signals, and
nothing else, into one record per platform: ``source``, ``state`` (``ok``,
``expiring``, ``expired`` or ``rejected``) and ``days_left`` when a date is
known. It reads no secret and no environment variable, so no token value can
reach a line, a summary or an annotation. It sends nothing anywhere: the owner's
alert channels belong to the instance repository's workflow
(docs/token-health.md).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from reach_around.config.numbers_sources import InstagramSource, ThreadsSource

OK = "ok"
EXPIRING = "expiring"
EXPIRED = "expired"
REJECTED = "rejected"
STATES = (OK, EXPIRING, EXPIRED, REJECTED)

# Source kinds that hold a platform token or key.
KINDS = frozenset({"instagram", "threads", "twitch", "tiktok", "youtube"})

# Every fixed error word a collector can put in the status file, sorted into
# "about the token or key" (mapped to a state) or "not about the token" (a
# setting, a response shape, a network failure). tests/test_counts_token_health.py
# reads the collectors' own raise statements and fails on a word in neither set,
# so a new collector word cannot appear without a decision here.
WORD_STATES = {
    "token_expired": EXPIRED,
    "token_rejected": REJECTED,
    "refresh_failed": REJECTED,  # the platform refused the refresh token
    "refresh_store_failed": REJECTED,  # a rotated pair could not be kept
    "refresh_response_invalid": REJECTED,  # TikTok's refresh answer was unusable
    "scope_missing": REJECTED,
    "missing_credential": REJECTED,  # the credential is not set or not usable
    "invalid_credential": REJECTED,
}
NOT_TOKEN_WORDS = frozenset(
    {
        "api_error",
        "bad_aggregate",
        "bad_auto_refresh",
        "bad_count",
        "bad_credential_name",
        "bad_env_name",
        "bad_host",
        "bad_source_setting",
        "bad_well_known",
        "fetch_failed",
        "invalid_handle",
        "missing_field",
        "missing_handle",
        "refresh_needs_sink",
        "refresh_sink_required",
        "room_not_public",
        "window_incomplete",
        "window_mismatch",
    }
)
EXPIRED_WORDS = frozenset(w for w, v in WORD_STATES.items() if v == EXPIRED)
REJECTED_WORDS = frozenset(w for w, v in WORD_STATES.items() if v == REJECTED)
# YouTube has no token words; its API refuses a bad key with these fetch words.
# UNVERIFIED: http_403 is also what a quota stop looks like, so it is NOT counted.
# http_400 may be a malformed request rather than a bad key (docs/token-health.md).
YOUTUBE_REJECTED_WORDS = frozenset({"http_400", "http_401"})


@dataclass(frozen=True)
class TokenHealth:
    """One platform's token state. Holds no secret by construction."""

    source: str
    kind: str
    state: str
    days_left: int | None = None

    def line(self) -> str:
        days = "unknown" if self.days_left is None else str(self.days_left)
        return f"token-health: source={self.source} state={self.state} days_left={days}"


def _word_state(kind: str, word: str | None) -> str | None:
    if word is None:
        return None
    if word in WORD_STATES:
        return WORD_STATES[word]
    if kind == "youtube" and word in YOUTUBE_REJECTED_WORDS:
        return REJECTED
    return None


def assess(
    sources: Iterable[object],
    status: Mapping[str, Mapping[str, str]],
    today: date,
) -> list[TokenHealth]:
    """The health of every token-holding source, in config order.

    ``status`` maps a platform slug to ``{"status": ..., "detail": ...}`` (the
    status file's shape). A word from the last run wins over the date, because
    a platform that refused the token has said so already. For Instagram and
    Threads the config's ``token_expires`` gives ``days_left``; at or inside
    ``refresh_within_days`` the state is ``expiring``, past the day ``expired``.
    Without a date ``days_left`` stays ``None`` and the state follows the words.
    """
    report: list[TokenHealth] = []
    for source in sources:
        kind = getattr(source, "kind", None)
        platform = getattr(source, "platform", None)
        if kind not in KINDS or not isinstance(platform, str):
            continue
        entry = status.get(platform, {})
        word = entry.get("detail") if entry.get("status") == "error" else None
        days: int | None = None
        state = OK
        if isinstance(source, InstagramSource | ThreadsSource):
            if source.token_expires is not None:
                days = (source.token_expires - today).days
                if days < 0:
                    state = EXPIRED
                elif days <= source.refresh_within_days:
                    state = EXPIRING
        from_word = _word_state(str(kind), word)
        if from_word is not None:
            state = from_word
        report.append(TokenHealth(platform, str(kind), state, days))
    return report


def exit_code(report: Iterable[TokenHealth], failed: int, warned: int) -> int:
    """``failed`` for any expired or rejected token, ``warned`` for expiring, else 0."""
    states = {item.state for item in report}
    if states & {EXPIRED, REJECTED}:
        return failed
    if EXPIRING in states:
        return warned
    return 0


def render_text(report: list[TokenHealth]) -> str:
    return "".join(item.line() + "\n" for item in report)


def render_json(report: list[TokenHealth]) -> str:
    return (
        json.dumps(
            {
                "tokens": [
                    {
                        "source": i.source,
                        "state": i.state,
                        "days_left": i.days_left,
                    }
                    for i in report
                ]
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


_ACTION = {
    EXPIRING: "renew it soon",
    EXPIRED: "renew it now",
    REJECTED: "the platform refused it; authorize again",
}


def render_markdown(report: list[TokenHealth]) -> str:
    """A GitHub step summary table. Fixed words and numbers only."""
    lines = [
        "### Token health",
        "",
        "| Source | State | Days left | Action |",
        "| --- | --- | --- | --- |",
    ]
    for item in report:
        days = "unknown" if item.days_left is None else str(item.days_left)
        lines.append(
            f"| {item.source} | {item.state} | {days} | {_ACTION.get(item.state, '')} |"
        )
    return "\n".join(lines) + "\n"


def render_github(report: list[TokenHealth]) -> str:
    """GitHub Actions annotations: a warning while expiring, an error otherwise."""
    out = []
    for item in report:
        if item.state == OK:
            continue
        level = "warning" if item.state == EXPIRING else "error"
        days = "" if item.days_left is None else f", {item.days_left} days left"
        out.append(
            f"::{level} title=Token health::{item.source} token {item.state}{days}; "
            f"{_ACTION[item.state]}"
        )
    return "".join(line + "\n" for line in out)
