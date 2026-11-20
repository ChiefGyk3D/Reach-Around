#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Day-2 acceptance in one command: export Reach Around's own daily numbers from
# D1 (SELECT only) and, with --beacons, reconcile them against Beacons.
#
#   RA_D1_TOKEN=... RA_D1_ACCOUNT_ID=... RA_D1_DATABASE_ID=... \
#     scripts/proof-export.sh --out DIR --since DAY --until DAY [--site links] [--beacons FILE]
#
# Credentials come from the environment only. All logic lives in
# scripts/make-ours-days.py (read its header); this wrapper only runs it.
# Exit code: the script's own (0 ok, 1 mismatch or gap, 2 refused, 3 query failed).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -I "$here/make-ours-days.py" "$@"
