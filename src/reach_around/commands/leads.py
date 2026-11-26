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
* The SQL names the lead by its random id AND its normalized address, in every
  deleting statement, so the live database deletes only a row that still pairs
  them (a forged or stale export deletes nothing and the read-back says 1).
  The only values in the SQL are a 32-character lowercase hex id and an
  address that passed the strict grammar (the export is semi-trusted input;
  wrangler cannot bind parameters on the command line). The SQL file holds
  the address, so it is deleted with the export; the messages never carry it.
* ``--database`` is mandatory and exact; it is the owner's attestation of
  where the export came from, not proof (the export names no database).
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
import os
import re
import shlex
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


_TWO_SETS = (
    "export: expected exactly two result sets in order, the leads query and "
    "the kit events query (the second may be empty)"
)


def _load_rows(path: Path) -> list[Any]:
    """Unchecked rows from the result blocks of a wrangler ``--json`` export."""
    return [row for block in _load_blocks(path) for row in block]


def _load_blocks(path: Path, *, wrapped: bool = False) -> list[list[Any]]:
    """The result blocks of an export, boundaries kept (one list per block).

    With ``wrapped`` every item must be a result block; a bare row is refused.
    Without it a bare row counts as a block of its own (the delete export).
    """
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
    blocks: list[list[Any]] = []
    total = 0
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
            blocks.append(results)
            total += len(results)
        elif wrapped:
            raise LeadsError(_TWO_SETS)
        else:
            blocks.append([item])
            total += 1
        if total > MAX_ROWS:
            raise LeadsError("export: too many rows")
    return blocks


def load_leads(path: Path) -> list[tuple[str, str]]:
    """Validated ``(id, email)`` pairs from a ``wrangler d1 execute --json`` file."""
    pairs = [_check_row(row) for row in _load_rows(path)]
    # One id for two rows means a stale or joined export: refuse it before any
    # address is matched, never pick one of the rows.
    if len({lead_id for lead_id, _ in pairs}) != len(pairs):
        raise LeadsError("export: a lead id appears more than once")
    return pairs


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


def _check_ts(value: Any) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value <= _MAX_TS
    ):
        raise LeadsError("export: a timestamp is not valid")
    return value


# The lead columns the access export selects (docs/privacy.md). Exactly these.
VIEW_LEAD_COLUMNS = frozenset({"id", "email", "created_ts", "verified_ts", "country"})


def _check_view_lead_row(row: Any) -> tuple[str, str]:
    """A row of the access export: exactly the selected columns, each checked."""
    if not isinstance(row, dict) or set(row) != VIEW_LEAD_COLUMNS:
        raise LeadsError("export: a lead row has unexpected columns")
    lead_id, email = row["id"], row["email"]
    if not isinstance(lead_id, str) or not _ID.fullmatch(lead_id):
        raise LeadsError("export: a lead id is not 32 lowercase hex characters")
    if not isinstance(email, str) or normalize_email(email) != email:
        raise LeadsError("export: a stored address is not in normalized form")
    for name in ("created_ts", "verified_ts", "country"):
        _check_value(row[name])
    return lead_id, email


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
    blocks = _load_blocks(path, wrapped=True)
    if len(blocks) != 2:
        raise LeadsError(_TWO_SETS)
    lead_rows, event_rows = blocks
    # An event row in the leads result (or the reverse) is a wrong export.
    if any(isinstance(r, dict) and "lead_id" in r for r in lead_rows) or any(
        not (isinstance(r, dict) and "lead_id" in r) for r in event_rows
    ):
        raise LeadsError(_TWO_SETS)
    for row in event_rows:
        if set(row) != EVENT_COLUMNS:
            raise LeadsError("export: an event row has unexpected columns")
        lead_id, kind = row["lead_id"], row["kind"]
        if not isinstance(lead_id, str) or not _ID.fullmatch(lead_id):
            raise LeadsError("export: a lead id is not 32 lowercase hex characters")
        if kind not in EVENT_KINDS:
            raise LeadsError("export: an event kind is not known")
        ts = _check_ts(row["ts"])
        # Stored times need not be on the hour; show only the hour.
        events.append((lead_id, kind, ts - ts % _HOUR_MS))
    for row in lead_rows:
        lead_id, email = _check_view_lead_row(row)
        if lead_id in emails:
            # Never keep one of two rows silently, and never print either.
            raise LeadsError("export: a lead id appears more than once")
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


