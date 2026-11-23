#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Plan only. Shows what OpenTofu would change on the zone; changes nothing.
#
#   TF_VAR_zone_id=<zone id> CLOUDFLARE_API_TOKEN=<token> deploy/edge/plan.sh
#
# There is no apply here on purpose: an apply is the owner's explicit step,
# typed by hand after reading this plan (docs/edge.md). The script reads the
# token from the environment and never prints it.
set -eu

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for var in TF_VAR_zone_id CLOUDFLARE_API_TOKEN; do
    if [ -z "${!var:-}" ]; then
        echo "plan: $var is not set" >&2
        exit 64 # not 2: with -detailed-exitcode, 2 means "changes planned"
    fi
done

cd "$here"
tofu init -input=false -lockfile=readonly >/dev/null
exec tofu plan -input=false -lock-timeout=60s -detailed-exitcode
