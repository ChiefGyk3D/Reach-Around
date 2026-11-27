#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Owner-run: export the analytics tables of the ANALYTICS D1 database (binding DB) to a private folder.
#
# WHICH DATABASE. Two D1 databases exist (issue #772): the analytics database this script exports, and
# the leads database (binding LEADS_DB, holding leads and kit events) that it never exports. DATABASE
# must be the analytics one; every run prints the name it acts on. IDENTITY is checked on the live
# database, not by name: with --apply the script reads the db_identity row and refuses (exit 3) unless
# it is exactly 'analytics', whatever the database is called or how it was reached (a name, an alias).
# The name is never a safeguard: a valid analytics database may be called anything.
# The leads database has its own backup procedure (docs/backup-restore.md), never this allowlist.
#
# Usage: scripts/backup-d1.sh DATABASE DEST_DIR            # dry run: prints the plan
#        scripts/backup-d1.sh DATABASE DEST_DIR --apply    # writes the export
#
# DATABASE is the D1 database name. DEST_DIR is an existing directory that only
# you can use (mode 700) and that is NOT inside a git work tree: an export is
# private data and must never reach a repository. The script refuses (exit 3)
# otherwise. Run it from the directory whose wrangler config names the database.
#
# What it writes: a new directory DEST_DIR/d1-<UTC timestamp>/ (mode 700) with
# one <table>.sql per table (mode 600) and a SHA256SUMS file. Lead tables
# (leads, kit_events), the daily visitor hash (visitor_seen) and the raw page
# view rows (view_events) are never exported. All files are then checked with
# scripts/check-dump.py (no address, no user agent, only allowlisted columns);
# if the check fails the whole run is deleted and the script exits 1.
#
# The tables are exported one wrangler call at a time: the result is NOT a single
# point-in-time snapshot. Pause both write routes for the run when one consistent
# copy matters (docs/backup-restore.md).
#
# Prints table names and file counts only: never an id, a row or wrangler's own
# output. Every wrangler call is bounded by BACKUP_TIMEOUT seconds (default 120,
# 1 to 900). Uses `wrangler d1 export --remote` (set WRANGLER to another command,
# for example a test stub). The dry run makes no network call and writes nothing.
#
# The run makes read-only queries (the table listing with each table's definition,
# once before and once after the exports, and a row count of every table, once
# before and once after) plus the exports. An answer that is not a single
# successful result is never trusted. A table may GAIN rows during the run (page
# views, clicks and alerts keep arriving) but must not lose any: the count after
# the exports must be at least the count before (else exit 1, rows decreased:
# rerun), and each export file's row count, counted by reading it line by line
# (single-row INSERT lines only, size limits, nothing executed), must lie between
# the two, so an export cut short fails. If the listing after the exports differs
# from the first (a table dropped, replaced or changed) the run fails. Residual
# (inferred, not verified: D1 gives no snapshot across separate queries): a delete
# plus an insert that leaves the counts level is not seen. LIMIT: when a table
# grows during the run, the row COUNT cannot prove that every row that existed
# before is in the export (an omitted old row can be hidden by a new one); the
# count range proves cardinality only. D1's documentation says a running export
# blocks other requests to the database, which suggests one consistent snapshot,
# but that is INFERRED, not documented. BACKUP_MAX_BYTES is one budget for all
# export files of the run. Every
# table in the `tables` list below is ALWAYS required: if the database lacks any
# of them the run fails (exit 1) before any export and names the missing tables
# (at most 10, then "... and K more"). A database that lacks a current table (an
# older schema that is not migrated yet) therefore fails with a message to apply
# the migrations first. That is deliberate: a record kept inside the database
# (such as its migration history) can be damaged or edited, so nothing the
# database says may excuse a missing table, and a backup must never silently omit
# data. A table in the database that this script does not know (not in the lists
# below) also fails the run before any export, so a new or hand-made table is
# never silently left out either. Names read from the database are sanitized
# before they are printed, and every list printed from the database is bounded.
#
# Requires bash 4.0 or newer (the script refuses with exit 2 on an older one).
# Residual: the guard below reads variables the shell itself sets, but whoever
# controls this script's environment (PATH, WRANGLER, the files) already controls
# the whole run, so the guard is a usability check against a wrong shell, not a
# defense against such a caller.
#
# Exit: 0 done or dry run, 1 a step failed, 2 usage (or bash too old), 3 refused
# (unsafe folder).

