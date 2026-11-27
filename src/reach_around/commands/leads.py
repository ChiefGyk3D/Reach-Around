# SPDX-License-Identifier: Apache-2.0
"""``reach-around leads delete --input EXPORT --email ADDRESS [--yes]``.

The owner's way to honor a deletion request for one media kit lead (a person who
typed an email address into the gate). It reads a local D1 export of the
``leads`` table, finds the one row for the address, and prints the SQL that
deletes that lead and its kit events; the owner applies the printed SQL with
``wrangler d1 execute`` (docs/privacy.md, "Deleting one lead"). The dry run is
offline. With ``--yes`` the command also clears the ``alert_dedup`` hash table
(a keyed hash of recent alerts, which can include the address), in two steps:

1. ``--yes --alerts-database NAME --alerts-database-id UUID --leads-database-id
   UUID``: a read-only check of both databases (each name must resolve to the
   id the owner copied from the Worker's own configuration, and the schema must
   fit: the alert database holds ``alert_dedup`` and not the lead tables unless
   it is the leads database itself), then the lead SQL and its apply command
   are printed. Nothing is changed by this tool, and it exits 10: the checks
   passed and the erasure is NOT done.
2. After the owner applied the SQL, the same command with ``--finish``: it
   reads the lead tables back for the id AND for the address (must be 0 and
   0), checks again, clears ``alert_dedup`` with one statement that deletes only
   while the alert database holds no lead tables, reads it back (must be 0),
   waits a moment, reads it back again, checks the identity and schema a last
   time and reads the lead tables once more for the address (a lead created
   again after the erasure started fails the run). Only this step can exit 0.
   Precondition, not checked by this tool (the kill switch is a Worker setting
   it cannot read): the kit gate must be closed for the whole erasure.

Database identity. Every remote statement runs between two identity checks of
the same database name (``d1 info NAME`` before and after), and a changed id
discards the answer. ``wrangler d1 execute`` takes a database NAME or binding
(documented, read 2026-11-26), not an id, and its JSON result is not documented
to carry the database id, so nothing proves that a given execute call ran on the
database the checks named. The checks bind the name to the id the Worker's own
configuration names at the moment of each check; a name that is re-pointed to
another database and back inside one call is NOT detected. That window is a
residual risk of this design.

Trust boundary. The tool hands the Cloudflare token to the wrangler it runs,
which is code the operator already trusts with that token (same OS user). A
version printout is not an integrity check, so the tool does not run the
wrangler to ask its version. What it enforces: the file must resolve, with no
symlink escape, inside the repository's ``worker/node_modules/wrangler``
package (named from ``worker/node_modules/.bin``); the package's own
``package.json`` name and version and npm's install record
(``node_modules/.package-lock.json``) must agree with
``worker/package-lock.json``; the file and every directory above it, and every
file and directory of the wrangler package and of the packages it depends on
(resolved by node's rules under ``worker/node_modules``, bounded in size), must
belong to root or the current user, and none may be writable by others (world
write is refused everywhere) or by a group that anyone else belongs to. A group
write bit is accepted only when the group is your effective primary group (not a
supplementary one), lists no member but you, is no other account's primary
group, and the ``passwd`` and ``group`` lines of /etc/nsswitch.conf name exactly
``files`` (so the enumeration is complete; ``systemd`` and any directory service
can answer a targeted lookup without being enumerable, and an unreadable file or
a lookup error refuses too). That is the user-private group of
a normal account with umask 002, so an ordinary checkout and ``npm ci`` are
accepted as they are; otherwise run ``chmod -R g-w worker/node_modules``.
Outside the repository a sticky shared directory owned by root (such as
``/tmp``) is accepted too. A required dependency (``dependencies``,
non-optional peers) that is not installed under ``worker/node_modules`` is
refused, as is a ``node_modules`` in the repository root or any directory above
it (or a legacy module folder in HOME); a symlink (to a file or a directory)
must resolve inside ``worker/node_modules``, the directories between its target
and the repository root are checked, and a directory target is walked.

Installation mode. Only the layout made by ``cd worker && npm ci
--ignore-scripts`` is supported: the lock file is ``worker/package-lock.json`` and
every package of the wrangler closure sits under ``worker/node_modules``.
Hoisted and workspace installs (a ``node_modules`` in the repository root, or a
required package found only above ``worker``) are refused on purpose, with a
message that names that command; hoisting is not supported.

Only the walked tree is checked: a package
that is not named by a ``dependencies`` field is not seen. What it does NOT
prove: that the files on disk are the files of the published tarball (npm keeps
no per-file hashes, and the tarball digest in the lock file cannot be re-checked
without downloading it), that ``node`` is genuine, or that nothing changes the
files between the check and the run. Anyone who can write as the same OS user
can defeat all of it.

The tool runs one wrangler (never found through PATH or ``npx``) with a minimal
environment (the token, the account id, HOME and a fixed PATH), a neutral working
directory, no shell and no stdin, one statement per call, bounded output, and
accepts exactly one well-formed result block per call. The child runs in its own
process group; on the output cap or the time limit the whole group is killed. It
never repeats remote text.

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

Exit codes: 0 the erasure verified by ``--finish`` (it is also the code of a
dry run, which requests nothing and changes nothing, and of ``--checklist``);
10 ``--yes`` step 1: the checks passed and the SQL was printed, the erasure is
NOT done; 1 no lead matches; 2 usage problem, a refused export, or a refused
alert database, database id or wrangler (step 1, or before ``--finish`` makes
any remote call; this tool changed nothing); 3 the lead rows were read as gone
but the alert hashes could not be cleared and read back as 0, a database changed
or a check failed after that point, or a lead for the address exists again (the
erasure is NOT complete); 4 ``--finish`` could not confirm that the lead rows
are gone (any failure before that, the alert table was not touched). Once
``--finish`` is running it never returns 2. Only ``--yes --finish`` can return 0
for an erasure.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import pwd
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reach_around.config.loader import ConfigError, _read

EXIT_OK = 0
EXIT_NO_MATCH = 1
EXIT_USAGE = 2
EXIT_ALERTS_INCOMPLETE = 3
EXIT_LEADS_UNCONFIRMED = 4
# Step 1 (`--yes`): the checks passed and the SQL was printed. NOT an erasure.
EXIT_CHECKED = 10

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


# `leads show` takes two files, each from its own `wrangler d1 execute --json
# --command "<one statement>"` call. VERIFIED 2026-11-27 (wrangler 4.162.0, real D1,
# synthetic data): one statement gives ONE result block. A file with any other number
# of blocks, or the wrong kind of rows, is a wrong export and is refused.
_ONE_SET = (
    "export: expected exactly one result set from a single statement "
    "(the leads query in --input, the kit events query in --events; the "
    "events set may be empty)"
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
            raise LeadsError(_ONE_SET)
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
    leads_path: Path, events_path: Path
) -> tuple[dict[str, str], dict[str, dict[str, Any]], list[tuple[str, str, int]]]:
    """``(email by id, lead fields by id, events)`` from the leads export and the kit
    events export, each the one result block of its own single-statement call."""
    emails: dict[str, str] = {}
    fields: dict[str, dict[str, Any]] = {}
    events: list[tuple[str, str, int]] = []
    lead_blocks = _load_blocks(leads_path, wrapped=True)
    event_blocks = _load_blocks(events_path, wrapped=True)
    if len(lead_blocks) != 1 or len(event_blocks) != 1:
        raise LeadsError(_ONE_SET)
    lead_rows, event_rows = lead_blocks[0], event_blocks[0]
    # An event row in the leads result (or the reverse) is a wrong export.
    if any(isinstance(r, dict) and "lead_id" in r for r in lead_rows) or any(
        not (isinstance(r, dict) and "lead_id" in r) for r in event_rows
    ):
        raise LeadsError(_ONE_SET)
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
)
# The read-back is NOT in the file: `d1 execute --file` returns one summary row and no
# per-statement rows (wrangler 4.162.0 on real D1, checked 2026-11-27), so a SELECT in
# the file would show nothing. It runs as its own `--command`, whose one block holds the
# row. It names the id only, never the address.
_READ_BACK = (
    "SELECT (SELECT COUNT(*) FROM leads WHERE id = '<ID>') AS leads_remaining, "
    "(SELECT COUNT(*) FROM kit_events WHERE lead_id = '<ID>') AS events_remaining;"
)
_SQL_EMAIL = re.compile(r"[a-z0-9_+.@-]{6,254}")
# The read-back `--finish` uses. The printed file's read-back asks only about the
# exported id; this one asks about the ADDRESS too, so a lead created again for
# the same address (a new id) is seen: the erasure subject is the person.
#
# The address is compared in the form the store keeps it: the Worker stores
# trimmed ASCII lower-case addresses only, and the leads table refuses any other
# spelling (worker/migrations/0009_kit.sql: `email = lower(email)` and a character
# class check), with no plus or dot folding. The read-back also compares the
# stored value trimmed and lower-cased, so a copy of the row written with another
# case or surrounding white space (an older or hand-made row) is still counted.
# Nothing wider: no plus-address or dot folding, and SQLite's lower() and trim()
# touch ASCII only, so a look-alike character never matches.
_STORED_EMAIL = "lower(trim(email, char(9, 10, 11, 12, 13, 32)))"
_FINISH_READBACK = (
    "SELECT (SELECT COUNT(*) FROM leads WHERE id = '<ID>' "
    "OR <STORED> = '<EMAIL>') "
    "AS leads_remaining, (SELECT COUNT(*) FROM kit_events WHERE lead_id = '<ID>' "
    "OR lead_id IN (SELECT id FROM leads WHERE <STORED> = '<EMAIL>')) "
    "AS events_remaining"
).replace("<STORED>", _STORED_EMAIL)

# The alert dedup table keeps a keyed hash of recent alerts, which can include
# the address (worker/migrations/0013_alerts.sql: site, event_hash, expires_at).
# `--finish` clears the whole table (not only expired rows) with the constant
# statements below. Clearing every row only resets repeat-alert suppression for
# up to 10 minutes. The table has no creation column: a row's creation time is
# expires_at minus the Worker's dedup life (DEDUP_MS in worker/src/alerts/
# dispatcher.ts, 10 minutes), which the read-back uses to name rows made after
# the erasure started.
ALERT_TABLE = "alert_dedup"
ALERT_TTL_MS = 600_000
ALERT_CLEAR_SQL = "DELETE FROM alert_dedup"
# Used when the alert database is a different database from the leads one: the
# same delete, but only while this database holds no lead table, decided in the
# same remote statement as the delete (so a schema that changed after the check
# deletes nothing; the final re-check then reports it).
ALERT_CLEAR_SPLIT_SQL = (
    "DELETE FROM alert_dedup WHERE NOT EXISTS (SELECT 1 FROM sqlite_master "
    "WHERE type = 'table' AND name IN ('leads', 'kit_events'))"
)
ALERT_READBACK_SQL = "SELECT COUNT(*) AS alert_dedup_remaining FROM alert_dedup"
_READBACK_TEMPLATE = (
    "SELECT COUNT(*) AS alert_dedup_remaining, "
    "COALESCE(SUM(expires_at > <T>), 0) AS alert_dedup_new FROM alert_dedup"
)
ALERT_ATTEMPTS = 3
ALERT_SETTLE_SECONDS = 2.0
_DATABASE_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _checked_database(database: str) -> str:
    """The database name, only if it is a plain identifier (it reaches argv and
    printed shell text)."""
    if not _DATABASE_NAME.fullmatch(database):
        raise LeadsError("a database name must be 1 to 64 letters, digits, '_' or '-'")
    return database


def alert_readback_sql(started_ms: int) -> str:
    """The read-back: all rows left, and the rows made after ``started_ms`` (their
    expires_at is later than the start plus the dedup life). Both read 0 on an
    empty table. ``started_ms`` is an integer the tool computed, never remote text."""
    if isinstance(started_ms, bool) or not isinstance(started_ms, int):
        raise LeadsError("internal: the start time must be an integer")
    threshold = max(0, started_ms) + ALERT_TTL_MS
    return _READBACK_TEMPLATE.replace("<T>", str(threshold))


# ---------------------------------------------------------------------------
# The online part: one pinned wrangler, minimal environment, strict parsing.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
WRANGLER_TIMEOUT = 120
MAX_WRANGLER_BYTES = 1024 * 1024
MAX_RESULT_ROWS = 50
INSTALL_WRANGLER = "cd worker && npm ci --ignore-scripts"
_LEAD_TABLES = frozenset({"leads", "kit_events"})
_SCHEMA_SQL = (
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
    "('alert_dedup', 'leads', 'kit_events') ORDER BY name"
)
_PIN = re.compile(r"\d+\.\d+\.\d+")
_WR_KEY = "node_modules/wrangler"
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


class WranglerError(LeadsError):
    """The wrangler step failed or answered in a way that is not trusted. The
    message is fixed text; remote output is never repeated."""


def _kill_group(proc: Any) -> None:
    """SIGKILL the child's whole process group (the child leads it, so the group
    id is its pid), then reap the child. The group is signalled before the child
    is reaped, so its id cannot have been reused yet. The plain kill is the
    fallback when the group cannot be signalled."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass
    proc.wait()


