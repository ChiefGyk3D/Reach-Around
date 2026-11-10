#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Derive the Worker's LINK_IDS and LEAD_RETENTION_DAYS from the build manifest.

Run as `python3 -I .github/scripts/verify-manifest.py <manifest>`. The ids in
the manifest are the list the deploy gives the Worker: they are written to
$GITHUB_OUTPUT as `link_ids=<compact JSON array>` (sorted), so the allow-list
cannot fall behind the build. The list must hold 1 to MAX_LINK_IDS ids (the
Worker's own cap in worker/src/sites.ts, which would drop the rest silently,
so more is an error here). The Worker still rejects every id that is not on
the list, so the click route never accepts an arbitrary id.

LINK_IDS in the environment (a JSON array) is optional. When set, a
difference from the build is a workflow warning only (the deployed list is the
build's). Any id outside the closed
grammar ^[a-z0-9-]{1,40}$ (the grammar the workflow enforces for LINK_IDS) is
refused with a fixed error that never echoes the value, so a hostile name in a
downloaded artifact cannot forge a workflow command (a newline plus `::...`).

Lead retention has ONE source: the site config's privacy.lead_retention_days,
which the build prints on the privacy page and records in the manifest. This
script checks it is a whole number from 1 to MAX_LEAD_RETENTION_DAYS (the
Worker's own maximum) and writes it as `lead_retention_days=<n>`; the deploy
gives that value to the Worker as LEAD_RETENTION_DAYS. The repository variable
LEAD_RETENTION_DAYS is optional and only compared: when set (plain digits, in
range) and different from the config, the deploy fails, naming both, so the
page can never promise one number while the Worker deletes at another.
"""

from __future__ import annotations

import json
import os
import re
import sys

ID = re.compile(r"[a-z0-9-]{1,40}")  # used with fullmatch: `$` would allow "\n"
MAX_LINK_IDS = 200  # worker/src/sites.ts MAX_LINK_IDS (a test keeps them equal)
# worker/src/kit/leadretention.ts MAX_LEAD_RETENTION_DAYS (a test keeps them equal)
MAX_LEAD_RETENTION_DAYS = 365
PLAIN_DAYS = re.compile(r"[1-9][0-9]{0,2}")  # used with fullmatch


def escape(text: str) -> str:
    """Escape text emitted after a workflow command (defense in depth: ids are
    already validated, so this should be a no-op)."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def refuse(reason: str) -> int:
    print(f"::error::{reason}")
    return 1


def valid_ids(value: object) -> set[str] | None:
    """The ids in a JSON array or object, or None if any is not a valid id."""
    if not isinstance(value, list | dict):
        return None
    names = list(value)
    if not all(isinstance(n, str) and ID.fullmatch(n) for n in names):
        return None
    return set(names)


def retention_days(value: object) -> int | None:
    """A whole number 1..MAX in the manifest (never a bool), else None."""
    if type(value) is not int or not 1 <= value <= MAX_LEAD_RETENTION_DAYS:
        return None
    return value


ALERT_CHANNELS = {"discord", "matrix", "email"}
ALERT_EVENTS = {
    "kit_unlocked",
    "lead_captured",
    "gate_failed_repeatedly",
    "analytics_health",
    "inquiry_received",
}
ALERT_VISITOR = {"full", "masked", "none"}
ALERT_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]")
MAX_ALERTS_CONFIG = 1000


def alerts_config(value: object) -> str | None:
    """The alerts value the Worker's ALERTS_CONFIG reads, re-checked here as a
    second lock (worker/src/alerts/config.ts is the reader). Returns the
    canonical one-line JSON, or None when it is not acceptable. Absent is fine
    and handled by the caller."""
    if type(value) is not str or not 2 <= len(value) <= MAX_ALERTS_CONFIG:
        return None
    try:
        doc = json.loads(value)
    except ValueError:
        return None
    if type(doc) is not dict or not set(doc) <= {
        "channels",
        "events",
        "visitor_email",
        "max_per_minute",
        "matrix",
    }:
        return None
    for key, allowed in (("channels", ALERT_CHANNELS), ("events", ALERT_EVENTS)):
        items = doc.get(key)
        if (
            type(items) is not list
            or not items
            or len(set(items)) != len(items)
            or not all(type(i) is str and i in allowed for i in items)
        ):
            return None
    if doc.get("visitor_email") not in ALERT_VISITOR:
        return None
    rate = doc.get("max_per_minute")
    if type(rate) is not int or not 1 <= rate <= 30:
        return None
    matrix = doc.get("matrix")
    if matrix is not None:
        hosts = matrix.get("allowed_hosts") if type(matrix) is dict else None
        if (
            type(hosts) is not list
            or not 1 <= len(hosts) <= 4
            or set(matrix) != {"allowed_hosts"}
            or not all(type(h) is str and ALERT_HOST.fullmatch(h) for h in hosts)
        ):
            return None
    elif "matrix" in doc["channels"]:
        return None
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        return refuse("usage: verify-manifest.py <manifest>")
    try:
        with open(argv[1], encoding="utf-8") as handle:
            document = json.load(handle)
        built = valid_ids(document["links"])
        days = retention_days(document["lead_retention_days"])
        raw = os.environ.get("LINK_IDS", "")
        allowed = valid_ids(json.loads(raw)) if raw else set()
    except (OSError, ValueError, KeyError, TypeError):
        return refuse("the manifest or LINK_IDS could not be read")
    if days is None:
        return refuse(
            "the manifest has no valid lead_retention_days "
            f"(a whole number from 1 to {MAX_LEAD_RETENTION_DAYS})"
        )
    alerts = None
    if "alerts_config" in document:
        alerts = alerts_config(document["alerts_config"])
        if alerts is None:
            return refuse("the manifest has an invalid alerts_config")
    variable = os.environ.get("LEAD_RETENTION_DAYS", "")
    if variable:
        if (
            not PLAIN_DAYS.fullmatch(variable)
            or int(variable) > MAX_LEAD_RETENTION_DAYS
        ):
            return refuse(
                "the repository variable LEAD_RETENTION_DAYS is not a whole "
                f"number from 1 to {MAX_LEAD_RETENTION_DAYS}"
            )
        if int(variable) != days:
            return refuse(
                f"the repository variable LEAD_RETENTION_DAYS ({int(variable)}) "
                f"differs from privacy.lead_retention_days in the site config "
                f"({days}); the privacy page prints the config value and the "
                "Worker deletes by the deployed value, so set both to the same "
                "number or delete the variable"
            )
    if built is None:
        return refuse("the manifest holds a link id outside the allowed grammar")
    if allowed is None:
        return refuse("LINK_IDS holds a value outside the allowed grammar")
    if not built:
        return refuse("the manifest holds no link ids")
    if len(built) > MAX_LINK_IDS:
        return refuse(f"the manifest holds more than {MAX_LINK_IDS} link ids")
    if raw:
        for name in sorted(built - allowed):
            print(
                f"::warning::link id {escape(name)} is in the build but not in LINK_IDS"
            )
        for name in sorted(allowed - built):
            print(
                f"::warning::LINK_IDS lists {escape(name)}, which is not in the build"
            )
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        derived = json.dumps(sorted(built), separators=(",", ":"))
        with open(out, "a", encoding="utf-8") as handle:
            handle.write(f"link_ids={derived}\n")
            handle.write(f"lead_retention_days={days}\n")
            if alerts is not None:
                handle.write(f"alerts_config={alerts}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