# --- shell guard begin (POSIX syntax only: it must run on any shell, first) ---
# Needs bash 4.0 or newer. macOS ships 3.2: install a newer bash first (for
# example `brew install bash`). Nothing above this block may use bash-only syntax.
# BASH_VERSION can be set by the environment, so it is not trusted: the running
# shell is asked for its read-only BASH_VERSINFO array inside eval in a command
# substitution. A shell that is not bash fails there (the error stays inside the
# subshell) and exits 2 here, before any bash-only syntax is parsed.
bash_major="$(eval 'printf %s "${BASH_VERSINFO[0]}"' 2>/dev/null)" || bash_major=""
case "$bash_major" in
"" | *[!0-9]*)
  echo "error: bash 4.0 or newer is required (this is not bash)" >&2
  exit 2
  ;;
0 | 1 | 2 | 3)
  echo "error: bash 4.0 or newer is required (this is bash $bash_major)" >&2
  exit 2
  ;;
esac
# --- shell guard end ---

set -euo pipefail

# Tables worth keeping. Only the four tables named in the header are left out on
# purpose; every other table of the schema is in this list (a test enforces it).
tables=(
  alert_dedup alert_quota analytics_coverage
  click_budget click_budget_lh click_capacity click_country_daily click_country_monthly
  click_events click_funnel_state click_hop_daily click_hop_monthly
  click_landing_daily click_landing_monthly click_landing_state click_month_log click_seal
  click_src_daily click_src_monthly
  counters d1_migrations db_identity kit_meta
  view_city_daily view_daily view_dim_daily view_geo_daily view_hour_daily view_meta
  view_rollup_log
)

# Every table above is ALWAYS required (see the header). tests/test_backup_d1.py
# builds the schema from the migrations and fails when a table exists there that
# is in neither this list nor never_exported, so a new migration table cannot be
# forgotten.

# Tables that exist in the database and are left out on purpose (see the header).
never_exported=(leads kit_events visitor_seen view_events)

# The only other tables the run accepts without exporting them: SQLite's own
# (every name that starts with sqlite_) and Cloudflare's reserved `_cf_KV`. Any
# other table that is not above fails the run before an export starts.
# _cf_KV is Cloudflare's own internal table, not part of this schema (a test
# proves no migration creates it). Ignoring it means anything stored in it would
# not be backed up: the match is the exact name only, so a look-alike fails.
ignored_tables=(_cf_KV)

usage() {
  echo "usage: $0 DATABASE DEST_DIR [--apply]" >&2
  exit 2
}

[ "$#" -ge 2 ] && [ "$#" -le 3 ] || usage
db="$1"
dest="$2"
apply=0
if [ "$#" -eq 3 ]; then
  [ "$3" = "--apply" ] || usage
  apply=1
fi
case "$db" in
"" | -* | *[!A-Za-z0-9._-]*)
  echo "error: bad database name" >&2
  exit 2
  ;;
esac

limit="${BACKUP_TIMEOUT:-120}"
case "$limit" in
"" | *[!0-9]* | 0*)
  echo "error: BACKUP_TIMEOUT must be a whole number from 1 to 900" >&2
  exit 2
  ;;
esac
if [ "$limit" -gt 900 ]; then
  echo "error: BACKUP_TIMEOUT must be a whole number from 1 to 900" >&2
  exit 2
fi

max_bytes="${BACKUP_MAX_BYTES:-1073741824}"
case "$max_bytes" in
"" | *[!0-9]* | 0*)
  echo "error: BACKUP_MAX_BYTES must be a whole number from 1 to 1099511627776" >&2
  exit 2
  ;;
