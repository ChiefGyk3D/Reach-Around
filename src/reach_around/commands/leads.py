# SPDX-License-Identifier: Apache-2.0
"""``reach-around leads delete --input EXPORT --email ADDRESS [--yes]``.

The owner's way to honor a deletion request for one media kit lead (a person who
typed an email address into the gate). It reads a local D1 export of the
``leads`` table, finds the one row for the address, and prints the SQL that
deletes that lead and its kit events. It does not talk to Cloudflare, needs no
token, writes no file and exposes no route: the owner applies the printed SQL
with ``wrangler d1 execute`` (docs/privacy.md, "Deleting one lead").

Safety rules, all tested:

* The default is a dry run. It prints how many leads match, with the address
  masked, and no SQL. ``--yes`` is required to print the SQL.
* The SQL names the lead by its random id, never by the address, so the
  address does not travel into the SQL, a file or a shell history. The only
  value in the SQL is a 32-character lowercase hex id that passed a full-match
  check (the export is semi-trusted input; wrangler cannot bind parameters on
  the command line, so the id is the one validated constant).
* The address is validated and normalized the way the Worker does it (ASCII,
  lowercase, one ``@``, dotted domain). A bad address is refused with a fixed
  message that never repeats the value. ``--email -`` reads it from stdin so it
  stays out of argv and shell history.
* Exactly one match is required. None is exit 1; more than one is exit 2.

Exit codes: 0 done (dry run or SQL printed); 1 no lead matches; 2 usage problem
or an export that was refused.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from reach_around.config.loader import ConfigError, _read

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2

MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_ROWS = 60_000
MAX_STDIN_CHARS = 256

# The grammar of worker/src/kit/input.ts normalizeEmail and the leads table.
_PRINTABLE = re.compile(r"[\x21-\x7e]+")
_LOCAL = re.compile(r"[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_TLD = re.compile(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}")
_ID = re.compile(r"[0-9a-f]{32}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Every column of the leads table (worker/migrations/0009_kit.sql). Any other
# column (an IP address, a user agent) refuses the file.
LEAD_COLUMNS = frozenset(
    {
        "id",
        "email",
        "created_ts",
        "last_seen_ts",
        "verified_ts",
        "source",
        "country",
        "last_view_alert_ts",
        "last_view_event_ts",
        "last_cc_alert_ts",
        "session_floor_ts",
    }
)


class LeadsError(ValueError):
    """A refusal. The message is fixed text and never holds an address or an id."""


def normalize_email(raw: str) -> str:
    """The address as the Worker stores it, or ``LeadsError`` (fixed message)."""
    bad = LeadsError("not a valid email address (ASCII, one @, a dotted domain)")
    text = raw.strip()
    if not 3 <= len(text) <= 254 or not _PRINTABLE.fullmatch(text):
        raise bad
    text = text.lower()
    local, at, domain = text.partition("@")
    if not at or "@" in domain or not 1 <= len(local) <= 64:
        raise bad
    if not _LOCAL.fullmatch(local) or len(domain) > 253:
        raise bad
    labels = domain.split(".")
    if len(labels) < 2 or not all(_LABEL.fullmatch(part) for part in labels):
        raise bad
    if not _TLD.fullmatch(labels[-1]):
        raise bad
    return text


def mask_email(email: str) -> str:
    """``p***@***.test``: the first character and the last domain label only."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@***.{domain.rsplit('.', 1)[-1]}"


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise LeadsError("export: duplicate key in the JSON")
        out[key] = value
    return out


def _bad_constant(_: str) -> Any:
    raise LeadsError("export: NaN and Infinity are not allowed")


