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
# Exit: 0 done or dry run, 1 a step failed, 2 usage, 3 refused (unsafe folder).

set -euo pipefail

# Tables worth keeping. Everything else in the database is left out on purpose.
tables=(
  alert_dedup alert_quota analytics_coverage
  click_budget click_budget_lh click_capacity click_country_daily click_country_monthly
  click_events click_funnel_state click_hop_daily click_hop_monthly
  click_landing_daily click_landing_monthly click_landing_state click_month_log click_seal
  click_src_daily click_src_monthly
  counters d1_migrations kit_meta
  view_daily view_dim_daily view_geo_daily view_meta view_rollup_log
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
  echo "database: $db (the analytics database; the leads database is never exported by this script)"
  echo "would create: $out (mode 700)"
  echo "would export ${#tables[@]} tables (those that exist), one file each, mode 600:"
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

# Which of our tables exist in this database (older schemas may lack some).
listing=$(timeout "$limit" "${wrangler[@]}" d1 execute "$db" --remote --json \
  --command "SELECT name FROM sqlite_master WHERE type = 'table'" 2>/dev/null) || {
  echo "error: could not list the tables (wrong name, not signed in, or timed out)" >&2
  exit 1
}
existing=$(printf '%s' "$listing" | python3 -I -c '
import json, sys
data = json.load(sys.stdin)
rows = data[0]["results"] if isinstance(data, list) else data["results"]
for r in rows:
    print(r["name"])
') || {
  echo "error: unexpected answer from the database" >&2
  exit 1
}

count=0
exported=()
for t in "${tables[@]}"; do
  if ! printf '%s\n' "$existing" | grep -qx -- "$t"; then
    echo "skipped (not in this database): $t"
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
echo "done: $count tables of the analytics database $db written to $out"
echo "note: the tables are exported one call at a time, so this is not a single point-in-time snapshot."
echo "      For one consistent copy, pause both write routes first (see docs/backup-restore.md)."
echo "next: copy it to your offline copy, then run: (cd that folder && sha256sum -c SHA256SUMS)"
