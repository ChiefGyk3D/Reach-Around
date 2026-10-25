#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Deploy this instance from your own machine (the same steps as the
# production workflow). Adapted from docs/examples/deploy-private.sh in the
# engine repository. Use it when you want to deploy without GitHub Actions,
# or to rehearse with --dry-run.
#
# What it does:
#   1. builds the site with the pinned engine image (scripts/validate.sh),
#   2. derives the link ids from the build manifest (written beside the site
#      output, never inside it),
#   3. writes a wrangler config into a private directory,
#   4. checks the shape of every value,
#   5. shows the plan and asks for confirmation (or stops at --dry-run),
#   6. runs the three wrangler commands, the only place the token exists.
#
# Usage: scripts/deploy-local.sh [--dry-run] [--yes] SETTINGS_FILE
#
# SETTINGS_FILE is a shell file of KEY=value lines, kept OUTSIDE this
# repository (mode 0600). Every value below is a placeholder.
#   ENGINE_DIR=/path/to/Reach-Around            # checkout of ENGINE_REF
#   PRIVATE_DIR=/path/to/private/deploy         # build output + wrangler config
#   CLOUDFLARE_ACCOUNT_ID=00000000000000000000000000000000
#   PAGES_PROJECT=my-links
#   D1_DATABASE_ID=00000000-0000-0000-0000-000000000000
#   RATELIMIT_NAMESPACE_ID=1001
#   SITE_HOST=links.example.invalid
#   ZONE_NAME=example.invalid                   # optional: creates the /e/* route
#   DOPPLER_PROJECT=my-project                  # your Doppler project
#   DOPPLER_CONFIG=prd                          # optional, default prd

set -euo pipefail

dry_run=0
assume_yes=0
settings=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --yes) assume_yes=1 ;;
    -*)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
    *) settings="$arg" ;;
  esac
done
if [ -z "$settings" ] || [ ! -f "$settings" ]; then
  echo "usage: $0 [--dry-run] [--yes] SETTINGS_FILE" >&2
  exit 2
fi

instance_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The token must not be inherited from the caller's shell. Doppler injects it
# on the three wrangler commands below and nowhere else.
unset CLOUDFLARE_API_TOKEN

# The settings file is trusted shell, so keep it private (mode 0600).
# shellcheck source=/dev/null  # the path is supplied at run time
. "$settings"

DOPPLER_CONFIG="${DOPPLER_CONFIG:-prd}"
ZONE_NAME="${ZONE_NAME:-}"

fail() {
  echo "error: $*" >&2
  exit 1
}

for name in ENGINE_DIR PRIVATE_DIR CLOUDFLARE_ACCOUNT_ID PAGES_PROJECT \
  D1_DATABASE_ID RATELIMIT_NAMESPACE_ID SITE_HOST DOPPLER_PROJECT; do
  [ -n "${!name:-}" ] || fail "$name is not set in $settings"
done

check() { # check NAME REGEX
  printf '%s' "${!1}" | grep -Eq "$2" || fail "$1 has the wrong shape"
}
check CLOUDFLARE_ACCOUNT_ID '^[0-9a-f]{32}$'
check D1_DATABASE_ID '^[0-9a-f-]{36}$'
check RATELIMIT_NAMESPACE_ID '^[0-9]{1,12}$'
check SITE_HOST '^[a-z0-9.-]{3,253}$'
check PAGES_PROJECT '^[a-z0-9-]{1,58}$'
check DOPPLER_PROJECT '^[A-Za-z0-9_-]{1,64}$'
check DOPPLER_CONFIG '^[a-z0-9_-]{1,32}$'
if [ -n "$ZONE_NAME" ]; then
  check ZONE_NAME '^[a-z0-9.-]{3,253}$'
fi
# Paths go into a sed expression and a TOML string below.
check ENGINE_DIR '^/[A-Za-z0-9._/-]+$'

