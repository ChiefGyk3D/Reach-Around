# SPDX-License-Identifier: Apache-2.0
"""Trust labels for the numbers the kit prints (kit v2, slice 1, #626).

Exactly three labels, read from where a number came from and never chosen by a
template:

* ``api``: ``counts refresh`` wrote the numbers-file entry (it records
  ``source: api``) and a collector is still configured for the platform. An
  entry with no recorded source, or a collector that is gone, is hand-kept.
* ``hand-kept``: a person typed it (in the kit config or in the numbers file), or
  imported it from a platform export. An export is named in the method line;
  there is no fourth label for it (owner decision 2, 2026-11-20).
* ``estimate``: the owner worked it out and said how (``estimate`` on a metric).
  There is no way to get this label without the owner naming the method.

``Trust`` refuses any other label, so a fourth one cannot be printed by
accident. A figure computed from several numbers takes the weakest label of its
inputs (``weakest``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

API = "api"
HAND_KEPT = "hand-kept"
ESTIMATE = "estimate"

# Strongest first. Nothing else is a label.
LABELS: tuple[str, ...] = (API, HAND_KEPT, ESTIMATE)

LABEL_TEXT: dict[str, str] = {API: "API", HAND_KEPT: "Hand-kept", ESTIMATE: "Estimate"}

LEGEND_HEADING = "How to read these numbers"
# Neutral draft wording. The owner approves it (see the pull request).
_LEGEND_BASE: tuple[str, ...] = (
    "API means a collector read the number from the platform's own interface "
    "on the date shown; no outside party audited it.",
    "Hand-kept means the owner typed the number in or imported it from a "
    "platform export, and the line beside it says which.",
    "Estimate means the owner worked the number out and names the method.",
)
# The last sentence names what carries the out-of-date mark. The public page may
# not name the gated sections, so it has its own wording.
LEGEND_SENTENCES: tuple[str, ...] = (
    *_LEGEND_BASE,
    "Headline numbers, the total and analytics are marked out of date past their "
    "age limit; imported history prints its own period, and recent posts carry no "
    "date.",
)
LEGEND_SENTENCES_GATED: tuple[str, ...] = (
    *_LEGEND_BASE,
    "Headline numbers, the total, analytics, engagement and demographics are marked "
    "out of date past their age limit; case studies and imported history print "
    "their own period, and recent posts carry no date.",
)


@dataclass(frozen=True)
class Trust:
    """One number's label and the short line that says where it came from."""

    label: str
    method: str

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise ValueError(f"{self.label!r} is not a trust label")

    @property
    def text(self) -> str:
        return LABEL_TEXT[self.label]

    def view(self) -> dict[str, str]:
        return {"label": self.label, "text": self.text, "method": self.method}


def weakest(trusts: Iterable[Trust]) -> Trust | None:
    """The weakest label among ``trusts`` (``estimate`` below ``hand-kept``
    below ``api``), with a line that says so; ``None`` for no input."""
    found = list(trusts)
    if not found:
        return None
    label = max((t.label for t in found), key=LABELS.index)
    return Trust(label, "Weakest label among the numbers added")


def metric_trust(
    platform: str,
    source: str,
    estimate: str | None,
    api_platforms: frozenset[str],
    origin: str | None = None,
    as_of: date | None = None,
) -> Trust:
    """The label of one headline metric.

    ``source`` is the kit metric's own (``manual`` or ``counts``). The platform
    never enters the choice except through ``api_platforms``, so two platforms
    with the same provenance always get the same label and the same method.

    API needs all of: a ``counts`` number, an entry that ``counts refresh`` wrote
    (``origin == "api"``) and a collector still configured for the platform. A
    number that is old stays API (the page marks it out of date); anything else
    from the numbers file is hand-kept."""
    if estimate:
        return Trust(ESTIMATE, f"Estimate: {estimate}")
    if source == "counts":
        if platform in api_platforms and origin == "api":
            when = f" on {as_of.isoformat()}" if as_of is not None else ""
            return Trust(API, f"Read from the platform's API{when}")
        return Trust(HAND_KEPT, "Typed into the numbers file by the owner")
    return Trust(HAND_KEPT, "Typed by the owner")


def analytics_trust(synced_with: str) -> Trust:
    return Trust(HAND_KEPT, f"Typed by the owner from {synced_with}")


def engagement_trust() -> Trust:
    return Trust(HAND_KEPT, "Worked out from figures typed by the owner")


def case_trust(start: date, end: date) -> Trust:
    return Trust(
        HAND_KEPT, f"Typed by the owner for {start.isoformat()} to {end.isoformat()}"
    )


def posts_trust() -> Trust:
    return Trust(HAND_KEPT, "Counts typed by the owner")


def demographic_trust(as_of: date) -> Trust:
    return Trust(
        HAND_KEPT,
        f"Shares typed by the owner from the report dated {as_of.isoformat()}",
    )


def imported_trust(note: str, last_end: date) -> Trust:
    return Trust(
        HAND_KEPT,
        f"Imported from the {note} export, last period ending {last_end.isoformat()}",
    )
