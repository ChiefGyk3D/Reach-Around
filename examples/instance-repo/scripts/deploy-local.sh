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
#   DOPPLER_CONFIG=dev                          # optional, default dev

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

DOPPLER_CONFIG="${DOPPLER_CONFIG:-dev}"
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

# The three pins must describe one release (image, repository, commit, tag), and
# the checkout must be that release's repository, at that tag, with the schema
# the manifest names and no local edits (issue #768, F-11 and F-60).
"$instance_dir/scripts/read-lock.sh" --release >/dev/null ||
  fail "engine.lock and engine.release.json do not describe one release"
want_repo="$("$instance_dir/scripts/read-lock.sh" ENGINE_REPO)"
want_repo_lc="$(printf '%s' "$want_repo" | tr '[:upper:]' '[:lower:]')"
remote_ok=0
while read -r remote_name; do
  [ -n "$remote_name" ] || continue
  remote_url="$(git -C "$ENGINE_DIR" remote get-url "$remote_name" 2>/dev/null || true)"
  # Only GitHub: https://github.com/O/N(.git), ssh://git@github.com/O/N(.git),
  # git@github.com:O/N(.git). A URL with credentials or another host never matches.
  case "$remote_url" in
    https://github.com/* | ssh://git@github.com/* | git@github.com:*) ;;
    *) continue ;;
  esac
  remote_path="$(printf '%s' "$remote_url" |
    sed -E 's#^(https://github\.com/|ssh://git@github\.com/|git@github\.com:)##; s#\.git$##' |
    tr '[:upper:]' '[:lower:]')"
  if [ "$remote_path" = "$want_repo_lc" ]; then remote_ok=1; fi
done < <(git -C "$ENGINE_DIR" remote)
[ "$remote_ok" -eq 1 ] ||
  fail "no remote of ENGINE_DIR is github.com/$want_repo (the repository engine.lock names)"
want_tag="$(jq -er '.tag | strings' "$instance_dir/engine.release.json")"
tag_commit="$(git -C "$ENGINE_DIR" rev-parse --verify --quiet "refs/tags/${want_tag}^{commit}" || true)"
[ -n "$tag_commit" ] ||
  fail "ENGINE_DIR has no tag $want_tag (run: git -C ENGINE_DIR fetch --tags)"
[ "$tag_commit" = "$want_ref" ] ||
  fail "tag $want_tag in ENGINE_DIR is $tag_commit, not the pinned $want_ref"
want_schema="$(jq -er '.d1_schema | strings' "$instance_dir/engine.release.json")"
have_schema=""
for migration in "$ENGINE_DIR"/worker/migrations/[0-9][0-9][0-9][0-9]_*.sql; do
  [ -e "$migration" ] || continue
  have_schema="$(basename "$migration" | cut -c1-4)"
done
[ "$have_schema" = "$want_schema" ] ||
  fail "ENGINE_DIR migrations end at ${have_schema:-none} but engine.release.json says $want_schema"
[ -z "$(git -C "$ENGINE_DIR" status --porcelain --untracked-files=no)" ] ||
  fail "ENGINE_DIR has uncommitted changes to tracked files"

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

# The migrations, the Worker and Pages are three separate changes, and a
# failure after the first leaves the old Worker on the new schema (issue #768,
# F-10). So: record the Worker version that is live now (the JSON shape is
# INFERRED, see docs/deploy.md; an empty answer means "unknown"), and on any
# failure from here on print the exact rollback command. Migrations must be
# additive (expand, migrate, contract), so the old Worker keeps working on the
# new schema; a rollback restores the code, never the schema.
prev_version="$(with_token npx --no-install wrangler deployments status \
  --config "$wrangler_toml" --json 2>/dev/null |
  jq -r '[.. | objects | .version_id? | strings] | .[0] // empty' 2>/dev/null || true)"
case "$prev_version" in
  *[!0-9a-f-]*) prev_version="" ;;
esac
on_failure() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "error: a deploy step failed (exit $status)." >&2
    if [ -n "$prev_version" ]; then
      echo "To restore the previous Worker code: npx wrangler rollback $prev_version --config $wrangler_toml --message rollback" >&2
    else
      echo "The previous Worker version could not be read; use 'wrangler deployments list' to pick one." >&2
    fi
  fi
}
trap on_failure EXIT
with_token npx --no-install wrangler d1 migrations apply DB --remote \
  --config "$wrangler_toml"
with_token npx --no-install wrangler deploy --config "$wrangler_toml"
with_token npx --no-install wrangler pages deploy "$site_out" \
  --project-name "$PAGES_PROJECT" --branch main --commit-dirty=true
trap - EXIT
echo "done."