# Constant statements with two placeholders. wrangler cannot bind parameters on
# the command line, so `_sql` renders them with the one validated id literal and
# the one validated, normalized address literal; no other value ever reaches a
# statement. Both are in every deleting statement, so the live database deletes
# only a row that still pairs that id with that address: a forged or stale
# export that names another person's id deletes nothing, and the read-back
# says 1 instead of 0. The SQL file therefore holds the address (docs/privacy.md
# says to delete it with the export).
_STATEMENTS = (
    "-- reach-around leads delete: one lead and its kit events, only if the id "
    "still belongs to the address",
    "DELETE FROM kit_events WHERE lead_id IN "
    "(SELECT id FROM leads WHERE id = '<ID>' AND email = '<EMAIL>');",
    "DELETE FROM leads WHERE id = '<ID>' AND email = '<EMAIL>';",
    # An event that arrived between the two deletes is removed once the lead is
    # gone; it never touches the events of a lead that still exists.
    "DELETE FROM kit_events WHERE lead_id = '<ID>' AND NOT EXISTS "
    "(SELECT 1 FROM leads WHERE id = '<ID>');",
    "-- read-back: 0 and 0 means no remaining rows AT THIS MOMENT, not proof of "
    "completed erasure (no transaction in a file; an event can arrive after "
    "the read). Run this file again after a wait until two consecutive runs "
    "read 0 and 0. leads_remaining 1 means the id does not belong to that "
    "address in this database and nothing was deleted",
    "SELECT (SELECT COUNT(*) FROM leads WHERE id = '<ID>') AS leads_remaining, "
    "(SELECT COUNT(*) FROM kit_events WHERE lead_id = '<ID>') AS events_remaining;",
)
_SQL_EMAIL = re.compile(r"[a-z0-9_+.@-]{6,254}")


def _sql(lead_id: str, email: str) -> list[str]:
    # Both already passed validation; check again at the point of use so a
    # future edit cannot route anything else here.
    if not _ID.fullmatch(lead_id):
        raise LeadsError("internal: refusing to build SQL from a non-id value")
    if not _SQL_EMAIL.fullmatch(email) or normalize_email(email) != email:
        raise LeadsError("internal: refusing to build SQL from a non-address value")
    return [
        line.replace("<ID>", lead_id).replace("<EMAIL>", email) for line in _STATEMENTS
    ]


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


# The leads live in their own D1 database (#772, binding LEADS_DB). The owner
# tools act on that one only. A wrangler export does not name its database, so
# the owner must say, every time, which database it came from, and only the
# exact leads name is accepted. This is an attestation by the owner: it stops a
# wrong name, and it proves nothing about the file. What protects the live
# database is that the delete SQL pairs the id with the address (see _sql).
LEADS_DATABASE = "reach-around-leads"
DATABASE_NOTE = (
    "(named by you; this does not prove the export came from it, the file "
    "carries no database name)"
)