esac
if [ "${#max_bytes}" -gt 13 ] || [ "$max_bytes" -gt 1099511627776 ]; then
  echo "error: BACKUP_MAX_BYTES must be a whole number from 1 to 1099511627776" >&2
  exit 2
fi

refuse() {
  echo "refused: $1" >&2
  exit 3
}

# The folder: real directory, ours, private, and outside every git work tree.
[ -d "$dest" ] || refuse "DEST_DIR is not a directory."
[ ! -L "$dest" ] || refuse "DEST_DIR is a symbolic link."
dest="$(cd "$dest" && pwd -P)"
[ -O "$dest" ] || refuse "DEST_DIR is not owned by you."
# A "+" after the mode bits means an ACL: it can grant access the mode hides.
# Files made inside a folder with a default ACL would inherit it.
# shellcheck disable=SC2012 # one resolved directory, only the mode column is read
acl_flag="$(ls -ld -- "$dest" | cut -c11)"
[ "$acl_flag" != "+" ] || refuse "DEST_DIR has an ACL (access control list). Remove it: setfacl -b -k <folder>"
mode="$(stat -c '%a' "$dest")"
case "$mode" in
?00) ;;
*) refuse "DEST_DIR must not be open to group or others (run: chmod 700 on it)." ;;
esac
command -v git >/dev/null 2>&1 || refuse "git is needed to prove DEST_DIR is outside a repository."
in_tree="$(git -C "$dest" rev-parse --is-inside-work-tree 2>/dev/null || true)"
in_gitdir="$(git -C "$dest" rev-parse --is-inside-git-dir 2>/dev/null || true)"
if [ "$in_tree" = "true" ] || [ "$in_gitdir" = "true" ]; then
  refuse "DEST_DIR is inside a git repository. An export must never be committed."
fi

read -r -a wrangler <<<"${WRANGLER:-npx wrangler}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
checker="$here/check-dump.py"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$dest/d1-$stamp"

if [ "$apply" -eq 0 ]; then
  echo "dry run: nothing is written and no network call is made."
  echo "database: $db (the analytics database; the leads database is never exported by this script)"
  echo "would create: $out (mode 700)"
  echo "would export ${#tables[@]} tables (the run fails if the database lacks any of them, apply the migrations first, or if it holds a table this script does not know), one file each, mode 600:"
  printf '  %s\n' "${tables[@]}"
  echo "never exported: leads, kit_events, visitor_seen, view_events"
  echo "note: the tables are exported one call at a time, so this is not a single point-in-time snapshot."
  echo "      For one consistent copy, pause both write routes first (see docs/backup-restore.md)."
  echo "run again with --apply to write it."
  exit 0
fi

[ -f "$checker" ] || {
  echo "error: scripts/check-dump.py not found next to this script" >&2
  exit 1
}