[ -d "$ENGINE_DIR/worker" ] || fail "ENGINE_DIR has no worker/ directory"
case "$PRIVATE_DIR" in
  "$instance_dir" | "$instance_dir"/* | "$ENGINE_DIR" | "$ENGINE_DIR"/*)
    fail "PRIVATE_DIR must be outside this repository and the engine checkout"
    ;;
esac
command -v jq >/dev/null || fail "jq is required"

# The Worker code must be the release this repository pins.
want_ref="$("$instance_dir/scripts/read-lock.sh" ENGINE_REF)"
have_ref="$(git -C "$ENGINE_DIR" rev-parse HEAD)"
[ "$have_ref" = "$want_ref" ] ||
  fail "ENGINE_DIR is at $have_ref but engine.lock pins $want_ref"

umask 077
mkdir -p "$PRIVATE_DIR"
site_out="$PRIVATE_DIR/site-out"
# The manifest sits beside the site, never inside the directory Pages gets.
manifest_dir="$PRIVATE_DIR/site-manifest"
manifest="$manifest_dir/manifest.json"
wrangler_toml="$PRIVATE_DIR/wrangler.deploy.toml"

# 1. Build and validate. No token exists in this process.
rm -rf "$site_out" "$manifest_dir"
"$instance_dir/scripts/validate.sh" --out "$site_out" --manifest "$manifest"

# 2. Link ids come from the build manifest, so the Worker accepts exactly the
#    links the page publishes.
link_ids="$(jq -c '.links | keys' "$manifest")"
printf '%s' "$link_ids" |
  grep -Eq '^\[("[a-z0-9-]{1,40}"(, ?)?)+\]$' ||
  fail "the manifest produced no valid link ids"

# 3. Generate the wrangler config. Paths that wrangler resolves relative to
#    the config file become absolute, because the file lives elsewhere.
sed \
  -e "s|^main = .*|main = \"$ENGINE_DIR/worker/src/index.ts\"|" \
  -e "s|^migrations_dir = .*|migrations_dir = \"$ENGINE_DIR/worker/migrations\"|" \
  -e "s|^database_id = \"[^\"]*\"|database_id = \"${D1_DATABASE_ID}\"|" \
  -e "s|^namespace_id = \"[^\"]*\"|namespace_id = \"${RATELIMIT_NAMESPACE_ID}\"|" \
  -e "s|^SITE_HOST = \"[^\"]*\"|SITE_HOST = \"${SITE_HOST}\"|" \
  -e "s|^LINK_IDS = '[^']*'|LINK_IDS = '${link_ids}'|" \
  "$ENGINE_DIR/worker/wrangler.toml" >"$wrangler_toml"
route_note="none (clicks are not counted)"
if [ -n "$ZONE_NAME" ]; then
  printf '\n[[routes]]\npattern = "%s/e/*"\nzone_name = "%s"\n' \
    "$SITE_HOST" "$ZONE_NAME" >>"$wrangler_toml"
  route_note="$SITE_HOST/e/* in zone $ZONE_NAME"
fi
grep -qF "$D1_DATABASE_ID" "$wrangler_toml" || fail "database id not written"
grep -qF "$link_ids" "$wrangler_toml" || fail "link ids not written"

# 4. Show the plan. Nothing printed here is a secret.
cat <<PLAN
Plan
  engine           $("$instance_dir/scripts/read-lock.sh" ENGINE_IMAGE)
  site host        $SITE_HOST
  pages project    $PAGES_PROJECT
  worker config    $wrangler_toml
  link ids         $link_ids
  route            $route_note
  token source     Doppler project $DOPPLER_PROJECT, config $DOPPLER_CONFIG
PLAN

if [ "$dry_run" -eq 1 ]; then
  echo "dry run: stopping before any wrangler command."
  exit 0
fi

if [ "$assume_yes" -ne 1 ]; then
  printf 'Deploy to %s? Type "deploy" to continue: ' "$SITE_HOST"
  read -r answer
  [ "$answer" = "deploy" ] || fail "not confirmed"
fi

# 5. The only three commands that see the token. Doppler injects it into the
#    child process; this script never holds or prints it.
export CLOUDFLARE_ACCOUNT_ID
export WRANGLER_SEND_METRICS=false
with_token() {
  doppler run --project "$DOPPLER_PROJECT" --config "$DOPPLER_CONFIG" \
    --only-secrets CLOUDFLARE_API_TOKEN -- "$@"
}

cd "$ENGINE_DIR/worker"
# Install the Worker's dependencies without running their install scripts.
# No token exists for this command.
npm ci --ignore-scripts
with_token npx --no-install wrangler d1 migrations apply DB --remote \
  --config "$wrangler_toml"
with_token npx --no-install wrangler deploy --config "$wrangler_toml"
with_token npx --no-install wrangler pages deploy "$site_out" \
  --project-name "$PAGES_PROJECT" --branch main --commit-dirty=true
echo "done."
