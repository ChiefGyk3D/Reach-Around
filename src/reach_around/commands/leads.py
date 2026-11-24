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
  lowercase, one ``@``, dotted domain, ``xn--`` labels checked by the same
  round-trip A-label rules as worker/src/kit/punycode.ts). A bad address is
  refused with a fixed message that never repeats the value. ``--email -``
  reads it from stdin so it stays out of argv and shell history.
* Every wrangler result block must say ``"success": true``; anything else
  refuses the export before a row is read.
* Exactly one match is required. None is exit 1; more than one is exit 2.

Exit codes: 0 done (dry run or SQL printed); 1 no lead matches; 2 usage problem
or an export that was refused.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import UTC, datetime
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


def _is_valid_a_label(label: str) -> bool:
    """The Worker's isValidALabel (worker/src/kit/punycode.ts), round trip included.

    A label that starts ``xn--`` must decode, hold a non-ASCII letter, mark or
    digit (hyphen allowed) and no right-to-left character, not start with a
    combining mark, be lowercase and NFC, and encode back to the same ASCII.
    Right-to-left is judged by bidi class, which is a superset of the Worker's
    script list: it can only refuse more, never accept what the Worker refuses.
    """
    rest = label[4:]
    if len(label) > 63 or not label.startswith("xn--") or not rest:
        return False
    try:
        text = rest.encode("ascii").decode("punycode")
        if text.encode("punycode").decode("ascii") != rest:
            return False
    except (UnicodeError, ValueError):
        return False
    if (
        text.isascii()
        or text != text.lower()
        or text != unicodedata.normalize("NFC", text)
    ):
        return False
    if unicodedata.category(text[0]).startswith("M"):
        return False
    for char in text:
        if char != "-" and unicodedata.category(char)[0] not in ("L", "M", "N"):
            return False
        if unicodedata.bidirectional(char) in ("R", "AL", "AN"):
            return False
    return True


def _label_ok(label: str) -> bool:
    """The Worker's dnsLabelOk."""
    if not _LABEL.fullmatch(label):
        return False
    if label.startswith("xn--"):
        return _is_valid_a_label(label)
    return label[2:4] != "--"


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
    if len(labels) < 2 or not all(_label_ok(part) for part in labels):
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


def _load_rows(path: Path) -> list[Any]:
    """Unchecked rows from the result blocks of a wrangler ``--json`` export."""
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
            # Fail closed: a failed or partial wrangler answer must never
            # become deletion SQL. Only the JSON true counts.
            if item.get("success") is not True:
                raise LeadsError("export: a result block is missing success: true")
            results = item["results"]
            if not isinstance(results, list):
                raise LeadsError("export: 'results' must be a list")
            rows.extend(results)
        else:
            rows.append(item)
        if len(rows) > MAX_ROWS:
            raise LeadsError("export: too many rows")
    return rows


def load_leads(path: Path) -> list[tuple[str, str]]:
    """Validated ``(id, email)`` pairs from a ``wrangler d1 execute --json`` file."""
    return [_check_row(row) for row in _load_rows(path)]


# ---------------------------------------------------------------------------
# leads show: the access-request view (GDPR Art. 15). Same trust rules as
# delete: the export is semi-trusted, every column is checked, and the only
# things printed are the masked address, the country, two timestamps and the
# kit event kinds with their hours.
# ---------------------------------------------------------------------------

EVENT_COLUMNS = frozenset({"lead_id", "kind", "ts"})
EVENT_KINDS = frozenset({"unlock", "view", "verify"})
_COUNTRY = re.compile(r"[A-Z]{2}")
_HOUR_MS = 3_600_000
_MAX_TS = 253_402_300_799_000  # 9999-12-31T23:59:59Z, in milliseconds


def _check_ts(value: Any, *, hour: bool = False) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_TS
        or (hour and value % _HOUR_MS)
    ):
        raise LeadsError("export: a timestamp is not valid")
    return value


