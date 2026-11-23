# SPDX-License-Identifier: Apache-2.0
"""Write a synthetic export for the example Grafana dashboards.

Every number here is made up by a fixed seed for an example creator. Nothing is
read from a real site. The file has the shape `reach-around grafana-export` reads
(`docs/grafana.md`), so the dashboards go through the real floor rules:

    python3 make_sample_export.py --end 2026-11-20 > sample-export.json
    reach-around grafana-export --input sample-export.json --output grafana.sqlite3

The same `--end` always gives the same bytes. Leave `--end` off to end on today's
UTC day, so the default Grafana time range shows data.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime, timedelta

DAYS = 60
SEED = 727
GAP_OFFSET = 20  # the day (counted from the first) with no collection
SITE = "links"

# (link id, clicks per 100 likely-human views)
# twitch and kick are the two watch links. Their volumes differ; their treatment
# in the dashboards does not.
LINKS = (
    ("twitch", 5.5),
    ("kick", 4.5),
    ("youtube", 3.0),
    ("shop", 2.4),
    ("newsletter", 1.8),
)
PAGES = ("home", "gear-picks", "events", "kit")
SOURCES = (
    ("d", ""),
    ("r", "news.example"),
    ("u", "newsletter"),
    ("i", ""),
)
COUNTRIES = ("US", "DE", "GB", "CA", "FR")


class _Rng:
    """A tiny seeded LCG, so the sample is the same on every Python."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def uniform(self, low: float, high: float) -> float:
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % 2**64
        return low + (high - low) * (self.state >> 11) / 2**53


def _ts(day: date, second: int) -> int:
    base = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return int(base.timestamp() * 1000) + second * 1000


def build(end: date) -> dict[str, object]:
    rng = _Rng(SEED)
    views: list[dict[str, object]] = []
    clicks: list[dict[str, object]] = []
    coverage: list[dict[str, object]] = []
    for offset in range(DAYS):
        day = end - timedelta(days=DAYS - 1 - offset)
        iso = day.isoformat()
        day_views: list[dict[str, object]] = []
        day_clicks: list[dict[str, object]] = []
        trend = 1.0 + offset / DAYS  # slow growth
        weekend = 1.25 if day.weekday() >= 5 else 1.0
        base = int(rng.uniform(150, 260) * trend * weekend)
        human = 0
        for page, share in zip(PAGES, (0.55, 0.2, 0.15, 0.1), strict=True):
            for (kind, src), sshare in zip(SOURCES, (0.4, 0.3, 0.2, 0.1), strict=True):
                for country, cshare in zip(
                    COUNTRIES, (0.5, 0.2, 0.15, 0.1, 0.05), strict=True
                ):
                    n = int(base * share * sshare * cshare * rng.uniform(0.7, 1.3))
                    if n:
                        human += n
                        day_views.append(_view(iso, page, kind, src, country, "h", n))
        for bot_class, ratio in (("v", 0.08), ("a", 0.12), ("s", 0.03)):
            n = int(human * ratio * rng.uniform(0.6, 1.4))
            if n:
                day_views.append(_view(iso, "home", "d", "", "US", bot_class, n))
        for link_id, per_100 in LINKS:
            n = int(human * per_100 / 100 * rng.uniform(0.6, 1.4))
            for k in range(n):
                day_clicks.append(
                    {
                        "ts": _ts(day, (k * 37) % 86000),
                        "link_id": link_id,
                        "destination_kind": "link",
                        "country": COUNTRIES[k % len(COUNTRIES)],
                    }
                )
        if offset % 11 == 0:  # two clicks on a rare link: always under the floor
            day_clicks += [
                {
                    "ts": _ts(day, k),
                    "link_id": "tiny-link",
                    "destination_kind": "link",
                    "country": "US",
                }
                for k in range(2)
            ]
        hours = "o" * 24
        if offset == GAP_OFFSET:
            hours = "-" * 24  # one whole day with no collection
        elif offset == 33:
            hours = "o" * 9 + "q" * 15  # a partial day: the view budget ran out
        coverage.append({"day": iso, "hours": hours})
        if offset != GAP_OFFSET:  # the Worker has no rows for a day it did not run
            views += day_views
            clicks += day_clicks
    return {
        "version": 1,
        "tables": {
            "view_daily": views,
            "click_events": clicks,
            "analytics_coverage": coverage,
        },
    }


def _view(
    day: str, page: str, kind: str, src: str, country: str, bot_class: str, n: int
) -> dict[str, object]:
    return {
        "site": SITE,
        "day": day,
        "day_tz": "UTC",
        "page": page,
        "src_kind": kind,
        "src": src,
        "country": country,
        "bot_class": bot_class,
        "views": n,
        "uniques": max(1, n // 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--end", help="last UTC day, YYYY-MM-DD (default today)")
    args = parser.parse_args(argv)
    end = date.fromisoformat(args.end) if args.end else datetime.now(UTC).date()
    json.dump(build(end), sys.stdout, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
