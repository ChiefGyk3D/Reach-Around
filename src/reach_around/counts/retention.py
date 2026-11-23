# SPDX-License-Identifier: Apache-2.0
"""Warn, before a platform's retention window ends, about stored ``api`` rows (#620).

``numbers.retain_days`` names a window in days per platform (the owner reads it
from ``docs/research/platform-terms-retention.md``). A row is *expiring* when its
age is within ``numbers.retain_warn_days`` of the window and *past* when it is
older than the window. The check only COUNTS: it reads records, never changes or
deletes one, and never prints a value. Removal is the history tombstone tool.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from reach_around.counts.history import Record, parse_as_of

DEFAULT_WARN_DAYS = 7


@dataclass(frozen=True)
class RetentionPolicy:
    retain_days: Mapping[str, int] = field(default_factory=dict)
    warn_days: int = DEFAULT_WARN_DAYS


@dataclass(frozen=True)
class RetentionWarning:
    platform: str
    retain_days: int
    warn_days: int
    expiring: int
    past: int

    def line(self) -> str:
        window = f"{self.retain_days} day window"
        if self.expiring and self.past:
            body = (
                f"{self.expiring} api record(s) expire within {self.warn_days} days "
                f"of the {window}, {self.past} are past it"
            )
        elif self.expiring:
            body = (
                f"{self.expiring} api record(s) expire within {self.warn_days} days "
                f"of the {window}"
            )
        else:
            body = f"{self.past} api record(s) are past the {window}"
        return (
            f"{self.platform}: retention warning: {body}; redact them "
            "with the history tombstone tool (#586)"
        )


def retention_warnings(
    records: Sequence[Record], policy: RetentionPolicy, today: date
) -> list[RetentionWarning]:
    """One warning per platform with rows near or past its window, by name."""
    expiring: dict[str, int] = {}
    past: dict[str, int] = {}
    for record in records:
        if record.type != "record" or record.source != "api":
            continue
        window = policy.retain_days.get(record.platform)
        if window is None:
            continue
        age = (today - parse_as_of(record.as_of_utc).date()).days
        if age > window:
            past[record.platform] = past.get(record.platform, 0) + 1
        elif age >= window - policy.warn_days:
            expiring[record.platform] = expiring.get(record.platform, 0) + 1
    return [
        RetentionWarning(
            platform=name,
            retain_days=policy.retain_days[name],
            warn_days=policy.warn_days,
            expiring=expiring.get(name, 0),
            past=past.get(name, 0),
        )
        for name in sorted(set(expiring) | set(past))
    ]
