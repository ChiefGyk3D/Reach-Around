# SPDX-License-Identifier: Apache-2.0
"""The one way to release the source and country by destination tables.

``release_report`` checks that the period is complete, builds the report in
memory, records the period in the release ledger, and only then returns it.
Nothing is shown before the record is written, and if the record cannot be
written nothing is returned. A build that fails writes no record. Rendering
after the return (a CSV write, say) can still fail: the period then stays
recorded as released although nobody saw it, which is the safe direction (a
repeat with the same export is allowed).

THREAT MODEL: the owner runs this on his own machine over his own private data
and is a trusted operator. These checks stop accidents (a partial export, an
overlapping week and month, a repeat with different data). They do not stop an
owner who edits his own files or a hostile caller in the process; Python cannot
enforce that, and no attempt is made.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from reach_around.funnel.export import Export
from reach_around.funnel.ledger import (
    LedgerError,
    Release,
    now_stamp,
    record_release,
)
from reach_around.funnel.report import _build_report, period_bounds


class IncompletePeriod(ValueError):
    """The export does not prove the period is complete and fully counted."""


def _evidence(export: Export, site: str, period: str, start: date, end: date) -> None:
    """Refuse unless the export proves the period is closed and was fully counted."""
    first = start.isoformat()
    if period == "week":
        if "click_seal" not in export.present:
            raise IncompletePeriod(
                "the export has no click_seal rows, so it cannot show the week "
                "is closed; export click_seal too (docs/report.md)"
            )
        sealed = {r["day"] for r in export.click_seal if r["site"] == site}
        missing = [
            (start + timedelta(days=n)).isoformat()
            for n in range(7)
            if (start + timedelta(days=n)).isoformat() not in sealed
        ]
        if missing:
            raise IncompletePeriod(
                f"{len(missing)} day(s) of the week are not sealed in this export "
                "(the Worker has not closed them yet, or the export is partial); "
                "export again later"
            )
    else:
        if "click_month_log" not in export.present:
            raise IncompletePeriod(
                "the export has no click_month_log rows, so it cannot show the "
                "month was rolled up; export click_month_log too (docs/report.md)"
            )
        logged = [
            r
            for r in export.click_month_log
            if r["site"] == site and r["month"] == first[:7]
        ]
        if not logged:
            raise IncompletePeriod(
                "the month has no completed rollup marker in this export; export "
                "again after the Worker has rolled the month up"
            )


def release_report(
    export: Export,
    ledger: Path,
    *,
    site: str = "links",
    period: str,
    offset: int = 0,
    as_of: date,
    diagnostics: dict[str, Any] | None = None,
    **kw: Any,
) -> tuple[dict[str, Any], str]:
    """Build the report for one fixed period, record it, return (report, status).

    ``status`` is 'release' or 'repeat'. Public traffic never vetoes a release: a
    counting-capacity problem only sets the coarse status carried by the report.
    Raises ``ValueError`` (not complete, bad arguments) or ``LedgerError`` (no
    verified ledger, overlap, changed data for a released period, cannot write)
    and returns nothing.
    """
    if any(kw.get(k) is not None for k in ("since", "until", "days")):
        raise ValueError(
            "--period cannot be combined with --since, --until or --days: "
            "the source and country tables are released for one fixed "
            "calendar week or month only"
        )
    start, end = period_bounds(period, offset, as_of)
    _evidence(export, site, period, start, end)
    report, digest = _build_report(
        export,
        site=site,
        period=period,
        offset=offset,
        as_of=as_of,
        diagnostics=diagnostics,
        **kw,
    )
    status = record_release(
        ledger,
        Release(site, period, start.isoformat(), end.isoformat(), now_stamp(), digest),
    )
    return report, status


__all__ = ["IncompletePeriod", "LedgerError", "release_report"]
