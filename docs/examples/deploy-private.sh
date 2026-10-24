#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Sanitized example of a private deploy script for a real Reach Around
# instance. Copy it OUTSIDE the repository, fill in the settings file, and
# keep both private. See docs/deploy.md ("A private deploy script").
#
# What it does:
#   1. builds the site into a directory outside the repository,
#   2. derives the link ids from the build manifest,
#   3. writes a wrangler config into a private directory,
#   4. checks the shape of every value,
#   5. shows the plan and asks for confirmation (or stops at --dry-run),
#   6. runs the three wrangler commands, the only place the token exists.
#
# Usage: deploy-private.sh [--dry-run] [--yes] SETTINGS_FILE
#
# SETTINGS_FILE is a shell file of KEY=value lines. Every value below is a
# placeholder; real values never go in the repository.
#   REPO_DIR=/path/to/Reach-Around              # a checkout of main
#   INSTANCE_CONFIG=/path/to/instance/site.yaml # your private site config
#   PRIVATE_DIR=/path/to/private/deploy         # build output + wrangler config
#   CLOUDFLARE_ACCOUNT_ID=00000000000000000000000000000000
#   PAGES_PROJECT=reach-around
#   D1_DATABASE_ID=00000000-0000-0000-0000-000000000000
#   RATELIMIT_NAMESPACE_ID=1001
#   SITE_HOST=links.example.invalid
#   ZONE_NAME=example.invalid                   # optional: creates the /e/* route
#   DOPPLER_CONFIG=prd                          # optional, default prd

set -euo pipefail

dry_run=0
assume_yes=0
settings=""
for arg in "$@"; do
  case "$arg" in
    --dry-run) dry_run=1 ;;
    --yes) assume_yes=1 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) settings="$arg" ;;
  esac
done
if [ -z "$settings" ] || [ ! -f "$settings" ]; then
  echo "usage: $0 [--dry-run] [--yes] SETTINGS_FILE" >&2
  exit 2
fi

# The token must not be inherited from the caller's shell. It is injected by
# Doppler on the three wrangler commands below and nowhere else.
unset CLOUDFLARE_API_TOKEN

# The settings file is trusted shell, so keep it private (mode 0600).
# shellcheck source=/dev/null  # the path is supplied at run time
. "$settings"

DOPPLER_CONFIG="${DOPPLER_CONFIG:-prd}"
ZONE_NAME="${ZONE_NAME:-}"

fail() { echo "error: $*" >&2; exit 1; }

for name in REPO_DIR INSTANCE_CONFIG PRIVATE_DIR CLOUDFLARE_ACCOUNT_ID \
            PAGES_PROJECT D1_DATABASE_ID RATELIMIT_NAMESPACE_ID SITE_HOST; do
  [ -n "${!name:-}" ] || fail "$name is not set in $settings"
done

# Shape checks. These match the CI workflow's, plus the account id.
check() { # check NAME REGEX
  printf '%s' "${!1}" | grep -Eq "$2" || fail "$1 has the wrong shape"
}
check CLOUDFLARE_ACCOUNT_ID '^[0-9a-f]{32}$'
check D1_DATABASE_ID '^[0-9a-f-]{36}$'
check RATELIMIT_NAMESPACE_ID '^[0-9]{1,12}$'
check SITE_HOST '^[a-z0-9.-]{3,253}$'
check PAGES_PROJECT '^[a-z0-9-]{1,58}$'
check DOPPLER_CONFIG '^[a-z0-9_-]{1,32}$'
if [ -n "$ZONE_NAME" ]; then
  check ZONE_NAME '^[a-z0-9.-]{3,253}$'
fi
# Paths go into a sed expression and a TOML string below.
check REPO_DIR '^/[A-Za-z0-9._/-]+$'

[ -d "$REPO_DIR/worker" ] || fail "REPO_DIR has no worker/ directory"
[ -f "$INSTANCE_CONFIG" ] || fail "INSTANCE_CONFIG is not a file"
case "$PRIVATE_DIR" in
  "$REPO_DIR"|"$REPO_DIR"/*) fail "PRIVATE_DIR must be outside the repository" ;;
esac
command -v jq >/dev/null || fail "jq is required"
command -v reach-around >/dev/null || fail "reach-around is not installed"

umask 077
mkdir -p "$PRIVATE_DIR"
site_out="$PRIVATE_DIR/site-out"
wrangler_toml="$PRIVATE_DIR/wrangler.deploy.toml"

# 1. Build outside the repository. No token exists in this process.
rm -rf "$site_out"
reach-around build --config "$INSTANCE_CONFIG" --out "$site_out"

# 2. Link ids come from the build manifest, so the Worker accepts exactly the
#    links the page publishes.
link_ids="$(jq -c '.links | keys' "$site_out/manifest.json")"
printf '%s' "$link_ids" \
  | grep -Eq '^\[("[a-z0-9-]{1,40}"(, ?)?)+\]$' \
  || fail "the manifest produced no valid link ids"

# 3. Generate the wrangler config. Paths that wrangler resolves relative to
#    the config file become absolute, because the file lives elsewhere.
sed \
  -e "s|^main = .*|main = \"$REPO_DIR/worker/src/index.ts\"|" \
  -e "s|^migrations_dir = .*|migrations_dir = \"$REPO_DIR/worker/migrations\"|" \
  -e "s|^database_id = \"[^\"]*\"|database_id = \"${D1_DATABASE_ID}\"|" \
  -e "s|^namespace_id = \"[^\"]*\"|namespace_id = \"${RATELIMIT_NAMESPACE_ID}\"|" \
  -e "s|^SITE_HOST = \"[^\"]*\"|SITE_HOST = \"${SITE_HOST}\"|" \
  -e "s|^LINK_IDS = '[^']*'|LINK_IDS = '${link_ids}'|" \
  "$REPO_DIR/worker/wrangler.toml" > "$wrangler_toml"
route_note="none (clicks are not counted)"
if [ -n "$ZONE_NAME" ]; then
  printf '\n[[routes]]\npattern = "%s/e/*"\nzone_name = "%s"\n' \
    "$SITE_HOST" "$ZONE_NAME" >> "$wrangler_toml"
  route_note="$SITE_HOST/e/* in zone $ZONE_NAME"
fi
grep -qF "$D1_DATABASE_ID" "$wrangler_toml" || fail "database id not written"
grep -qF "$link_ids" "$wrangler_toml" || fail "link ids not written"

# 4. Show the plan. Nothing printed here is a secret.
cat <<PLAN
Plan
  site host        $SITE_HOST
  pages project    $PAGES_PROJECT
  worker config    $wrangler_toml
  link ids         $link_ids
  route            $route_note
  token source     Doppler project reach-around, config $DOPPLER_CONFIG
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
  doppler run --project reach-around --config "$DOPPLER_CONFIG" \
    --only-secrets CLOUDFLARE_API_TOKEN -- "$@"
}

cd "$REPO_DIR/worker"
with_token npx --no-install wrangler d1 migrations apply DB --remote \
  --config "$wrangler_toml"
with_token npx --no-install wrangler deploy --config "$wrangler_toml"
with_token npx --no-install wrangler pages deploy "$site_out" \
  --project-name "$PAGES_PROJECT" --branch main --commit-dirty=true
echo "done."
