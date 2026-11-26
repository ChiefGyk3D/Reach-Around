# SPDX-License-Identifier: Apache-2.0
"""The aggregate-only row of the fediverse collector (design sections 4 and 8).

One public post becomes one ``InteractionRow`` of exactly three whole numbers:
boosts, favorites, replies. The type has no field for an id, a time, an author
or text, so none can be carried by mistake, and ``slots`` stops a stray
attribute being added later.

``NotProvided`` is the incomplete marker: a read that could not be completed in
full, with one fixed reason word. ``keep_prior`` is the only rule for what an
incomplete read leaves behind: the previous rows, untouched, and never a row of
zeros or an empty list made up on the spot.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from reach_around.config.fediverse import MAX_INTERACTIONS, MAX_POSTS

# Closed list of reason words (design section 8). ``egress_<word>`` names come
# from the egress guard's own fixed set.
REASONS: frozenset[str] = frozenset(
    {
        "timeout",
        "server_error",
        "needs_token",
        "account_not_found",
        "account_moved",
        "account_suspended",
        "rate_limited",
        "partial_pages",
        "page_cap",
        "window_truncated",
        "clock_skew",
        "counts_unavailable",
        "content_type",
        "bad_json",
        "bad_shape",
        "guard",
    }
)
_EGRESS = re.compile(r"^egress_[a-z][a-z_]{0,31}$")


def is_reason(word: object) -> bool:
    """True for a word on the closed list or an ``egress_*`` word."""
    return isinstance(word, str) and (
        word in REASONS or _EGRESS.match(word) is not None
    )


def _count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTERACTIONS:
        raise ValueError("an interaction count is a whole number from 0 to 10^9")
    return value


@dataclass(frozen=True, slots=True)
class InteractionRow:
    """Boosts, favorites and replies of one public post, and nothing else."""

    boosts: int
    favorites: int
    replies: int

    def __post_init__(self) -> None:
        _count(self.boosts)
        _count(self.favorites)
        _count(self.replies)

    def as_dict(self) -> dict[str, int]:
        return {
            "boosts": self.boosts,
            "favorites": self.favorites,
            "replies": self.replies,
        }


@dataclass(frozen=True, slots=True)
class NotProvided:
    """An incomplete read. ``reason`` is a fixed word, never remote text."""

    reason: str

    def __post_init__(self) -> None:
        if not is_reason(self.reason):
            raise ValueError("not a fixed reason word")


def keep_prior(
    read: Sequence[InteractionRow] | NotProvided,
    prior: Sequence[InteractionRow] | None,
) -> tuple[Sequence[InteractionRow] | None, str | None]:
    """The rows to keep and the reason word, if any.

    A complete read replaces the prior rows (more than ``MAX_POSTS`` rows is a
    ``window_truncated`` read, which keeps the prior rows). An incomplete read
    returns the prior rows exactly as they were, or ``None`` when there are
    none: an incomplete read is never a zero and never an empty list.
    """
    if isinstance(read, NotProvided):
        return prior, read.reason
    if len(read) > MAX_POSTS:
        return prior, "window_truncated"
    return list(read), None