def _exec(argv: list[str], env: dict[str, str], timeout: int) -> tuple[int, bytes]:
    """The one place a process starts: argv list, no shell, no stdin, a neutral
    working directory (no wrangler config is picked up from where the operator
    stands), stderr dropped (it can carry remote text). Stdout is read in chunks
    with a hard cap: the child is killed as soon as MAX_WRANGLER_BYTES is passed
    or the time is up, so memory stays bounded. The child is its own session and
    process group, and on the cap, the time limit or any error the whole group is
    killed (SIGKILL) before the child is reaped, so a descendant that kept the
    token or the pipe does not outlive the call. A descendant that left the
    group on its own (a new session of its own) is not reached."""
    proc = subprocess.Popen(  # noqa: S603 - argv list, validated absolute binary
        argv,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd="/",
        start_new_session=True,
    )
    deadline = time.monotonic() + timeout
    chunks: list[bytes] = []
    total = 0
    try:
        if proc.stdout is None:
            raise WranglerError("wrangler gave no output channel")
        fd = proc.stdout.fileno()
        with selectors.DefaultSelector() as sel:
            sel.register(fd, selectors.EVENT_READ)
            while True:
                left = deadline - time.monotonic()
                if left <= 0 or not sel.select(left):
                    raise subprocess.TimeoutExpired(argv, timeout)
                data = os.read(fd, 65536)
                if not data:
                    break
                total += len(data)
                if total > MAX_WRANGLER_BYTES:
                    raise WranglerError("wrangler printed more than the allowed size")
                chunks.append(data)
        code = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except BaseException:
        _kill_group(proc)
        raise
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
    return code, b"".join(chunks)


def _wrangler_env() -> dict[str, str]:
    """Only what wrangler needs: the token and account when set, HOME for its own
    login state, and a fixed PATH (the pinned script's shebang finds node there).
    Nothing else is passed on, so a NODE_PATH, NODE_OPTIONS or NPM_CONFIG_* value of
    the parent never reaches the child (they would change where node loads code
    from)."""
    env = {"PATH": "/usr/bin:/bin"}
    for key in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "HOME"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _pinned_entry(lock: Path, key: str) -> dict[str, Any]:
    """One ``packages`` entry of an npm lock file, as a dict."""
    try:
        data = json.loads(_read(lock, MAX_EXPORT_BYTES * 2))
        entry = data["packages"][key]
    except (ConfigError, ValueError, KeyError, TypeError):
        raise WranglerError(
            "cannot read the wrangler entry of the npm lock files; install it "
            f"with: {INSTALL_WRANGLER}"
        ) from None
    if not isinstance(entry, dict):
        raise WranglerError("the npm lock file has no usable wrangler entry")
    return entry