# WHICH DATABASE, BY WHAT IT SAYS IT IS. A name proves nothing (a database can be renamed, or reached
# through an id or an alias), so ask the database itself: each one holds a db_identity row written by its
# own migrations, 'analytics' or 'leads'. Only a database that answers exactly 'analytics' is exported;
# one that answers 'leads', answers nothing, or cannot be read is refused before anything is written.
identity=$(timeout "$limit" "${wrangler[@]}" d1 execute "$db" --remote --json \
  --command "SELECT role FROM db_identity ORDER BY role" 2>/dev/null) || {
  echo "error: could not read the database identity (wrong name, not signed in, timed out, or the analytics migrations are not applied); nothing was exported" >&2
  exit 1
}
roles=$(printf '%s' "$identity" | python3 -I -c '
import json, sys
try:
    data = json.load(sys.stdin)
    rows = data[0]["results"] if isinstance(data, list) else data["results"]
    # A fixed class only: what the database returned is never printed.
    found = [r.get("role") for r in rows]
    if not found:
        print("none")
    elif len(found) > 1:
        print("multiple")
    elif found[0] == "analytics":
        print("analytics")
    elif found[0] == "leads":
        print("leads")
    else:
        print("unexpected")
except Exception:
    print("unreadable")
') || roles="unreadable"
if [ "$roles" != "analytics" ]; then
  refuse "the database did not identify itself as the analytics database (class: ${roles:-unreadable}). Apply the analytics migrations (worker/migrations) first, and never point this script at the leads database."
fi

umask 077
mkdir "$out"
chmod 700 "$out"
ok=0
cleanup() {
  if [ "$ok" -ne 1 ]; then
    rm -rf -- "$out"
    echo "failed: the partial export was removed." >&2
  fi
}
trap cleanup EXIT

# Read a wrangler --json answer on stdin; MODE is names, fingerprint or counts.
# Only a single result object with success true and a list of row objects is
# accepted: anything else (an error object, several results, a failed result) is
# an error, never an authoritative answer. Names are printed one per line and are
# compared as whole lines, so a name that holds a line break, a carriage return
# or NUL is refused outright. fingerprint prints one hash of every (name, sql)
# pair; counts prints "name n" lines (n a whole number).
parse_answer() {
  python3 -I -c '
import hashlib, json, sys

def bad(msg):
    sys.stderr.write("error: " + msg + "\n")
    sys.exit(1)

mode = sys.argv[1]
expected = set(sys.argv[2:])  # counts: exactly these tables, once each
seen = set()
try:
    data = json.load(sys.stdin)
except ValueError:
    bad("the answer is not JSON")
if isinstance(data, list):
    if len(data) != 1:
        bad("unexpected number of results in the answer")
    data = data[0]
if not isinstance(data, dict) or data.get("success") is not True:
    bad("the answer is not a successful result")
rows = data.get("results")
if not isinstance(rows, list):
    bad("the answer has no result rows")
pairs = []
for r in rows:
    if not isinstance(r, dict):
        bad("invalid row in the answer")
    name = r.get("name")
    if not isinstance(name, str) or any(c in name for c in "\r\n\0"):
        bad("invalid table name in the answer")
    if mode == "counts":
        n = r.get("n")
        if type(n) is not int or n < 0:
            bad("invalid row count in the answer")
        if name in seen:
            bad("duplicate table in the row counts")
        if name not in expected:
            bad("unexpected table in the row counts")
        seen.add(name)
        print(name, n)
    elif mode == "fingerprint":
        sql = r.get("sql")
        if sql is not None and not isinstance(sql, str):
            bad("invalid table definition in the answer")
        pairs.append([name, sql])
    else:
        print(name)
if mode == "counts" and seen != expected:
    bad("the row counts do not cover exactly the expected tables")
if mode == "fingerprint":
    pairs.sort(key=lambda p: (p[0], p[1] or ""))
    print(hashlib.sha256(json.dumps(pairs).encode()).hexdigest())
' "$@"
}

list_tables() {
  timeout "$limit" "${wrangler[@]}" d1 execute "$db" --remote --json \
    --command "SELECT name, sql FROM sqlite_master WHERE type = 'table'" 2>/dev/null
}

# Which of our tables exist in this database, and a fingerprint of their
# definitions to compare with a second listing after the exports.
listing=$(list_tables) || {
  echo "error: could not list the tables (wrong name, not signed in, or timed out)" >&2
  exit 1
}
existing=$(printf '%s' "$listing" | parse_answer names) || {
  echo "error: unexpected answer from the database (table listing)" >&2
  exit 1
}
fingerprint_before=$(printf '%s' "$listing" | parse_answer fingerprint) || {
  echo "error: unexpected answer from the database (table listing)" >&2
  exit 1
}

# Names read from the database are untrusted text: before one is printed, every
# byte outside [A-Za-z0-9_.-] becomes '?' (C locale, so a multibyte or control
# byte cannot reach a terminal or a log) and a long name is cut at 80 characters.
safe() {
  local LC_ALL=C s="$1"
  s="${s//[!A-Za-z0-9_.-]/?}"
  if [ "${#s}" -gt 80 ]; then
    s="${s:0:80}..."
  fi
  printf '%s' "$s"
}

# A diagnostic list from the database: at most max_listed names, each through
# safe, then "... and K more", so a hostile database cannot flood the output.
max_listed=10
safe_list_bounded() {
  local out="" n shown=0
  for n in "$@"; do
    if [ "$shown" -ge "$max_listed" ]; then
      out="$out ... and $(($# - shown)) more"
      break
    fi
    out="${out:+$out }$(safe "$n")"
    shown=$((shown + 1))
  done
  printf '%s' "$out"
}

# Plain string matching, not `printf | grep -q`: under pipefail grep -q exits at
# its first match, the writer can then die of SIGPIPE, and the whole pipeline
# reports failure, so a table that exists would be skipped without an error.
nl=$'\n'
is_listed() {
  case "$nl$existing$nl" in
  *"$nl$1$nl"*) return 0 ;;
  *) return 1 ;;
  esac
}

