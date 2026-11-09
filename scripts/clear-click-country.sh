#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Owner-run, one time: clear the country column of the old raw click rows.
#
# A raw click row now carries at most a source label and never a country (country
# lives only in the aggregate counter click_country_daily). Rows written before the
# new Worker still hold a country. This clears them so the two stores cannot be
# differenced. It is NOT a migration on purpose: a migration runs once, in the
# order the files apply, and would run before the Worker is live (the counters
# would be empty and the cleanup would silently not happen).
#
# DEPLOY ORDER: apply migration 0011, deploy the Worker, check that
# click_country_daily gains rows, THEN run this script.
#
# Usage: scripts/clear-click-country.sh DATABASE            # dry run: counts only
#        scripts/clear-click-country.sh DATABASE --apply    # does it
#
# DATABASE is the D1 database name. Uses `wrangler d1 execute --remote` (set
# WRANGLER to another command, for example a test stub). Prints counts only.
# It refuses (exit 3) when the counters are empty or show no activity in the last
# two days, so it cannot run before the new Worker is counting. The change is not
# reversible: export first if the country of old clicks matters.
#
# Exit: 0 done or dry run, 2 usage, 3 refused by the precheck, 1 a query failed.

set -euo pipefail

usage() {
  echo "usage: $0 DATABASE [--apply]" >&2
  exit 2
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || usage
db="$1"
apply=0
if [ "$#" -eq 2 ]; then
  [ "$2" = "--apply" ] || usage
  apply=1
fi
case "$db" in
  "" | -* | *[!A-Za-z0-9._-]*) echo "error: bad database name" >&2; exit 2 ;;
esac

# WRANGLER may be a command with arguments; split it into an array once.
read -r -a wrangler <<<"${WRANGLER:-npx wrangler}"

# Run one SQL command and print the first numeric value of the first row.
count() {
  local out
  out=$("${wrangler[@]}" d1 execute "$db" --remote --json --command "$1") || {
    echo "error: a query failed" >&2
    exit 1
  }
  printf '%s' "$out" | python3 -I -c '
import json, sys
data = json.load(sys.stdin)
rows = data[0]["results"] if isinstance(data, list) else data["results"]
print(int(next(iter(rows[0].values()))))
' || {
    echo "error: unexpected answer from the database" >&2
    exit 1
  }
}

counters=$(count "SELECT count(*) AS n FROM click_country_daily")
recent=$(count "SELECT count(*) AS n FROM click_country_daily WHERE day >= date('now', '-2 day')")
if [ "$counters" -eq 0 ] || [ "$recent" -eq 0 ]; then
  echo "refused: click_country_daily has no rows from the last two days ($counters in all)." >&2
  echo "Deploy the counting Worker and check that the counters rise first." >&2
  exit 3
fi

pending=$(count "SELECT count(*) AS n FROM click_events WHERE country IS NOT NULL")
echo "raw click rows holding a country: $pending (counter rows: $counters)"

if [ "$apply" -eq 0 ]; then
  echo "dry run: nothing changed. Re-run with --apply to clear them."
  exit 0
fi

"${wrangler[@]}" d1 execute "$db" --remote --json \
  --command "UPDATE click_events SET country = NULL WHERE country IS NOT NULL" >/dev/null || {
  echo "error: the update failed" >&2
  exit 1
}
left=$(count "SELECT count(*) AS n FROM click_events WHERE country IS NOT NULL")
echo "cleared. raw click rows still holding a country: $left"
[ "$left" -eq 0 ]
