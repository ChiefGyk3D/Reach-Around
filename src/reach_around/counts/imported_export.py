# SPDX-License-Identifier: Apache-2.0
"""Export the kit-readable imported-history file from the importer's history file.

``counts history-import-beacons`` writes daily link-page records with provenance
(``source: imported``, ``note: Beacons``) into a private, hash-chained history
file. The site build never reads that file. This step reads it, checks the chain
(``read_history``), and writes the separate small file the kit reads
(``kit.imported_history``): views and clicks per calendar month, the number of
days each month holds, and the provenance words. No day, run id, hash or any
per-visitor, per-link or per-referrer value leaves this step; the importer never
stored those.
"""

from __future__ import annotations

import calendar
import json
from collections import defaultdict
from datetime import date

from reach_around.config.imported_history import PLATFORM, SOURCE, parse_imported
from reach_around.config.loader import ConfigError
from reach_around.counts.history import History

_METRICS = ("views", "clicks")


def export_imported(history: History) -> str:
    """The imported-history JSON text for ``history``; ``ConfigError`` when the
    history holds anything but the importer's link-page records."""
    if history.tombstones:
        raise ConfigError("history", None, "the history file holds redacted records")
    if not history.records:
        raise ConfigError("history", None, "the history file has no records")
    notes: set[str | None] = set()
    days: dict[tuple[int, int], dict[str, dict[date, int]]] = defaultdict(
        lambda: {m: {} for m in _METRICS}
    )
    for record in history.records:
        if (
            record.platform != PLATFORM
            or record.source != SOURCE
            or record.metric not in _METRICS
            or record.type != "record"
        ):
            raise ConfigError(
                "history",
                None,
                "the history file holds records that are not imported link-page "
                "views and clicks; export only the file the importer wrote",
            )
        notes.add(record.note)
        day = date.fromisoformat(record.as_of_utc[:10])
        slot = days[(day.year, day.month)][record.metric]
        if day in slot:
            raise ConfigError("history", None, "a day is recorded twice")
        slot[day] = record.value
    if len(notes) != 1 or None in notes:
        raise ConfigError("history", None, "the records do not share one note")
    (note,) = notes
    periods = []
    for (year, month), metrics in sorted(days.items()):
        if set(metrics["views"]) != set(metrics["clicks"]):
            raise ConfigError("history", None, "views and clicks cover different days")
        last = calendar.monthrange(year, month)[1]
        periods.append(
            {
                "start": date(year, month, 1).isoformat(),
                "end": date(year, month, last).isoformat(),
                "days": len(metrics["views"]),
                "views": sum(metrics["views"].values()),
                "clicks": sum(metrics["clicks"].values()),
            }
        )
    doc = {
        "version": 1,
        "source": SOURCE,
        "note": note,
        "platform": PLATFORM,
        "periods": periods,
    }
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    parse_imported(text.encode("utf-8"))  # never write a file the kit would refuse
    return text