is_in() {
  local needle="$1" e
  shift
  for e in "$@"; do
    [ "$e" = "$needle" ] && return 0
  done
  return 1
}

# A table is accepted when the script knows it: exported, deliberately never
# exported, or an exact-name system table (sqlite_ prefix or a listed name).
is_known_table() {
  case "$1" in
  sqlite_*) return 0 ;;
  esac
  is_in "$1" "${tables[@]}" "${never_exported[@]}" "${ignored_tables[@]}"
}

# A table that exists but is not in the inventory would be left out of the backup
# without a word: fail before anything is exported, naming it (names only).
unexpected=()
while IFS= read -r t; do
  [ -n "$t" ] || continue
  is_known_table "$t" || unexpected+=("$t")
done <<<"$existing"
if [ "${#unexpected[@]}" -gt 0 ]; then
  echo "error: table(s) in this database that this script does not know: $(safe_list_bounded "${unexpected[@]}")" >&2
  echo "       Add each to tables (or never_exported, or ignored_tables for a system table) in scripts/backup-d1.sh; nothing was backed up." >&2
  exit 1
fi

# Every table of the inventory is required. A missing one is an incomplete backup
# (an older schema not migrated yet, or lost data): fail before anything is
# exported, naming the first few (the names are the script's own, but the same
# bounded printer is used).
missing=()
for t in "${tables[@]}"; do
  is_listed "$t" || missing+=("$t")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "error: required table(s) missing from this database: $(safe_list_bounded "${missing[@]}")" >&2
  echo "       If this database is an older schema, apply the migrations first (npx wrangler d1 migrations apply); a backup never omits a table. Nothing was backed up." >&2
  exit 1
fi

# Row count of every inventory table in one query (the names are this script's
# own). Taken before and after the exports: an export must hold a count between.
count_sql=""
for t in "${tables[@]}"; do
  count_sql="${count_sql:+$count_sql UNION ALL }SELECT '$t' AS name, COUNT(*) AS n FROM \"$t\""
done
count_tables() {
  timeout "$limit" "${wrangler[@]}" d1 execute "$db" --remote --json \
    --command "$count_sql" 2>/dev/null | parse_answer counts "${tables[@]}"
}
count_tables >"$out/.counts-before" || {
  echo "error: could not count the rows before the export" >&2
  exit 1
}

count=0
exported=()
for t in "${tables[@]}"; do
  file="$out/$t.sql"
  if ! timeout "$limit" "${wrangler[@]}" d1 export "$db" --remote --table="$t" \
    --output="$file" >/dev/null 2>&1; then
    echo "error: export of $t failed or timed out" >&2
    exit 1
  fi
  chmod 600 "$file"
  [ -s "$file" ] || {
    echo "error: export of $t is empty" >&2
    exit 1
  }
  exported+=("$t")
  count=$((count + 1))
done

count_tables >"$out/.counts-after" || {
  echo "error: could not count the rows after the export" >&2
  exit 1
}
listing_after=$(list_tables) || {
  echo "error: could not list the tables again after the export" >&2
  exit 1
}
fingerprint_after=$(printf '%s' "$listing_after" | parse_answer fingerprint) || {
  echo "error: unexpected answer from the database (second table listing)" >&2
  exit 1
}
if [ "$fingerprint_before" != "$fingerprint_after" ]; then
  echo "error: the tables changed while the export ran (one was added, dropped or replaced); nothing was kept. Pause the write routes and run again." >&2
  exit 1