def _pinned_version() -> str:
    """The wrangler version this repository pins (worker/package-lock.json)."""
    version = _pinned_entry(REPO_ROOT / "worker" / "package-lock.json", _WR_KEY).get(
        "version"
    )
    if not isinstance(version, str) or not _PIN.fullmatch(version):
        raise WranglerError("worker/package-lock.json has no usable wrangler pin")
    return version


NSSWITCH_CONF = "/etc/nsswitch.conf"
_LOCAL_NSS_SOURCES = frozenset({"files"})  # round 6: systemd is not enumerable


def _read_nsswitch() -> str:
    """The text of the name service switch file (tests replace this function)."""
    with open(NSSWITCH_CONF, encoding="utf-8", errors="replace") as handle:
        return handle.read(65536)


def _nss_is_local() -> bool:
    """True only when the ``passwd`` and ``group`` lines of nsswitch.conf each
    appear once and name exactly ``files`` (bracketed action clauses are
    ignored). ``systemd`` (systemd-userdb can answer targeted lookups for
    accounts that enumeration never lists) and any directory service (sss, ldap,
    winbind, nis, ...) can make a group look private or hide its members from the
    enumeration below, so anything else, an unreadable file or a line this cannot
    parse counts as not local (fail closed)."""
    try:
        text = _read_nsswitch()
    except Exception:
        return False
    for database in ("passwd", "group"):
        lines = [
            line.split("#", 1)[0].strip()
            for line in text.splitlines()
            if line.split("#", 1)[0].strip().startswith(database + ":")
        ]
        if len(lines) != 1:
            return False
        rest = re.sub(r"\[[^\]]*\]", " ", lines[0].split(":", 1)[1])
        sources = rest.split()
        if not sources or not set(sources) <= _LOCAL_NSS_SOURCES:
            return False
    return True


def _group_is_private(gid: int) -> bool:
    """True when a group write bit lets no other account write: the group is the
    process's EFFECTIVE primary group (not a supplementary one), its member list
    is empty or only you, no other account has it as its primary group (the user
    private group many systems give each account), and the account and group
    databases are local files (``_nss_is_local``), so the enumeration is
    complete. Any error from the group or account lookups is a refusal."""
    try:
        if gid != os.getegid() or not _nss_is_local():
            return False
        uid = os.geteuid()
        me = pwd.getpwuid(uid).pw_name
        if not set(grp.getgrgid(gid).gr_mem) <= {me}:
            return False
        return all(
            account.pw_uid == uid for account in pwd.getpwall() if account.pw_gid == gid
        )
    except Exception:
        return False


