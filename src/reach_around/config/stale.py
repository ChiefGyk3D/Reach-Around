# SPDX-License-Identifier: Apache-2.0
"""Which shown numbers are old, and which of those a person keeps by hand.

One list feeds the build's stale label, the ``check`` gate and the
``counts stale`` reminder, so the three never disagree. A number is *hand-kept*
when it is typed in the config or sits in the numbers file under a key no
configured fetch source writes. Everything is judged in whole days against one
date; the commands that report pass the UTC day.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from reach_around.config.numbers import (
    NumberNote,
    Numbers,
    shown_numbers,
    source_platforms,
    stale_limit,
)

REMIND_DAYS = 21


def kit_numbers(config: Any, numbers: Numbers | None, today: date) -> list[NumberNote]:
    """Every kit headline number that has a value and a date."""
    kit = getattr(config, "kit", None)
    if kit is None:
        return []
    fetched = source_platforms(config)
    found = []
    for metric in kit.metrics:
        if metric.source == "counts":
            entry = (
                None
                if numbers is None
                else numbers.lookup(metric.platform, metric.platform)
            )
            if entry is None:
                continue
            stamp = entry.as_of_date
            by_hand = metric.platform not in fetched
        else:
            if metric.value is None or metric.as_of is None:
                continue
            stamp, by_hand = metric.as_of, True
        found.append(
            NumberNote(
                "",
                "",
                metric.label,
                metric.platform,
                (today - stamp).days,
                stale_limit(config, metric.platform),
                by_hand,
                stamp,
                "kit",
            )
        )
    return found


def all_numbers(config: Any, numbers: Numbers | None, today: date) -> list[NumberNote]:
    """Platform-block numbers, then kit numbers."""
    return [
        *shown_numbers(config, numbers, today),
        *kit_numbers(config, numbers, today),
    ]


def due_numbers(
    config: Any, numbers: Numbers | None, today: date, remind_days: int = REMIND_DAYS
) -> list[NumberNote]:
    """Hand-kept numbers that are stale, or old enough for a reminder
    (``remind_days`` or more, and never later than the platform's own limit)."""
    return [
        note
        for note in all_numbers(config, numbers, today)
        if note.hand_kept and (note.stale or note.days >= min(remind_days, note.limit))
    ]