fi

# No table may have lost rows while the exports ran: its count after must be at
# least its count before (rows decreased: rerun, exit 1). Growth is allowed. Then
# each file's row count must be at least the before count and at most the after
# count. The files are only READ, never executed: each
# is opened once (with O_NOFOLLOW where the platform has it, so a final symbolic
# link is refused there; elsewhere only the regular-file check on the open
# descriptor and the DEST_DIR link check apply), checked on that descriptor, and
# read with a cumulative limit (BACKUP_MAX_BYTES, default 1 GiB, whatever the
# size was at the start) and a 1 MiB line limit. A NUL byte or invalid UTF-8
# fails. A small quote-aware scanner tracks single and double quotes ACROSS lines
# and counts a statement only when it is exactly `INSERT INTO <table> ... VALUES
# (one tuple);`. Every other line that begins like an insert (any case, leading
# space, other quoting, REPLACE), a statement with more than one VALUES tuple, an
# unbalanced quote or a statement cut off at the end of the file fails closed.
# GRAMMAR: VERIFIED on 2026-11-27 against real D1 exports (wrangler 4.162.0,
# synthetic data, three runs): a PRAGMA line first, `CREATE TABLE <bare name>`,
# one INSERT per row on one line, `INSERT INTO "<table>" ("col",...) VALUES(...);`
# (a value with a newline is escaped and stays on one line), no CR, statements
# not grouped by table (a CREATE TABLE can follow INSERT lines), no INSERT line
# for an empty table. Both `VALUES(` and `VALUES (` are accepted. Anything outside
# that grammar fails closed. Names and verdicts only are printed, never a value.
if ! BACKUP_MAX_BYTES="$max_bytes" python3 -I - "$out" "${exported[@]}" <<'PY'
import os, re, stat, sys

out = sys.argv[1]
tables = sys.argv[2:]
max_bytes = int(os.environ["BACKUP_MAX_BYTES"])
# One budget for the whole run: every byte read from every export file counts.
budget = [max_bytes]
max_line = 1 << 20
TOKEN = re.compile(r"""['"();]""")
INSERTISH = re.compile(r"(?is)\s*(insert|replace)\b")
# An INSERT or REPLACE word outside quotes anywhere else (a CTE-prefixed insert,
# INSERT OR ..., a statement that does not start with it) fails closed. Only the
# trigger event words of the allowlisted guard triggers are let through.
DML = re.compile(r"(?i)\b(insert|replace)\b")
TRIGGER_EVENT = re.compile(r"(?i)\b(before|after|of)\s+insert\s+on\b")


def has_dml(text):
    return bool(DML.search(TRIGGER_EVENT.sub(" ", text)))


def read(name):
    counts = {}
    with open(f"{out}/{name}") as fh:
        for line in fh:
            t, n = line.split()
            counts[t] = int(n)
    return counts


def say(msg, names):
    shown = " ".join(names[:10])
    more = f" ... and {len(names) - 10} more" if len(names) > 10 else ""
    sys.stderr.write(f"error: {msg}: {shown}{more}\n")


