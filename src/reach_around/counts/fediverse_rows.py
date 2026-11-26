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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import get_args

from reach_around.config.fediverse import MAX_INTERACTIONS, MAX_POSTS
from reach_around.net.egress import Reason

# Closed list of reason words (design section 8). ``egress_<word>`` names are
# built from the egress guard's own fixed set, so an invented one is refused.
_OWN_REASONS: frozenset[str] = frozenset(
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
EGRESS_REASONS: frozenset[str] = frozenset(f"egress_{w}" for w in get_args(Reason))
REASONS: frozenset[str] = _OWN_REASONS | EGRESS_REASONS


def is_reason(word: object) -> bool:
    """True only for a word on the closed list (own words and ``egress_*`` words)."""
    return type(word) is str and word in REASONS


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
    if type(read) is NotProvided:
        # Re-checked here: a marker built around the constructor is not trusted.
        return (prior, read.reason) if is_reason(read.reason) else (prior, "bad_shape")
    if not isinstance(read, (list, tuple)):
        return prior, "bad_shape"
    if len(read) > MAX_POSTS:
        return prior, "window_truncated"
    rows = list(read)
    if not all(_is_exact_row(row) for row in rows):
        return prior, "bad_shape"  # one bad row rejects the whole read
    return rows, None


def _is_exact_row(row: object) -> bool:
    """True only for a genuine ``InteractionRow`` whose three fields are in range.

    Checked at the boundary, not trusted from construction: an exact type (no
    subclass, no stand-in with the same attribute names), no instance dict, and
    every count a plain int in range, even if it was changed after creation.
    """
    if type(row) is not InteractionRow or hasattr(row, "__dict__"):
        return False
    try:
        _count(row.boosts)
        _count(row.favorites)
        _count(row.replies)
    except ValueError:
        return False
    return True