def _inside(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or below it (both already real paths)."""
    return path == root or path.startswith(os.path.join(root, ""))


def _unwritable_by_others(
    info: os.stat_result, *, directory: bool, path: str = "", sealed: bool = False
) -> bool:
    """Not writable by group (unless that group is private, see below) or others.

    A group write bit is accepted anywhere when the group is private (see
    ``_group_is_private``: a normal checkout and install on an account with its
    own group and umask 002 are group-writable by that group alone); an other
    write bit never is inside the repository. ``sealed`` (the path is inside the
    repository) also removes the sticky exemption: outside the repository, a
    directory with the sticky bit (such as /tmp, mode 1777) is accepted only when
    root owns it, because nobody can rename or remove an entry they do not own
    there, so the entries below it stay ours."""
    mode = info.st_mode
    if not sealed and directory and mode & stat.S_ISVTX and info.st_uid == 0:
        return True
    if mode & stat.S_IWGRP and not _group_is_private(info.st_gid):
        return False
    return not mode & stat.S_IWOTH


MAX_TREE_ENTRIES = 200_000
_PKG_NAME = re.compile(r"(?:@[a-z0-9._-]+/)?[a-z0-9._-]+")
_REFUSED_TREE = (
    "the wrangler package or one it depends on has a file or directory that "
    "belongs to another account or is writable by group or others; refusing it "
    "(only a local files-only identity database is accepted for a group write "
    "bit; fix with: chmod -R go-w worker/node_modules; for a group write bit only, "
    "chmod -R g-w worker/node_modules)"
)


def _check_entry(
    path: str, me: int, repo_real: str, node_root: str | None = None
) -> os.stat_result:
    """One file or directory of the walked tree: owner root or the current user,
    and no other write bit and no write bit for a group that is not private to
    you (``_unwritable_by_others``). A symlink's own mode
    means nothing, so the target is checked: it must stay in the repository and,
    when ``node_root`` is given, inside it (the real ``worker/node_modules``), and
    every directory between the target and the repository root is checked too (a
    writable parent would let another account swap the target file)."""
    link = os.lstat(path)
    if stat.S_ISLNK(link.st_mode):
        target = os.path.realpath(path, strict=True)
        if not _inside(target, repo_real) or (
            node_root is not None and not _inside(target, node_root)
        ):
            raise WranglerError(_REFUSED_TREE)
        _check_above(target, me, repo_real)
        link = os.stat(target)
    if link.st_uid not in (0, me) or not _unwritable_by_others(
        link, directory=stat.S_ISDIR(link.st_mode), path=path, sealed=True
    ):
        raise WranglerError(_REFUSED_TREE)
    return link


def _check_above(path: str, me: int, repo_real: str) -> None:
    """Every directory from the parent of ``path`` up to the repository root."""
    up = os.path.dirname(path)
    while _inside(up, repo_real):
        _check_entry(up, me, repo_real)
        if up == repo_real:
            break
        up = os.path.dirname(up)


def _walk_package(
    pkg: str,
    me: int,
    repo_real: str,
    budget: list[int],
    node_root: str,
    seen_dirs: set[str],
) -> None:
    """Check every entry of one package directory, except nested ``node_modules``
    (those packages are checked when something names them). A symlink to a
    directory is resolved: its target must stay inside ``node_root`` (the real
    ``worker/node_modules``), the directories between the target and the
    repository root are checked, and the target is walked like any directory (the
    set ``seen_dirs``, shared by the whole walk, catches loops, and the entries
    count against the same bound). The entry count is bounded: past the bound the
    check fails closed."""
    stack = [pkg]
    while stack:
        here = stack.pop()
        if here in seen_dirs:
            continue
        seen_dirs.add(here)
        _check_entry(here, me, repo_real, node_root)
        with os.scandir(here) as entries:
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise WranglerError(
                        "the wrangler dependency tree is larger than the bound "
                        f"({MAX_TREE_ENTRIES} entries); refusing it"
                    )
                if entry.is_dir(follow_symlinks=False):
                    if entry.name != "node_modules":
                        stack.append(entry.path)
                elif entry.is_symlink() and entry.is_dir():
                    target = os.path.realpath(entry.path, strict=True)
                    if not _inside(target, node_root):
                        raise WranglerError(_REFUSED_TREE)
                    _check_entry(entry.path, me, repo_real, node_root)
                    if entry.name != "node_modules":
                        stack.append(target)
                else:
                    _check_entry(entry.path, me, repo_real, node_root)


def _dependency_names(pkg: str) -> list[tuple[str, bool]]:
    """The package names a package lists for node to load, each with whether it is
    required: ``dependencies`` and the peer dependencies not marked optional in
    ``peerDependenciesMeta`` are required; ``optionalDependencies`` and optional
    peers may be absent (a platform package). An unreadable or malformed
    package.json is a refusal."""
    try:
        meta = json.loads(_read(Path(pkg) / "package.json", MAX_EXPORT_BYTES))
    except (ConfigError, ValueError):
        raise WranglerError(_REFUSED_TREE) from None
    if not isinstance(meta, dict):
        raise WranglerError(_REFUSED_TREE)
    peer_meta = meta.get("peerDependenciesMeta")
    if peer_meta is not None and not isinstance(peer_meta, dict):
        raise WranglerError(_REFUSED_TREE)
    # A name listed in more than one field is merged, not counted twice: npm lets
    # ``optionalDependencies`` override a same-named ``dependencies`` entry, and a
    # dependency (either kind) outranks a peer entry.
    merged: dict[str, bool] = {}
    for field in ("peerDependencies", "dependencies", "optionalDependencies"):
        group = meta.get(field)
        if group is None:
            continue
        if not isinstance(group, dict):
            raise WranglerError(_REFUSED_TREE)
        for name in group:
            if not isinstance(name, str) or not _PKG_NAME.fullmatch(name):
                raise WranglerError(_REFUSED_TREE)
            required = field == "dependencies"
            if field == "peerDependencies":
                note = (peer_meta or {}).get(name)
                required = not (isinstance(note, dict) and note.get("optional") is True)
            merged[name] = required
    return list(merged.items())


def _check_no_ancestor_node_modules(repo_real: str) -> None:
    """Refuse when node would also search a ``node_modules`` outside the checked
    install: one in the repository root or any directory above it (node walks up
    to ``/``), or in the account's own legacy module folders under HOME. Those
    are never walked, so a package found there would run with the token
    unchecked. Only the existence is looked at; the message names no path."""
    up = repo_real
    while True:
        if os.path.lexists(os.path.join(up, "node_modules")):
            raise WranglerError(
                "a node_modules directory exists in the repository root or in a "
                "directory above it; node would search it, and it is not checked. "
                "Only the layout made by "
                f"{INSTALL_WRANGLER} is supported (packages under "
                "worker/node_modules, lock file worker/package-lock.json); "
                "hoisted and workspace installs are refused. Move that directory "
                "away or run from a checkout without one"
            )
        parent = os.path.dirname(up)
        if parent == up:
            break
        up = parent
    home = os.environ.get("HOME")
    for legacy in (".node_modules", ".node_libraries"):
        if home and os.path.lexists(os.path.join(home, legacy)):
            raise WranglerError(
                "a legacy node module folder exists in the home directory "
                "(.node_modules or .node_libraries); node would search it, and it "
                "is not checked. Move it away"
            )


def _find_package(name: str, start: str, worker_real: str) -> str | None:
    """Where node would load ``name`` from when asked by the package at ``start``:
    ``node_modules/NAME`` of the package, then of each parent up to ``worker``.
    None when it is not installed (an optional or platform package)."""
    here = start
    while _inside(here, worker_real):
        base = here if os.path.basename(here) == "node_modules" else None
        cand = os.path.join(base or os.path.join(here, "node_modules"), name)
        if os.path.isdir(cand):
            return os.path.realpath(cand)
        if here == worker_real:
            break
        here = os.path.dirname(here)
    return None


def _check_package_tree(pkg_real: str, worker_real: str, me: int) -> None:
    """Walk the wrangler package and the closure of the packages it depends on
    (resolved under worker/node_modules, bounded), checking each entry and each
    directory between a package and ``worker``. Only this walked tree is checked.

    A dependency a package requires (``dependencies``, non-optional peers) that is
    not installed under ``worker/node_modules`` is a refusal, because node would
    look for it in the directories above ``worker`` too; and a ``node_modules``
    above ``worker`` is a refusal for the same reason (see
    ``_check_no_ancestor_node_modules``)."""
    repo_real = os.path.realpath(REPO_ROOT)
    _check_no_ancestor_node_modules(repo_real)
    node_root = os.path.join(worker_real, "node_modules")
    budget = [MAX_TREE_ENTRIES]
    seen: set[str] = set()
    seen_dirs: set[str] = set()
    queue = [pkg_real]
    while queue:
        pkg = queue.pop()
        if pkg in seen:
            continue
        seen.add(pkg)
        if not _inside(pkg, worker_real):
            raise WranglerError(_REFUSED_TREE)
        try:
            up = os.path.dirname(pkg)
            while _inside(up, worker_real) and up != worker_real:
                _check_entry(up, me, repo_real)
                up = os.path.dirname(up)
            _walk_package(pkg, me, repo_real, budget, node_root, seen_dirs)
            for name, required in _dependency_names(pkg):
                found = _find_package(name, pkg, worker_real)
                if found is not None:
                    queue.append(found)
                elif required:
                    raise WranglerError(
                        f"the dependency {name} is required by an installed "
                        "package but is not installed under worker/node_modules; "
                        "only the layout made by "
                        f"{INSTALL_WRANGLER} is supported (hoisted and workspace "
                        "installs are refused); reinstall with that command"
                    )
        except OSError:
            raise WranglerError(_REFUSED_TREE) from None


def _resolve_wrangler(arg: Path | None) -> str:
    """The wrangler to run: the repository's own install, never a PATH search and
    never an arbitrary executable. The path (``--wrangler``, or the default
    ``worker/node_modules/.bin/wrangler``) must be an absolute path inside the
    repository's ``worker/node_modules/.bin`` or ``worker/node_modules/wrangler``,
    and its real path (symlinks resolved) must stay inside the
    ``worker/node_modules/wrangler`` package. That package's ``package.json`` and
    npm's install record must name wrangler at the version and tarball digest of
    ``worker/package-lock.json``. The file and every directory from the root down
    must belong to root or the current user (the file too: a root-owned file is
    accepted) and not be writable by others, nor by a group another account
    belongs to (a group write bit is accepted only for your effective primary
    group under local account files; see ``_group_is_private``). The
    wrangler package and its dependency closure are walked the same
    way (``_check_package_tree``); only the walked tree is checked.

    Not proven (this is a trust boundary, not an integrity check): that the files
    are the files of the published tarball, that node is genuine, or that
    nothing changes them before the run. The wrangler is never executed here."""
    base = os.path.realpath(REPO_ROOT / "worker" / "node_modules")
    bin_dir = os.path.join(base, ".bin")
    pkg_dir = os.path.join(base, "wrangler")
    text = os.fspath(REPO_ROOT / "worker" / "node_modules" / ".bin" / "wrangler")
    if arg is not None:
        text = os.fspath(arg)
    if not os.path.isabs(text) or _CONTROL.search(text) or "\x00" in text:
        raise WranglerError(
            "--wrangler must be an absolute path with no control characters"
        )
    plain = os.path.normpath(REPO_ROOT / "worker" / "node_modules")
    allowed = (
        bin_dir,
        pkg_dir,
        os.path.join(plain, ".bin"),
        os.path.join(plain, "wrangler"),
    )
    inside = [os.path.join(p, "") for p in allowed]
    if not any(os.path.normpath(text).startswith(p) for p in inside):
        raise WranglerError(
            "--wrangler must name a file in this repository's worker/node_modules"
            "/.bin or worker/node_modules/wrangler; install it with: "
            f"{INSTALL_WRANGLER}"
        )
    try:
        real = os.path.realpath(text, strict=True)
        info = os.stat(real)
        real_pkg = os.path.realpath(pkg_dir, strict=True)
    except OSError:
        raise WranglerError(
            f"the pinned wrangler is not installed; install it with: {INSTALL_WRANGLER}"
        ) from None
    if not real.startswith(os.path.join(real_pkg, "")):
        raise WranglerError(
            "the wrangler path resolves outside the worker/node_modules/wrangler "
            "package; refusing it"
        )
    me = os.geteuid()
    repo_real = os.path.realpath(REPO_ROOT)
    if (
        not stat.S_ISREG(info.st_mode)
        or not info.st_mode & stat.S_IXUSR
        or info.st_uid not in (0, me)
        or not _unwritable_by_others(
            info, directory=False, path=real, sealed=_inside(real, repo_real)
        )
    ):
        raise WranglerError(
            "wrangler must be a regular executable owned by root or you, not "
            "writable by group or others (alternative: chmod -R g-w "
            "worker/node_modules)"
        )
    walk = Path(real).parent
    try:
        for directory in [walk, *walk.parents]:
            dinfo = os.stat(directory)
            if dinfo.st_uid not in (0, me) or not _unwritable_by_others(
                dinfo,
                directory=True,
                path=os.fspath(directory),
                sealed=_inside(os.fspath(directory), repo_real),
            ):
                raise WranglerError(
                    "every directory above wrangler must belong to root or you and "
                    "not be writable by others or by a group another account "
                    "belongs to (only your own primary group, with no other member "
                    "and on a machine whose passwd and group lines name only "
                    "local files, since only a local files-only identity "
                    "database is accepted, is "
                    "fine; fix with: chmod -R go-w worker/node_modules, or "
                    "chmod -R g-w worker/node_modules)"
                )
    except OSError:
        raise WranglerError("a directory above wrangler could not be checked") from None
    _check_install_record(real_pkg, os.path.join(base, ".package-lock.json"))
    _check_package_tree(real_pkg, os.path.dirname(base), me)
    return real


def _check_install_record(pkg: str, hidden_lock: str) -> None:
    """The installed package and npm's own record of the install must agree with
    the repository's lock file (name, version, tarball digest). A consistency
    check of metadata: it does not hash the installed files."""
    lock = _pinned_entry(REPO_ROOT / "worker" / "package-lock.json", _WR_KEY)
    version, integrity = _pinned_version(), lock.get("integrity")
    bad = WranglerError(
        "the installed wrangler does not match worker/package-lock.json; "
        f"reinstall it with: {INSTALL_WRANGLER}"
    )
    if not isinstance(integrity, str) or not integrity:
        raise WranglerError("worker/package-lock.json has no wrangler digest")
    try:
        meta = json.loads(_read(Path(pkg) / "package.json", MAX_EXPORT_BYTES))
        record = _pinned_entry(Path(hidden_lock), _WR_KEY)
    except (ConfigError, ValueError):
        raise bad from None
    if (
        not isinstance(meta, dict)
        or meta.get("name") != "wrangler"
        or meta.get("version") != version
        or record.get("version") != version
        or record.get("integrity") != integrity
    ):
        raise bad


def _wrangler_json(wrangler: str, args: list[str]) -> Any:
    """Run wrangler with ``args`` and return its stdout parsed as strict JSON
    (no duplicate keys, no NaN). Any failure is a fixed message."""
    failed = WranglerError("wrangler failed or its answer was not accepted")
    try:
        code, out = _exec([wrangler, *args], _wrangler_env(), WRANGLER_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise failed from None
    if code != 0 or len(out) > MAX_WRANGLER_BYTES:
        raise failed
    try:
        return json.loads(
            out.decode("utf-8"),
            object_pairs_hook=_no_duplicates,
            parse_constant=_bad_constant,
        )
    except (ValueError, RecursionError):
        raise failed from None


def database_uuid(wrangler: str, database: str) -> str:
    """The id Cloudflare gives the database NAME (``wrangler d1 info NAME
    --json``), lowercase. The output shape is not documented on the commands page
    (checked 2026-11-26); it is read as one JSON object, or a list of one such
    object, with a ``uuid`` string, and anything else is refused."""
    name = _checked_database(database)
    data = _wrangler_json(wrangler, ["d1", "info", name, "--json"])
    if isinstance(data, list) and len(data) == 1:
        data = data[0]
    uuid = data.get("uuid") if isinstance(data, dict) else None
    if not isinstance(uuid, str) or not _UUID.fullmatch(uuid):
        raise WranglerError("the database id answer was not accepted")
    return uuid.lower()


def _checked_uuid(value: str | None, option: str) -> str:
    if value is None or not _UUID.fullmatch(value):
        raise LeadsError(f"{option} must be the database id (a UUID) from the Worker")
    return value.lower()


class IdentityError(LeadsError):
    """A database name resolved to an id other than the one the owner gave."""


def verify_identity(wrangler: str, name: str, expected: str) -> None:
    """The database NAME must resolve to the id the owner took from the Worker's
    own configuration, at the moment of this call. That binds the name to the
    database the Worker's configuration names; it cannot bind a later
    ``d1 execute NAME`` to it (execute takes a name or binding, not an id, and
    its JSON answer is not documented to carry the id: Cloudflare D1 wrangler
    commands page, read 2026-11-26). See ``_bound_rows`` for what is done about
    that and what is left."""
    if database_uuid(wrangler, name) != expected:
        raise IdentityError(
            "a database name does not resolve to the id you gave (a different, "
            "renamed or decoy database)"
        )


def _bound_rows(
    wrangler: str, database: str, expected: str, sql: str
) -> list[dict[str, Any]]:
    """One statement between two identity checks of the same name (check, call,
    check). An id that differs at either check discards the answer, so a name
    that was re-pointed and STAYED re-pointed past the call is caught. A name
    re-pointed to another database and back inside the one call is not: nothing
    available to this tool proves which database an execute call used (residual
    window, stated in docs/privacy.md)."""
    verify_identity(wrangler, database, expected)
    rows = _wrangler_rows(wrangler, database, sql)
    verify_identity(wrangler, database, expected)
    return rows


def _wrangler_rows(wrangler: str, database: str, sql: str) -> list[dict[str, Any]]:
    """Run ONE statement through the pinned wrangler and return its rows. Exactly
    one result block with ``success: true`` is accepted; anything else is a
    failure with a fixed message."""
    failed = WranglerError("wrangler failed or its answer was not accepted")
    data = _wrangler_json(
        wrangler,
        ["d1", "execute", _checked_database(database), "--remote", "--json"]
        + ["--command", sql],
    )
    if not isinstance(data, list) or len(data) != 1:
        raise failed
    blk = data[0]
    if (
        not isinstance(blk, dict)
        or "results" not in blk
        or not set(blk) <= {"results", "success", "meta"}
        or blk.get("success") is not True
    ):
        raise failed
    rows = blk["results"]
    if (
        not isinstance(rows, list)
        or len(rows) > MAX_RESULT_ROWS
        or not all(isinstance(r, dict) for r in rows)
    ):
        raise failed
    return rows


def _counts(rows: list[dict[str, Any]], names: tuple[str, ...]) -> tuple[int, ...]:
    """The one row of counts: exactly these columns, each a non-negative integer."""
    bad = WranglerError("a count answer was not accepted")
    if len(rows) != 1 or set(rows[0]) != set(names):
        raise bad
    out: list[int] = []
    for name in names:
        value = rows[0][name]
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not (0 <= value < 10**9)
        ):
            raise bad
        out.append(value)
    return tuple(out)


def _tables(wrangler: str, database: str, expected: str) -> frozenset[str]:
    rows = _bound_rows(wrangler, database, expected, _SCHEMA_SQL)
    names: list[str] = []
    for row in rows:
        name = row.get("name")
        if set(row) != {"name"} or name not in _LEAD_TABLES | {ALERT_TABLE}:
            raise WranglerError("a schema answer was not accepted")
        names.append(name)
    if len(set(names)) != len(names):
        raise WranglerError("a schema answer was not accepted")
    return frozenset(names)


def preflight_alerts(
    wrangler: str, leads_db: str, alerts_db: str, leads_id: str, alerts_id: str
) -> None:
    """Read-only. Prove, with every schema read wrapped in two identity checks
    (``_bound_rows``), that both names resolve to the ids the owner took from the
    Worker's configuration and, from the schemas, that ``alerts_db`` is where the
    alert table lives and that the lead tables are where the lead SQL goes. An
    absent alert table is a refusal, never a clear: this tool cannot know whether
    the site ever had one, so it never reports nothing-to-clear. The messages say
    nothing about what was changed: the caller knows."""
    _checked_database(alerts_db)
    if (alerts_db == leads_db) != (alerts_id == leads_id):
        raise LeadsError(
            "one database cannot have two ids and two databases cannot share one: "
            "the two names and the two ids disagree"
        )
    leads_tables = _tables(wrangler, leads_db, leads_id)
    if not _LEAD_TABLES <= leads_tables:
        raise LeadsError(
            "the leads database does not hold the leads and kit_events tables"
        )
    if alerts_db == leads_db:
        # One database holds both (a single DB binding): the alert table must be
        # in this very database.
        if ALERT_TABLE not in leads_tables:
            raise LeadsError(
                "the alert_dedup table is not in the leads database; refusing to "
                "report the alert hashes as cleared"
            )
        return
    alert_tables = _tables(wrangler, alerts_db, alerts_id)
    if ALERT_TABLE not in alert_tables:
        raise LeadsError(
            "the named alert database does not hold the alert_dedup table; "
            "refusing to report the alert hashes as cleared"
        )
    if alert_tables & _LEAD_TABLES:
        raise LeadsError(
            "the named alert database holds lead tables, so it is not a separate "
            "alert database"
        )
    if ALERT_TABLE in leads_tables:
        raise LeadsError(
            "the leads database also holds an alert_dedup table, so the deployment "
            "is ambiguous; name the database the Worker writes alerts to and clear "
            "the other by hand"
        )


def pause(seconds: float) -> None:
    time.sleep(seconds)


def finish_erasure(
    wrangler: str,
    leads_db: str,
    alerts_db: str,
    ids: tuple[str, str],
    lead_id: str,
    email: str,
) -> tuple[int, str]:
    """The guarded step after the owner applied the lead SQL. ``ids`` is the
    (leads, alerts) database id pair. Returns (exit code, fixed message).

    Precondition, not checked here (the kill switch is a Worker setting this tool
    cannot read): the kit gate is closed for the whole erasure.

    Exit codes once this runs (never 2): before the lead tables are read back as
    clean, any failure is 4 (the alert table was not touched); after that, any
    failure, a changed database or a lead for the address that exists again is 3.

    Order: identity and schema checks and the lead read-back (for the id AND the
    address; must be 0 and 0), the same checks again right before the clear, the
    clear (one statement that deletes only while the alert database holds no lead
    table), read back, repeat while rows arrive (bounded), settle, read back once
    more, the checks a last time, and the lead tables read for the address one
    last time. Every statement runs between two identity checks of its database
    name (``_bound_rows``).

    The window between a check and a statement is not closed: a name re-pointed
    and restored inside one remote call is not seen, and a lead created after the
    last read is not seen (see docs/privacy.md)."""
    leads_id, alerts_id = ids
    lead_sql = _lead_readback_sql(lead_id, email)

    def leads_present() -> bool:
        left, events = _counts(
            _bound_rows(wrangler, leads_db, leads_id, lead_sql),
            ("leads_remaining", "events_remaining"),
        )
        return bool(left or events)

    unconfirmed = (
        "error: the lead rows are NOT confirmed erased (%s) and the alert table "
        "was not touched; apply the SQL, then run this command with --finish again"
    )
    try:
        preflight_alerts(wrangler, leads_db, alerts_db, leads_id, alerts_id)
        present = leads_present()
    except LeadsError as exc:
        return EXIT_LEADS_UNCONFIRMED, unconfirmed % _why(exc)
    if present:
        return EXIT_LEADS_UNCONFIRMED, (
            "error: lead rows remain in the leads database; apply the SQL first. "
            "The alert table was not touched"
        )
    started_ms = int(time.time() * 1000)
    incomplete = (
        "error: the lead rows were read as erased but the alert hashes are NOT "
        "confirmed cleared (%s). The erasure is NOT complete: close the kit gate, "
        "then run this command with --finish again"
    )
    again = (
        "lead rows reappeared for this address (a lead was created again after "
        "the erasure started): apply the delete SQL again with a fresh export"
    )
    new_rows = 0
    # The same checks again, immediately before the clear (the lead read-back
    # took time), and the clear itself decides on the schema in its own statement.
    clear_sql = ALERT_CLEAR_SQL if alerts_db == leads_db else ALERT_CLEAR_SPLIT_SQL
    try:
        preflight_alerts(wrangler, leads_db, alerts_db, leads_id, alerts_id)
    except LeadsError as exc:
        return EXIT_ALERTS_INCOMPLETE, incomplete % (
            f"a database check failed just before the clear ({_why(exc)}); "
            "nothing was cleared"
        )
    try:
        for _ in range(ALERT_ATTEMPTS):
            _bound_rows(wrangler, alerts_db, alerts_id, clear_sql)
            left, new_rows = _counts(
                _bound_rows(
                    wrangler, alerts_db, alerts_id, alert_readback_sql(started_ms)
                ),
                ("alert_dedup_remaining", "alert_dedup_new"),
            )
            if left == 0:
                break
        else:
            why = (
                "rows were created after the erasure started"
                if new_rows
                else "rows remain after the clear"
            )
            return EXIT_ALERTS_INCOMPLETE, incomplete % why
        pause(ALERT_SETTLE_SECONDS)
        left, new_rows = _counts(
            _bound_rows(wrangler, alerts_db, alerts_id, alert_readback_sql(started_ms)),
            ("alert_dedup_remaining", "alert_dedup_new"),
        )
        if left:
            why = (
                "rows were created after the erasure started"
                if new_rows
                else "rows reappeared"
            )
            return EXIT_ALERTS_INCOMPLETE, incomplete % why
        # Last: the databases are still the ones that were checked, and the lead
        # tables, read after the clear and after the pause, still hold nothing for
        # this address.
        preflight_alerts(wrangler, leads_db, alerts_db, leads_id, alerts_id)
        if leads_present():
            return EXIT_ALERTS_INCOMPLETE, incomplete % again
    except LeadsError as exc:
        return EXIT_ALERTS_INCOMPLETE, incomplete % _why(exc)
    return EXIT_OK, (
        "done as far as this tool can verify: the lead rows for the id and the "
        "address and the alert hashes read 0 twice, a moment apart. A person who "
        "can still open the kit or trigger an alert can create a new row or hash "
        "later; the kit gate (kill switch) must stay closed while you erase, and "
        "this tool cannot read it. Which database each call ran on cannot be "
        "proven: the checks bind the name to the id you gave only at the moments "
        "they ran"
    )


def _why(exc: LeadsError) -> str:
    """The fixed reason text of a failed step (never remote text)."""
    if isinstance(exc, IdentityError):
        return "a database name does not resolve to the id you gave"
    return str(exc)


def _lead_readback_sql(lead_id: str, email: str) -> str:
    """The `--finish` read-back for the id and the address, from the validated
    values only (the same checks as ``_sql``)."""
    _sql(lead_id, email)  # validates both again at the point of use
    return _FINISH_READBACK.replace("<ID>", lead_id).replace("<EMAIL>", email)


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


def _read_back(lead_id: str) -> str:
    """The one-statement read-back for ``--command`` (the id is already validated)."""
    if not _ID.fullmatch(lead_id):
        raise LeadsError("internal: refusing to build SQL from a non-id value")
    return _READ_BACK.replace("<ID>", lead_id)


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
        emails, fields, events = load_lead_views(args.input, args.events)
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
7. Short-lived alert hash (automated by `leads delete`, #905). The
   `alert_dedup` table holds a keyed hash of each recent alert, which can
   include the address. A row stops counting as a repeat after 10 minutes, but
   it is deleted only when the next alert event is admitted (no timer), so on
   a quiet site up to 500 rows stay stored until the next alert or a clear.
   `leads delete --yes --alerts-database NAME --alerts-database-id UUID
   --leads-database-id UUID` (the ids come from the Worker's own configuration)
   checks that each name resolves to its id and then the schemas (the named
   database must hold `alert_dedup`; it may hold the lead tables only if it is
   the leads database), prints the lead SQL and exits 10: checks passed,
   NOTHING erased. After you apply the SQL, `--finish` reads the lead tables
   back (for the id and for the address), clears the whole alert table, reads it
   back as 0 twice and reads the lead tables once more. Exit 0 from `--finish`
   is the only complete result: exit 3 means the lead rows were read as erased
   but the alert hashes are not confirmed cleared, a database changed, or a
   lead for the address exists again; exit 4 means the lead rows are not
   confirmed erased. Close the kit gate (kill switch) BEFORE you start and keep
   it closed until you finish: this tool cannot read the gate, and a person who
   can still open the kit or trigger an alert can create a new lead or hash
   after the last read, which no tool can prevent. Each remote statement runs
   between two checks that the name still resolves to the id you gave; that
   binds the name to the Worker's configuration at those moments and cannot
   prove which database one call used. Clearing the table only resets
   repeat-alert suppression for up to 10 minutes.
8. Worker storage, as far as a text scan can show. The scripts/erasure-stores.json
   registry and its test list every file that names a KV, cache, R2, Durable
   Object or queue primitive or binding, and pin each statement that does. That
   is a lexical inventory of where storage is named, not a proof of where data
   flows: a write that never names a primitive or binding is not seen. Verify
   by hand that the KV namespace holds only the gated page and live status
   state and that nothing else was wired to store requests. An unlock cookie is
   a signed token with no server copy; it stops opening the kit once the
   `leads` row is gone.
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


# The legacy copy (#772): installations that collected leads before the leads database
# existed still hold the lead tables in the analytics database until the one-time
# move purged them. Erasing a person means erasing that copy too. The legacy run is
# its own explicit mode: the owner names the legacy database AND types it a second
# time, so a typo or a wrong paste never points the tool at a database by accident.
# It deletes from exactly these two tables (the table list is explicit, never read
# from the database) and nothing else.
LEGACY_TABLES = ("leads", "kit_events")


def _legacy_database(name: str, confirm: str | None) -> str:
    """The legacy analytics database, only if named twice, valid, and not the leads
    database."""
    database = _checked_database(name)
    if database == LEADS_DATABASE:
        raise LeadsError(
            f"--legacy-database must not be {LEADS_DATABASE}: the leads database "
            "is erased by the normal run"
        )
    if confirm != database:
        raise LeadsError(
            "--confirm-legacy NAME is required and must repeat --legacy-database "
            "exactly: you are about to erase from the legacy analytics database. "
            "Nothing was printed or changed"
        )
    return database


def _run_delete_legacy(args: argparse.Namespace) -> int:
    """`leads delete` for the legacy copy of a lead in the analytics database.

    Same guards as the normal run: the address on stdin only, an id that the export
    itself assigned to that address, SQL that pairs the id with the address (in the
    file, never on a command line), a separate id-only read-back command, and exit
    10 for "checks passed, nothing erased" (there is no --finish here: the alert
    hashes are cleared by the normal run's --finish, and the read-back below is
    the proof for this copy)."""
    if args.legacy_database is None:
        print("error: --confirm-legacy needs --legacy-database NAME", file=sys.stderr)
        return EXIT_USAGE
    if (
        args.database is not None
        or args.alerts_database is not None
        or args.alerts_database_id is not None
        or args.leads_database_id is not None
        or args.finish
    ):
        print(
            "error: --legacy-database is its own run: it takes no --database, "
            "--alerts-database, database id or --finish (run the normal erasure "
            "for the leads database separately)",
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
    if args.wrangler is not None and not args.yes:
        print("error: --wrangler needs --yes", file=sys.stderr)
        return EXIT_USAGE
    try:
        database = _legacy_database(args.legacy_database, args.confirm_legacy)
        email = _address(args.email)
        leads = load_leads(args.input)
    except LeadsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(
        f"legacy database: {database} {DATABASE_NOTE}; erasing from its "
        f"{' and '.join(LEGACY_TABLES)} tables only",
        file=sys.stderr,
    )
    ids = [lead_id for lead_id, stored in leads if stored == email]
    if not ids:
        print(
            "no lead matches the supplied address; nothing to delete", file=sys.stderr
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
            f"dry run: 1 lead matches {mask_email(email)} in the legacy copy. It "
            "would be deleted with its kit events. Nothing was changed and "
            "nothing was requested, so this exit 0 is not an erasure. Re-run "
            "with --yes to print the SQL."
        )
        return EXIT_OK
    try:
        if (ids[0], email) not in leads:
            raise LeadsError("internal: the matched id is not in the export")
        statements = _sql(ids[0], email)
        wrangler = _resolve_wrangler(args.wrangler)
    except LeadsError as exc:  # pragma: no cover - guarded by load_leads
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    text = "\n".join(statements) + "\n"
    target = args.sql_out if args.sql_out is not None else "delete-lead-legacy.sql"
    if args.sql_out is not None:
        try:
            _write_private(args.sql_out, text)
        except LeadsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE
    quoted = shlex.quote(os.fspath(target))
    shown = os.path.relpath(wrangler, os.path.realpath(REPO_ROOT))
    where = (
        f"The SQL is in {quoted} (new file, mode 0600). "
        if args.sql_out is not None
        else "The SQL is on stdout; prefer --sql-out. "
    )
    print(
        f"checks passed (exit {EXIT_CHECKED}) for the LEGACY copy: NOTHING HAS BEEN "
        f"REMOVED YET. {where}It holds the address, so delete it afterward. It "
        "deletes only a row that still pairs that id with that address. Apply it "
        f"to the legacy database {database} only, then read it back with the second "
        "command (the id only, no address): 0 and 0 means no remaining rows at "
        "this moment, not proof of completed erasure (no transaction in a file; an "
        "event can arrive after the read); run the file again after a wait until two "
        "consecutive reads show 0 and 0. The alert hashes are cleared by the normal "
        "run's --finish, not here:\n"
        f"{shlex.quote(shown)} d1 execute {database} --remote --file {quoted}\n"
        f"{shlex.quote(shown)} d1 execute {database} --remote --json --command "
        f"{shlex.quote(_read_back(ids[0]))}",
        file=sys.stderr,
    )
    if args.sql_out is None:
        sys.stdout.write(text)
    return EXIT_CHECKED


def run_delete(args: argparse.Namespace) -> int:
    if args.checklist:
        if (
            args.input is not None
            or args.email is not None
            or args.yes
            or args.dry_run
            or args.database is not None
            or args.sql_out is not None
            or args.alerts_database is not None
            or args.alerts_database_id is not None
            or args.leads_database_id is not None
            or args.wrangler is not None
            or args.finish
            or args.legacy_database is not None
            or args.confirm_legacy is not None
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
    if args.legacy_database is not None or args.confirm_legacy is not None:
        return _run_delete_legacy(args)
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
    if (
        args.finish
        or args.wrangler is not None
        or args.alerts_database_id is not None
        or args.leads_database_id is not None
    ) and not args.yes:
        print(
            "error: --finish, --wrangler and the database ids need --yes",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.finish and args.sql_out is not None:
        print("error: --finish writes no SQL file; drop --sql-out", file=sys.stderr)
        return EXIT_USAGE
    try:
        database = _database(args.database)
        if args.yes and args.alerts_database is None:
            # Fail closed before any SQL is printed: without the alert clear the
            # erasure is not complete (docs/privacy.md).
            raise LeadsError(
                "--alerts-database NAME is required with --yes: the erasure is "
                "not complete without clearing the alert hashes. Name the database "
                "the Worker writes alerts to (the leads database if one database "
                "holds both). Nothing was printed or changed"
            )
        alerts_database = (
            None
            if args.alerts_database is None
            else _checked_database(args.alerts_database)
        )
        db_ids: tuple[str, str] | None = None
        if args.yes:
            # The ids come from the Worker's own configuration (the d1_databases
            # entries of the instance's wrangler config, binding by binding); the
            # public repository's config holds placeholders. See docs/privacy.md.
            db_ids = (
                _checked_uuid(args.leads_database_id, "--leads-database-id"),
                _checked_uuid(args.alerts_database_id, "--alerts-database-id"),
            )
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
            "kit events. Nothing was changed and nothing was requested, so this "
            "exit 0 is not an erasure. Re-run with --yes to print the SQL."
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
    if alerts_database is None or db_ids is None:  # pragma: no cover - refused above
        return EXIT_USAGE
    # Online and read-only until --finish: resolve the pinned wrangler and prove
    # the identity and the schemas of the databases, BEFORE any SQL is printed.
    try:
        wrangler = _resolve_wrangler(args.wrangler)
        if args.finish:
            code, message = finish_erasure(
                wrangler, database, alerts_database, db_ids, ids[0], email
            )
            print(message, file=sys.stderr)
            return code
        try:
            preflight_alerts(wrangler, database, alerts_database, db_ids[0], db_ids[1])
        except LeadsError as exc:
            # Read-only step: this invocation made no remote change.
            raise LeadsError(f"{exc}; nothing was changed") from None
    except LeadsError as exc:
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
    # Only the path inside the repository is shown (never the absolute path, which
    # holds the local account's directories); the command runs from the root.
    shown = os.path.relpath(wrangler, os.path.realpath(REPO_ROOT))
    where = (
        f"The SQL is in {quoted} (new file, mode 0600). "
        if args.sql_out is not None
        else "The SQL is on stdout; prefer --sql-out, which creates a private "
        "file and refuses a path that exists or ends in a symlink. "
    )
    # Printed before the apply command, which stays the last thing on stderr (the
    # tests and operators take the command from there).
    print(
        f"checks passed (exit {EXIT_CHECKED}): both names resolve to the ids you "
        f"gave and {alerts_database} holds the alert_dedup table (checklist item "
        "7). NOTHING HAS BEEN REMOVED YET. After the apply command below, run "
        "this same command again with --finish (the address on stdin again): it "
        "reads the lead tables back, clears the alert hashes and reads them back as 0. "
        "Only that run exits 0. Close the kit gate (kill switch) while you erase.",
        file=sys.stderr,
    )
    print(
        f"1 lead matches. {where}It holds the address, so delete it afterward. "
        "The apply command names wrangler relative to the repository root: run "
        "it from that directory (adjust the SQL file path if you are elsewhere). "
        "It deletes only a row in the live database that still pairs that id "
        f"with that address. Apply it to the leads database {database} only. "
        "The file shows no result rows (D1 returns only a summary for a file). "
        "After each run, read the result with the second command, which holds the "
        "id and not the address. Reading 0 and 0 means no remaining rows at this "
        "moment; it is not proof of completed erasure, because D1 offers no "
        "transaction in a file and an event can arrive after the read. Run the "
        "file again after a wait until two consecutive reads show 0 and 0 "
        "(leads_remaining 1 means nothing was deleted):\n"
        f"{shlex.quote(shown)} d1 execute {database} --remote --file {quoted}\n"
        f"{shlex.quote(shown)} d1 execute {database} --remote --json --command "
        f"{shlex.quote(_read_back(ids[0]))}",
        file=sys.stderr,
    )
    if args.sql_out is None:
        sys.stdout.write(text)
    return EXIT_CHECKED


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
        help="erase one lead, its events and the alert hashes",
        description="Read a local export of the leads table (made with wrangler "
        "d1 execute --json, see docs/privacy.md), find the lead for one email "
        "address and print the SQL that deletes it. Dry run by default and "
        "offline. --yes needs --alerts-database NAME, --alerts-database-id and "
        "--leads-database-id: it checks the database ids and schemas read-only "
        "through the repository's wrangler, prints the SQL for you to apply and "
        "exits 10 (checks passed, NOTHING erased), then --finish (after you "
        "applied it) reads the lead tables back, clears the alert hash table and "
        "reads it back as 0. No file written unless "
        "--sql-out; the SQL pairs the lead id with its address, so it deletes "
        "only a row that still matches both. --database and --email - are "
        "required. Close the kit gate (kill switch) before you start: this tool "
        "cannot read it. Every remote statement runs between two checks that the "
        "database name still resolves to the id you gave; that cannot prove which "
        "database one call used. Exit 0 the erasure verified by --finish (a dry "
        "run, which requests nothing, also exits 0), 10 checks passed and SQL "
        "printed, NOT erased, 1 no match, 2 usage problem or a refused export, "
        "database, database id or wrangler (step 1, or before --finish makes a "
        "remote call; --finish never exits 2 once it runs), 3 lead rows erased "
        "but the alert hashes not confirmed cleared, a database changed, or a "
        "lead for the address exists again (NOT complete), 4 lead rows not "
        "confirmed erased.",
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
        "--legacy-database",
        default=None,
        metavar="NAME",
        help="erase the legacy copy of a lead from this analytics database (its "
        "leads and kit_events tables only); its own run, with --confirm-legacy and "
        "--yes, never together with --database or --finish",
    )
    delete.add_argument(
        "--confirm-legacy",
        default=None,
        metavar="NAME",
        help="repeat the --legacy-database name exactly, to confirm it",
    )
    delete.add_argument(
        "--alerts-database",
        default=None,
        metavar="NAME",
        help="required with --yes: the database the Worker writes alerts to "
        "(letters, digits, _ and -); the same name as --database if one "
        "database holds both. Its schema is checked before anything is printed",
    )
    delete.add_argument(
        "--alerts-database-id",
        default=None,
        metavar="UUID",
        help="required with --yes: the id (UUID) of the alerts database, copied "
        "from the d1_databases entry of the Worker's own configuration for the "
        "binding the Worker reads alerts from; the name must resolve to it",
    )
    delete.add_argument(
        "--leads-database-id",
        default=None,
        metavar="UUID",
        help="required with --yes: the id (UUID) of the reach-around-leads "
        "database, copied from the Worker's own configuration; the name must "
        "resolve to it",
    )
    delete.add_argument(
        "--wrangler",
        type=Path,
        default=None,
        metavar="PATH",
        help="absolute path inside worker/node_modules/.bin or "
        "worker/node_modules/wrangler of this repository (default: "
        "worker/node_modules/.bin/wrangler); never searched on PATH; its package "
        "must match worker/package-lock.json, and the package and its "
        "dependencies (walked, bounded) must belong to root or you with no other "
        "write bit and no write bit for a group anyone else belongs to (only your "
        "own private primary group on a machine whose passwd and group lines "
        "name exactly files is "
        "fine; otherwise run 'chmod -R g-w worker/node_modules'). Only the "
        "layout of 'cd worker && npm ci --ignore-scripts' is supported (hoisted "
        "or workspace installs are refused). It receives your Cloudflare token: "
        "this is a trust boundary, not an integrity check",
    )
    delete.add_argument(
        "--finish",
        action="store_true",
        help="with --yes, after you applied the SQL: confirm the lead rows are "
        "gone, clear the alert hashes, read them back as 0 twice",
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
        description="Read two local exports, the leads table and the kit events "
        "(see docs/privacy.md) and print what an access request needs for one "
        "address: the masked address, country, created and verified times and "
        "the kit event kinds with their hours. Nothing else, no SQL, no network "
        "call. The address comes from a file or stdin, never from the command "
        "line. Exit 0 done, 1 no match, 2 usage problem or a refused export.",
    )
    show.add_argument(
        "--input",
        type=Path,
        required=True,
        metavar="PATH",
        help="the leads export, JSON (one result block)",
    )
    show.add_argument(
        "--events",
        type=Path,
        required=True,
        metavar="PATH",
        help="the kit events export, JSON (one result block, may be empty)",
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