def _iso(ts_ms: int, *, hour: bool = False) -> str:
    moment = datetime.fromtimestamp(ts_ms // 1000, tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%MZ" if hour else "%Y-%m-%dT%H:%M:%SZ")


def load_lead_views(
    path: Path,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[tuple[str, str, int]]]:
    """``(email by id, lead fields by id, events)`` from a leads plus events export."""
    emails: dict[str, str] = {}
    fields: dict[str, dict[str, Any]] = {}
    events: list[tuple[str, str, int]] = []
    for row in _load_rows(path):
        if isinstance(row, dict) and "lead_id" in row:
            if set(row) != EVENT_COLUMNS:
                raise LeadsError("export: an event row has unexpected columns")
            lead_id, kind = row["lead_id"], row["kind"]
            if not isinstance(lead_id, str) or not _ID.fullmatch(lead_id):
                raise LeadsError("export: a lead id is not 32 lowercase hex characters")
            if kind not in EVENT_KINDS:
                raise LeadsError("export: an event kind is not known")
            events.append((lead_id, kind, _check_ts(row["ts"], hour=True)))
            continue
        lead_id, email = _check_row(row)
        if "created_ts" not in row:
            raise LeadsError("export: every lead row needs created_ts")
        country = row.get("country")
        if country is not None and not (
            isinstance(country, str) and _COUNTRY.fullmatch(country)
        ):
            raise LeadsError("export: a country is not two capital letters")
        verified = row.get("verified_ts")
        fields[lead_id] = {
            "country": country,
            "created": _check_ts(row["created_ts"]),
            "verified": None if verified is None else _check_ts(verified),
        }
        emails[lead_id] = email
    return emails, fields, events


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


def _address_from_file(arg: str) -> str:
    """The address from a file or stdin (``-``). Errors never name the file."""
    if arg == "-":
        return _address("-")
    try:
        raw = _read(Path(arg), MAX_STDIN_CHARS * 4)
    except ConfigError:
        raise LeadsError("cannot read the address file") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise LeadsError("not a valid email address (not UTF-8)") from None
    if len(text.strip()) > MAX_STDIN_CHARS:
        raise LeadsError("not a valid email address (too long)")
    return normalize_email(text)


def run_show(args: argparse.Namespace) -> int:
    if args.email is not None:
        print(
            "error: the address is not accepted on the command line; "
            "use --email-file PATH or --email-file - (stdin)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.email_file is None:
        print("error: --email-file PATH (or -) is required", file=sys.stderr)
        return EXIT_USAGE
    try:
        email = _address_from_file(args.email_file)
        emails, fields, events = load_lead_views(args.input)
    except LeadsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    ids = [lead_id for lead_id, stored in emails.items() if stored == email]
    masked = mask_email(email)
    if not ids:
        print(f"no lead matches {masked}", file=sys.stderr)
        return EXIT_NO_MATCH
    if len(ids) > 1:
        print(
            f"error: {len(ids)} leads match {masked}; expected one. "
            "The export looks wrong; nothing printed",
            file=sys.stderr,
        )
        return EXIT_USAGE
    lead_id = ids[0]
    info = fields[lead_id]
    mine = sorted((ts, kind) for owner, kind, ts in events if owner == lead_id)
    lines = [
        f"email match: {masked}",
        f"country: {info['country'] or 'unknown'}",
        f"created: {_iso(info['created'])}",
        "verified: " + ("no" if info["verified"] is None else _iso(info["verified"])),
    ]
    if mine:
        lines.append("events:")
        lines.extend(f"  {kind} {_iso(ts, hour=True)}" for ts, kind in mine)
    else:
        lines.append("events: none")
    print("\n".join(lines))
    return EXIT_OK


CHECKLIST = """\
Erasure checklist: every copy of a lead the owner must also clear.
Deleting the database rows is one step of six. Tick each one in your reply.

1. Database rows. The `leads` row and its `kit_events` rows in D1. Run
   `reach-around leads delete ... --yes`, apply the SQL, and check that the
   read-back says 0 (docs/privacy.md).
2. Alert copies. If owner alerts are on, delete the unlock and new-lead
   notices that carry the address from the Discord room, the Matrix room or the
   mailbox you chose. The retention purge does not reach them.
3. Inquiry text. A kit inquiry or a reply the person sent you lives in your
   mailbox or inbox, outside the Worker. Find it by the address and delete it,
   or record why you keep it.
4. D1 Time Travel. D1 can restore the database to any minute in its window:
   7 days on the Free plan, 30 days on the Paid plan. The deleted rows stay
   restorable until the window ends. Do not restore to a point before the
   deletion without deleting the lead again afterward. Say in your reply that
   the copy ages out on its own, and the date.
5. Hand-made exports. Every `leads-export.json`, `delete-lead.sql`, backup dump
   or spreadsheet you made holds addresses. Delete them and empty the trash.
6. Shell history and notes. Check that the address is not in your shell
   history, a ticket or a note (use `--email -` or `--email-file`).

Raw page view rows carry no address and cannot be matched to a person by
design, so there is nothing to find or erase there (docs/privacy.md).
Your reply to the person is due within one month of the request.
"""


def run_delete(args: argparse.Namespace) -> int:
    if args.checklist:
        if args.input is not None or args.email is not None or args.yes or args.dry_run:
            print(
                "error: --checklist prints a fixed list and takes no other option",
                file=sys.stderr,
            )
            return EXIT_USAGE
        sys.stdout.write(CHECKLIST)
        return EXIT_OK
    if args.input is None or args.email is None:
        print("error: --input and --email are required", file=sys.stderr)
        return EXIT_USAGE
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
    delete.add_argument("--input", type=Path, metavar="PATH", help="leads export, JSON")
    delete.add_argument(
        "--email",
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
    mode.add_argument(
        "--checklist",
        action="store_true",
        help="print every copy of a lead you must also clear (no export, no "
        "address); takes no other option",
    )
    delete.set_defaults(func=run_delete)

    show = actions.add_parser(
        "show",
        help="print what is stored about one lead (access request)",
        description="Read a local export of the leads table and the kit events "
        "(see docs/privacy.md) and print what an access request needs for one "
        "address: the masked address, country, created and verified times and "
        "the kit event kinds with their hours. Nothing else, no SQL, no network "
        "call. The address comes from a file or stdin, never from the command "
        "line. Exit 0 done, 1 no match, 2 usage problem or a refused export.",
    )
    show.add_argument(
        "--input", type=Path, required=True, metavar="PATH", help="export, JSON"
    )
    show.add_argument(
        "--email-file",
        metavar="PATH",
        help="a file holding the address, or - to read it from stdin",
    )
    # Refused with a fixed message, so a typed address is never echoed back by
    # argparse's own usage error.
    show.add_argument("--email", default=None, help=argparse.SUPPRESS)
    show.set_defaults(func=run_show)