def scan(path, table):
    """Returns (verdict, count): verdict is ok, big or odd."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return "odd", 0
    accept = re.compile(r'INSERT INTO ("?)' + re.escape(table) + r'\1[ (]')
    # A quoted identifier is one Q in the head (its text is skipped).
    head_ok = re.compile(r"INSERT INTO (?:Q|" + re.escape(table) + r") VALUES")
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode):
            return "odd", 0
        if st.st_size > budget[0]:
            return "big", 0
        n = 0
        sq = dq = False
        stmt = None  # [depth, tuples, head] while inside a counted statement
        while True:
            raw = fh.readline(max_line + 1)
            if not raw:
                break
            budget[0] -= len(raw)
            if budget[0] < 0 or len(raw) > max_line:
                return "big", 0
            if b"\0" in raw:
                return "odd", 0
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return "odd", 0
            if stmt is None and not sq and not dq:
                if INSERTISH.match(text):
                    if not accept.match(text):
                        return "odd", 0
                    stmt = [0, 0, ""]
            pos = 0
            for m in TOKEN.finditer(text):
                ch = m.group()
                seg = text[pos : m.start()]
                pos = m.end()
                if sq:
                    sq = ch != "'"
                    continue
                if dq:
                    dq = ch != '"'
                    continue
                if stmt is None and has_dml(seg):
                    return "odd", 0
                if stmt is not None and stmt[0] == 0:
                    stmt[2] += seg
                if ch in "'\"":
                    sq, dq = ch == "'", ch == '"'
                    if stmt is not None and stmt[0] == 0:
                        stmt[2] += "Q"
                elif stmt is None:
                    continue
                elif ch == "(":
                    if stmt[0] == 0 and re.search(r"(?i)\bVALUES\b", stmt[2]):
                        stmt[1] += 1
                    stmt[0] += 1
                elif ch == ")":
                    stmt[0] -= 1
                    if stmt[0] < 0:
                        return "odd", 0
                elif stmt[0] == 0:  # ;
                    head = " ".join(stmt[2].split())
                    if stmt[1] != 1 or not head_ok.fullmatch(head):
                        return "odd", 0
                    if text[pos:].strip():
                        return "odd", 0
                    n += 1
                    stmt = None
                    break
                # a ';' inside a tuple (depth > 0) belongs to the statement
            else:
                # The real export keeps every INSERT on one physical line (a
                # newline inside a value is escaped): a statement that is still
                # open at the end of its line is outside the grammar.
                if stmt is not None:
                    return "odd", 0
                if not (sq or dq) and has_dml(text[pos:]):
                    return "odd", 0
        if sq or dq or stmt is not None:
            return "odd", 0
    return "ok", n


before, after = read(".counts-before"), read(".counts-after")
shrunk = [
    t
    for t in tables
    if t not in before or t not in after or after[t] < before[t]
]
if shrunk:
    say("rows decreased during the run (rerun it)", shrunk)
    sys.exit(1)

big, odd, wrong = [], [], []
for t in tables:
    verdict, n = scan(f"{out}/{t}.sql", t)
    if verdict == "big":
        big.append(t)
    elif verdict == "odd":
        odd.append(t)
    elif not before[t] <= n <= after[t]:
        wrong.append(t)
if big:
    say("export too large to check", big)
if odd:
    say("export holds an INSERT shape that cannot be counted", odd)
if wrong:
    say("export row count does not match the database", wrong)
if big or odd or wrong:
    sys.exit(1)
PY
then
  echo "error: the export could not be verified; removed" >&2
  exit 1
fi
rm -f -- "$out/.counts-before" "$out/.counts-after"

# Check everything written, in one pass, against the repo's allowlist cut down to
# the tables exported. Reports line numbers and kinds, never values.
python3 -I - "$here/dump-schema.json" "$out/.allowlist.json" "${exported[@]}" <<'PY'
import json, sys

cfg = json.load(open(sys.argv[1]))
keep = set(sys.argv[3:])
cfg["tables"] = {k: v for k, v in cfg["tables"].items() if k in keep}
json.dump(cfg, open(sys.argv[2], "w"))
PY
if ! cat -- "$out"/*.sql | python3 -I "$checker" --allow-empty \
  --schema "$out/.allowlist.json" - >&2; then
  echo "error: the export failed the dump check (an address, a user agent or an unexpected column); removed" >&2
  exit 1
fi
rm -f -- "$out/.allowlist.json"

(cd "$out" && sha256sum -- *.sql >SHA256SUMS)
chmod 600 "$out/SHA256SUMS"
ok=1
echo "done: $count tables of the analytics database $db written to $out"
echo "note: the tables are exported one call at a time, so this is not a single point-in-time snapshot."
echo "      For one consistent copy, pause both write routes first (see docs/backup-restore.md)."
echo "next: copy it to your offline copy, then run: (cd that folder && sha256sum -c SHA256SUMS)"