def _check_value(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        raise LeadsError("export: unexpected value type in a row")
    if isinstance(value, int):
        if abs(value) >= 10**15:
            raise LeadsError("export: a number is out of range")
        return
    if isinstance(value, str) and len(value) <= 32 and not _CONTROL.search(value):
        return
    raise LeadsError("export: unexpected value in a row")


def _check_row(row: Any) -> tuple[str, str]:
    if not isinstance(row, dict):
        raise LeadsError("export: a row must be an object")
    if not set(row) <= LEAD_COLUMNS:
        raise LeadsError("export: a column outside the leads table is not allowed")
    if "id" not in row or "email" not in row:
        raise LeadsError("export: every row needs id and email")
    lead_id, email = row["id"], row["email"]
    if not isinstance(lead_id, str) or not _ID.fullmatch(lead_id):
        raise LeadsError("export: a lead id is not 32 lowercase hex characters")
    if not isinstance(email, str) or normalize_email(email) != email:
        raise LeadsError("export: a stored address is not in normalized form")
    for name, value in row.items():
        if name not in ("id", "email"):
            _check_value(value)
    return lead_id, email


def load_leads(path: Path) -> list[tuple[str, str]]:
    """Validated ``(id, email)`` pairs from a ``wrangler d1 execute --json`` file."""
    try:
        raw = _read(path, MAX_EXPORT_BYTES)
    except ConfigError as exc:
        raise LeadsError(f"export: {exc.message}") from None
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LeadsError("export: not valid UTF-8") from None
    try:
        data = json.loads(
            text, object_pairs_hook=_no_duplicates, parse_constant=_bad_constant
        )
    except LeadsError:
        raise
    except RecursionError:
        raise LeadsError("export: JSON nested too deeply") from None
    except ValueError:
        raise LeadsError("export: not valid JSON") from None
    if not isinstance(data, list) or not data:
        raise LeadsError("export: expected a non-empty JSON list")
    rows: list[Any] = []
    for item in data:
        if isinstance(item, dict) and "results" in item:
            if not set(item) <= {"results", "success", "meta"}:
                raise LeadsError("export: unexpected key in a result block")
            results = item["results"]
            if not isinstance(results, list):
                raise LeadsError("export: 'results' must be a list")
            rows.extend(results)
        else:
            rows.append(item)
        if len(rows) > MAX_ROWS:
            raise LeadsError("export: too many rows")
    return [_check_row(row) for row in rows]


# Constant statements with one `?` placeholder. wrangler cannot bind parameters
# on the command line, so `_sql` renders the placeholder with the single
# validated id literal. No other value ever reaches a statement.
_STATEMENTS = (
    "-- reach-around leads delete: one lead and its kit events",
    "DELETE FROM kit_events WHERE lead_id = '?';",
    "DELETE FROM leads WHERE id = '?';",
    "-- read-back: remaining must be 0",
    "SELECT COUNT(*) AS remaining FROM leads WHERE id = '?';",
)


def _sql(lead_id: str) -> list[str]:
    # It already matched [0-9a-f]{32}; check again at the point of use so a
    # future edit cannot route anything else here.
    if not _ID.fullmatch(lead_id):
        raise LeadsError("internal: refusing to build SQL from a non-id value")
    return [line.replace("?", lead_id) for line in _STATEMENTS]


def _address(arg: str) -> str:
    if arg != "-":
        return normalize_email(arg)
    text = sys.stdin.read(MAX_STDIN_CHARS + 1)
    if len(text.strip()) > MAX_STDIN_CHARS:
        raise LeadsError("not a valid email address (too long)")
    return normalize_email(text)


def run_delete(args: argparse.Namespace) -> int:
    try:
        email = _address(args.email)
        leads = load_leads(args.input)
    except LeadsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    ids = [lead_id for lead_id, stored in leads if stored == email]
    masked = mask_email(email)
    if not ids:
        print(f"no lead matches {masked}; nothing to delete", file=sys.stderr)
        return EXIT_NO_MATCH
    if len(ids) > 1:
        print(
            f"error: {len(ids)} leads match {masked}; expected one. "
            "The export looks wrong; nothing printed",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if not args.yes:
        print(
            f"dry run: 1 lead matches {masked}. It would be deleted with its "
            "kit events. Nothing was changed. Re-run with --yes to print the SQL."
        )
        return EXIT_OK
    try:
        statements = _sql(ids[0])
    except LeadsError as exc:  # pragma: no cover - guarded by load_leads
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(
        f"1 lead matches {masked}. The SQL for it is on stdout. Apply it with "
        "wrangler (docs/privacy.md) and check that the read-back says 0.",
        file=sys.stderr,
    )
    sys.stdout.write("\n".join(statements) + "\n")
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    leads = subparsers.add_parser(
        "leads", help="owner tools for media kit leads (deletion requests)"
    )
    actions = leads.add_subparsers(dest="leads_command", required=True)
    delete = actions.add_parser(
        "delete",
        help="print the SQL that deletes one lead and its events",
        description="Read a local export of the leads table (made with wrangler "
        "d1 execute --json, see docs/privacy.md), find the lead for one email "
        "address and print the SQL that deletes it. Dry run by default; --yes "
        "prints the SQL. No network call, no file written; the SQL names the "
        "lead by id, never by address. Exit 0 done, 1 no match, 2 usage problem "
        "or a refused export.",
    )
    delete.add_argument(
        "--input", type=Path, required=True, metavar="PATH", help="leads export, JSON"
    )
    delete.add_argument(
        "--email",
        required=True,
        metavar="ADDRESS",
        help="the address to delete, or - to read it from stdin (keeps it out "
        "of shell history)",
    )
    mode = delete.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be deleted, masked (the default)",
    )
    mode.add_argument(
        "--yes", action="store_true", help="print the delete SQL (required to emit it)"
    )
    delete.set_defaults(func=run_delete)
