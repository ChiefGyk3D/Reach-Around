# SPDX-License-Identifier: Apache-2.0
"""Collection gaps (#401, #402): a missing day is a gap with a reason, not a zero.

The Worker's hourly health step writes one row per UTC day into
``analytics_coverage``: 24 letters, one per hour, for the hour that had just
ended. ``o`` means the Worker was collecting, ``k`` the page-view kill switch was
on when it sampled, ``q`` the account view budget had been reached (views were
dropped), and ``-`` that nothing was sampled (an outage: the Worker did not run).
A quiet hour with no views is ``o``: that is a real zero, not a gap.

This module compares that log with the calendar. It is pure (no I/O, no clock;
the caller passes ``today``) and its output holds only dates and the three
closed reason words, so nothing in it can carry visitor data.

What it does NOT claim: the letters are sampled once an hour (a kill switch
flipped on and off inside one hour can be missed), the budget letter is the
account-wide ceiling only (a single site's own budget is not in it), and days
before the first coverage row are *unmonitored*, which is not a gap and not a
zero: nothing is known about them.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

# Letter -> reason word. The only reasons a gap can carry.
REASONS: dict[str, str] = {"-": "outage", "q": "quota", "k": "kill_switch"}
# When two reasons tie, the more specific (a deliberate act) wins.
_PRIORITY = ("kill_switch", "quota", "outage")
MAX_LISTED = 60

NOTE = (
    "A gap is time when nothing was collected; it is not a zero. Totals in this "
    "report cover only the time data was collected."
)


@dataclass(frozen=True)
class DayStatus:
    day: str
    status: str  # "gap" (no hour collected), "partial" (some hours missing)
    reasons: tuple[str, ...]


@dataclass
class Coverage:
    """The coverage of one window; ``monitored`` is False without the table."""

    monitored: bool = False
    monitored_since: str | None = None
    gaps: list[DayStatus] = field(default_factory=list)
    partial: list[DayStatus] = field(default_factory=list)
    unmonitored_days: int = 0

    @property
    def spans_gap(self) -> bool:
        return bool(self.gaps or self.partial)

    def as_dict(self) -> dict[str, Any]:
        gaps = [{"day": g.day, "reason": g.reasons[0]} for g in self.gaps]
        partial = [{"day": p.day, "reasons": list(p.reasons)} for p in self.partial]
        if not self.monitored:
            status = "not_exported"
        elif self.spans_gap:
            status = "gaps"
        else:
            status = "complete"
        return {
            "status": status,
            "monitored_since": self.monitored_since,
            "gap_days": len(gaps),
            "partial_days": len(partial),
            "unmonitored_days": self.unmonitored_days,
            "gaps": gaps[:MAX_LISTED],
            "partial": partial[:MAX_LISTED],
            "note": NOTE if self.spans_gap else "",
        }


def classify(hours: str) -> DayStatus | None:
    """The status of one fully sampled day, or None when every hour was collected."""
    letters = Counter(hours)
    collected = letters.get("o", 0)
    if collected == 24:
        return None
    reasons = [REASONS[c] for c in letters if c in REASONS]
    reasons.sort(
        key=lambda r: (
            -sum(letters[k] for k, v in REASONS.items() if v == r),
            _PRIORITY.index(r),
        )
    )
    status = "gap" if collected == 0 else "partial"
    return DayStatus("", status, tuple(reasons))


def _days(first: date, last: date) -> Iterable[date]:
    for i in range((last - first).days + 1):
        yield first + timedelta(days=i)


def coverage_for_window(
    rows: Iterable[Mapping[str, Any]],
    present: bool,
    since: str | None,
    until: str | None,
    today: date,
) -> Coverage:
    """Compare the coverage log with the calendar over ``since``..``until`` (UTC days).

    Today is never judged (its hours are not over), and neither is the first
    covered day (the step started part way through it). A calendar day after the
    first covered day with no row at all is a gap with the reason ``outage``.
    """
    if not present:
        return Coverage()
    by_day = {r["day"]: r["hours"] for r in rows}
    if not by_day:
        return Coverage(monitored=True)
    first = date.fromisoformat(min(by_day))
    start = date.fromisoformat(since) if since else first
    end = date.fromisoformat(until) if until else date.fromisoformat(max(by_day))
    end = min(end, today - timedelta(days=1))
    cov = Coverage(monitored=True, monitored_since=first.isoformat())
    if start > end:
        return cov
    for d in _days(start, end):
        iso = d.isoformat()
        if d <= first:
            cov.unmonitored_days += 1
            continue
        hours = by_day.get(iso)
        if hours is None:
            cov.gaps.append(DayStatus(iso, "gap", ("outage",)))
            continue
        st = classify(hours)
        if st is None:
            continue
        item = DayStatus(iso, st.status, st.reasons)
        (cov.gaps if st.status == "gap" else cov.partial).append(item)
    return cov
