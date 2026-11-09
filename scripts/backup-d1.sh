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
# Prints table names and file counts only: never an id, a row or wrangler's own
# output. Every wrangler call is bounded by BACKUP_TIMEOUT seconds (default 120,
# 1 to 900). Uses `wrangler d1 export --remote` (set WRANGLER to another command,
# for example a test stub). The dry run makes no network call and writes nothing.
#
# Exit: 0 done or dry run, 1 a step failed, 2 usage, 3 refused (unsafe folder).

set -euo pipefail

# Tables worth keeping. Everything else in the database is left out on purpose.
tables=(
  alert_dedup alert_quota
  click_budget click_capacity click_country_daily click_country_monthly
  click_events click_funnel_state click_month_log click_seal
  click_src_daily click_src_monthly
  counters kit_meta
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
  echo "would export ${#tables[@]} tables (those that exist), one file each, mode 600:"
  printf '  %s\n' "${tables[@]}"
  echo "never exported: leads, kit_events, visitor_seen, view_events"
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
  count=$((count + 1))
done

[ "$count" -gt 0 ] || {
  echo "error: none of the expected tables exist in this database" >&2
  exit 1
}

# Check everything written, in one pass, against the repo's allowlist minus the
# tables we leave out on purpose. Reports line numbers and kinds, never values.
python3 -I - "$here/dump-schema.json" "$out/.allowlist.json" <<'PY'
import json, sys

cfg = json.load(open(sys.argv[1]))
for name in ("leads", "kit_events", "visitor_seen", "view_events"):
    cfg["tables"].pop(name, None)
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
echo "next: copy it to your offline copy, then run: (cd that folder && sha256sum -c SHA256SUMS)"