def _database(name: str | None) -> str:
    """The database the owner says the export came from; mandatory, exact."""
    if name is None:
        raise LeadsError(
            f"--database is required: name the database the export came from "
            f"({LEADS_DATABASE})"
        )
    if name != LEADS_DATABASE:
        raise LeadsError(
            f"--database must be exactly {LEADS_DATABASE}; an export from any "
            "other database is refused"
        )
    return name


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
        database = _database(args.database)
        email = _address_from_file(args.email_file)
        emails, fields, events = load_lead_views(args.input)
    except LeadsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"database: {database} {DATABASE_NOTE}", file=sys.stderr)
    ids = [lead_id for lead_id, stored in emails.items() if stored == email]
    masked = mask_email(email)
    # Fixed messages: stderr can reach a log, and a masked address still
    # identifies a person on a small domain.
    if not ids:
        print("no lead matches the supplied address", file=sys.stderr)
        return EXIT_NO_MATCH
    if len(ids) > 1:
        print(
            "error: more than one lead matches the supplied address; the "
            "export looks wrong; nothing printed",
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
Erasure checklist: the known places an address can be besides the database
rows. This tool does not check any of them, does not find copies and does not
confirm a deletion outside the SQL it prints; the list is not proof that no
other copy exists. Tick each item in your reply and say what you checked.

1. Database rows. The `leads` row and its `kit_events` rows in the leads D1
   database (reach-around-leads), never the analytics database. Run
   `reach-around leads delete ... --yes --sql-out FILE`, apply the SQL, and
   check that the read-back shows 0 leads and 0 events (docs/privacy.md).
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
   or spreadsheet you made holds addresses. That includes a whole-database
   `wrangler d1 export` taken before a migration (only scripts/backup-d1.sh
   leaves the lead tables out). Delete them and empty the trash.
6. Shell history and notes. Check that the address is not in your shell
   history, a ticket or a note (the tools take it from stdin or a file only).
7. Short-lived alert hash. The `alert_dedup` table holds a keyed hash of each
   recent alert, which can include the address. A row stops counting as a
   repeat after 10 minutes, but it is deleted only when the next alert event is
   admitted (no timer), so on a quiet site it can stay stored until then. It
   cannot be matched to an address without the Worker's secret key, and the
   delete SQL does not touch it. Clear expired rows by hand with the statement
   in docs/privacy.md ("Every store, checked against the schema").
8. Checked and clear: the Worker's KV namespace holds only the gated page and
   live status state, and the Worker uses no R2, Durable Object or queue
   storage. An unlock cookie is a signed token with no server copy; it stops
   opening the kit once the `leads` row is gone. The scripts/erasure-stores.json
   registry and its test keep this list in step with the database schema.
9. By hand, places this tool and this list cannot see. Look in each and
   record the result, even when it is "nothing found": Worker logs and any
   collector logs or exception output; alert delivery logs at the chat or mail
   provider; automated backups and exports of the database beyond Time Travel;
   CI artifacts and deploy logs; monitoring and error-tracking systems; any
   system you wired to the Worker that stores request or alert payloads.

Raw page view rows carry no address and cannot be matched to a person by
design, so there is nothing to find or erase there (docs/privacy.md).
Your reply to the person is due within one month of the request.
"""


def run_delete(args: argparse.Namespace) -> int:
    if args.checklist:
        if (
            args.input is not None
            or args.email is not None
            or args.yes
            or args.dry_run
            or args.database is not None
            or args.sql_out is not None
        ):
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
    if args.email != "-":
        # Refused before the export is read, so the address spends no time in
        # the process list. It is still in the shell history if it was typed.
        print(
            "error: the address is not accepted on the command line; "
            "use --email - and type it on stdin",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.sql_out is not None and not args.yes:
        print("error: --sql-out needs --yes", file=sys.stderr)
        return EXIT_USAGE
    if args.sql_out is not None and os.fspath(args.sql_out).startswith("-"):
        print(
            "error: --sql-out must not start with '-' (it could be read as an "
            "option); give a path such as ./delete-lead.sql",
            file=sys.stderr,
        )
        return EXIT_USAGE
    try:
        database = _database(args.database)
        email = _address(args.email)
        leads = load_leads(args.input)
    except LeadsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"database: {database} {DATABASE_NOTE}", file=sys.stderr)
    ids = [lead_id for lead_id, stored in leads if stored == email]
    masked = mask_email(email)
    if not ids:
        print(
            "no lead matches the supplied address; nothing to delete",
            file=sys.stderr,
        )
        return EXIT_NO_MATCH
    if len(ids) > 1:
        print(
            f"error: {len(ids)} leads match the supplied address; expected one. "
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
        # The id must be one the export itself assigned to this very address.
        if (ids[0], email) not in leads:
            raise LeadsError("internal: the matched id is not in the export")
        statements = _sql(ids[0], email)
    except LeadsError as exc:  # pragma: no cover - guarded by load_leads
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    text = "\n".join(statements) + "\n"
    target = args.sql_out if args.sql_out is not None else "delete-lead.sql"
    if args.sql_out is not None:
        try:
            _write_private(args.sql_out, text)
        except LeadsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    quoted = shlex.quote(os.fspath(target))
    where = (
        f"The SQL is in {quoted} (new file, mode 0600). "
        if args.sql_out is not None
        else "The SQL is on stdout; prefer --sql-out, which creates a private "
        "file and refuses a path that exists or ends in a symlink. "
    )
    print(
        f"1 lead matches. {where}It holds the address, so delete it afterward. "
        "It deletes only a row in the live database that still pairs that id "
        f"with that address. Apply it to the leads database {database} only. "
        "Reading 0 and 0 means no remaining rows at this moment; it is not proof "
        "of completed erasure, because D1 offers no transaction in a file and "
        "an event can arrive after the read. Run the file again after a wait "
        "until two consecutive runs read 0 and 0 (leads_remaining 1 means "
        "nothing was deleted):\n"
        f"npx wrangler d1 execute {database} --remote --file {quoted}",
        file=sys.stderr,
    )
    if args.sql_out is None:
        sys.stdout.write(text)
    return EXIT_OK


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _remove(path: str) -> None:
    os.unlink(path)


def _write_private(path: Path, text: str) -> None:
    """Create ``path`` (mode 0600, exclusively) and write ``text`` to it.

    ``O_EXCL`` refuses a path that exists, and ``O_NOFOLLOW`` refuses a path
    whose LAST component is a symlink. Symlinks in the parent directories are
    followed (not checked): use a directory you made yourself. If anything fails
    after the file was created, the descriptor is closed and the file removed,
    because it may already hold the address; if it cannot be removed, the
    message names it.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, 0o600)
    except OSError:
        raise LeadsError(
            "cannot create the SQL file: the path must not exist and its last "
            "component must not be a symlink"
        ) from None
    try:
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, text.encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        try:
            _remove(os.fspath(path))
        except OSError:
            raise LeadsError(
                "cannot write the SQL file, and the partial file may remain at "
                f"{shlex.quote(os.fspath(path))}: delete it, it may hold the address"
            ) from None
        raise LeadsError(
            "cannot write the SQL file; the partial file was removed, no file remains"
        ) from None


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
        "prints the SQL. No network call, no file written; the SQL pairs "
        "the lead id with its address, so it deletes only a row that still "
        "matches both. --database and --email - are required. Exit 0 done, "
        "1 no match, 2 usage problem or a refused export.",
    )
    delete.add_argument("--input", type=Path, metavar="PATH", help="leads export, JSON")
    delete.add_argument(
        "--email",
        metavar="ADDRESS",
        help="must be - : the address is read from stdin; an address on the "
        "command line is refused",
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
    delete.add_argument(
        "--database",
        default=None,
        metavar="NAME",
        help=f"required: the database the export came from; only "
        f"{LEADS_DATABASE} is accepted (your attestation, not proof)",
    )
    delete.add_argument(
        "--sql-out",
        type=Path,
        default=None,
        metavar="PATH",
        help="with --yes: write the SQL to a new file (mode 0600, created "
        "exclusively; refused if the path exists or its last component is a "
        "symlink; parent directories are not checked) instead of stdout",
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
    show.add_argument(
        "--database",
        default=None,
        metavar="NAME",
        help=f"required: the database the export came from; only "
        f"{LEADS_DATABASE} is accepted (your attestation, not proof)",
    )
    show.set_defaults(func=run_show)
