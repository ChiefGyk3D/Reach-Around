#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Owner-run: export the analytics tables of the D1 database to a private folder.
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
# Besides the table listing, the run makes one more read-only call, `SELECT name
# FROM d1_migrations`, to learn which migrations the database has applied: a table
# whose migration is applied must exist, or the run fails.
#
# Requires bash 4.0 or newer (the script refuses with exit 2 on an older one).
#
# Exit: 0 done or dry run, 1 a step failed, 2 usage (or bash too old), 3 refused
# (unsafe folder).

set -euo pipefail

# Needs bash 4.0 or newer. macOS ships 3.2: install a newer bash first (for
# example `brew install bash`).
if [ -z "${BASH_VERSINFO:-}" ] || [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
  echo "error: bash 4.0 or newer is required (this is ${BASH_VERSION:-unknown})" >&2
  exit 2
fi

# Tables worth keeping. Only the four tables named in the header are left out on
# purpose; every other table of the schema is in this list (a test enforces it).
tables=(
  alert_dedup alert_quota analytics_coverage
  click_budget click_budget_lh click_capacity click_country_daily click_country_monthly
  click_events click_funnel_state click_hop_daily click_hop_monthly
  click_landing_daily click_landing_monthly click_landing_state click_month_log click_seal
  click_src_daily click_src_monthly
  counters d1_migrations kit_meta
  view_city_daily view_daily view_dim_daily view_geo_daily view_hour_daily view_meta
  view_rollup_log
)

# Inventory: the migration that introduced each table above (table:migration
# file name; wrangler's own d1_migrations has no migration). The run reads the
# migrations the database has APPLIED and decides per table: introduced by an
# applied migration means REQUIRED (missing fails the run, so an incomplete
# backup is never reported as a success); introduced by a migration that is not
# applied means the table cannot exist yet and is skipped with a notice.
# tests/test_backup_d1.py derives this map from the migrations and fails when it
# drifts. A table with no entry here is treated as required.
introduced_by=(
  alert_dedup:0013_alerts.sql alert_quota:0013_alerts.sql
  analytics_coverage:0014_health.sql
  click_budget:0011_click_funnel.sql click_budget_lh:0017_landing_hop.sql
  click_capacity:0011_click_funnel.sql click_country_daily:0011_click_funnel.sql
  click_country_monthly:0011_click_funnel.sql click_events:0001_click_events.sql
  click_funnel_state:0011_click_funnel.sql click_hop_daily:0017_landing_hop.sql
  click_hop_monthly:0017_landing_hop.sql click_landing_daily:0017_landing_hop.sql
  click_landing_monthly:0017_landing_hop.sql click_landing_state:0017_landing_hop.sql
  click_month_log:0011_click_funnel.sql click_seal:0011_click_funnel.sql
  click_src_daily:0011_click_funnel.sql click_src_monthly:0011_click_funnel.sql
  counters:0009_kit.sql d1_migrations:wrangler kit_meta:0009_kit.sql
  view_city_daily:0019_view_city_daily.sql view_daily:0006_page_view_rollups.sql
  view_dim_daily:0006_page_view_rollups.sql view_geo_daily:0006_page_view_rollups.sql
  view_hour_daily:0015_hour_patterns.sql view_meta:0005_page_views.sql
  view_rollup_log:0006_page_view_rollups.sql
)

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
  echo "would create: $out (mode 700)"
  echo "would export ${#tables[@]} tables (the run fails if a table whose migration is applied is missing), one file each, mode 600:"
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

# One name per line from a wrangler --json answer on stdin. The names are compared
# as whole lines below, so a name that holds a line break could make a missing
# table or migration look present: refuse it outright.
names_of() {
  python3 -I -c '
import json, sys
what = sys.argv[1]
data = json.load(sys.stdin)
rows = data[0]["results"] if isinstance(data, list) else data["results"]
for r in rows:
    name = r["name"]
    if not isinstance(name, str) or any(c in name for c in "\r\n\0"):
        sys.stderr.write("error: invalid " + what + " name in the listing\n")
        sys.exit(1)
    print(name)
' "$1"
}

# Which of our tables exist in this database.
listing=$(timeout "$limit" "${wrangler[@]}" d1 execute "$db" --remote --json \
  --command "SELECT name FROM sqlite_master WHERE type = 'table'" 2>/dev/null) || {
  echo "error: could not list the tables (wrong name, not signed in, or timed out)" >&2
  exit 1
}
existing=$(printf '%s' "$listing" | names_of table) || {
  echo "error: unexpected answer from the database (table listing)" >&2
  exit 1
}

# Which migrations the database has applied (wrangler records each applied file
# name in d1_migrations). Unreadable means we cannot tell which tables must
# exist, so the run fails instead of guessing.
applied_json=$(timeout "$limit" "${wrangler[@]}" d1 execute "$db" --remote --json \
  --command "SELECT name FROM d1_migrations" 2>/dev/null) || {
  echo "error: could not read the applied migrations (d1_migrations); not guessing which tables must exist" >&2
  exit 1
}
applied=$(printf '%s' "$applied_json" | names_of migration) || {
  echo "error: unexpected answer from the database (applied migrations)" >&2
  exit 1
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

is_applied() {
  case "$nl$applied$nl" in
  *"$nl$1$nl"*) return 0 ;;
  *) return 1 ;;
  esac
}

# The migration that introduced a table; empty when the inventory has no entry.
migration_of() {
  local e
  for e in "${introduced_by[@]}"; do
    if [ "${e%%:*}" = "$1" ]; then
      printf '%s' "${e#*:}"
      return 0
    fi
  done
  return 0
}

# A table is REQUIRED unless its migration is known and NOT applied (wrangler's
# own d1_migrations has no migration, and a table with no entry is required).
is_required() {
  local m
  m="$(migration_of "$1")"
  case "$m" in
  "" | wrangler) return 0 ;;
  esac
  is_applied "$m"
}

# A missing REQUIRED table is an incomplete backup: fail before writing anything.
missing=()
for t in "${tables[@]}"; do
  if ! is_listed "$t" && is_required "$t"; then
    missing+=("$t")
  fi
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "error: required table(s) missing from this database: ${missing[*]}" >&2
  exit 1
fi

count=0
exported=()
for t in "${tables[@]}"; do
  if ! is_listed "$t"; then
    echo "skipped (not in this database): $t (migration $(migration_of "$t") is not applied)"
    continue
  fi
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

[ "$count" -gt 0 ] || {
  echo "error: none of the expected tables exist in this database" >&2
  exit 1
}

# Check everything written, in one pass, against the repo's allowlist cut down to
# the tables actually exported (so an older database that lacks one still works). Reports line numbers and kinds, never values.
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
echo "done: $count tables written to $out"
echo "note: the tables are exported one call at a time, so this is not a single point-in-time snapshot."
echo "      For one consistent copy, pause both write routes first (see docs/backup-restore.md)."
echo "next: copy it to your offline copy, then run: (cd that folder && sha256sum -c SHA256SUMS)"
