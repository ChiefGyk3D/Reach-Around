#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Check the staging and production settings and hand the checked values on.
# Reads plain values from the environment (STAGING_*, PRODUCTION_*, and the
# shared CLOUDFLARE_ACCOUNT_ID, DOPPLER_PROJECT, ALLOW_STAGING_DEPLOY and
# ALLOW_PRODUCTION_DEPLOY). Prints only the NAME of a setting that fails, never a
# value.
#
# Rules:
#   - Every value is one ASCII line with no space or tab around it.
#   - Values are compared after normalization: lowercase; a host or zone has one
#     trailing dot and a default port (:80, :443) removed and must then be a
#     plain ASCII name (an international name is written in its xn-- form); a
#     rate-limit namespace id is digits only and is compared as an integer.
#   - Required settings. Staging: worker name, Pages project, D1 database id,
#     rate-limit namespace id, site host and secret-manager identity. Production
#     needs the same set whenever any production setting is set or
#     ALLOW_PRODUCTION_DEPLOY is `true`; with none set, production is off. The
#     zone name is optional in both tiers. CLOUDFLARE_ACCOUNT_ID and
#     DOPPLER_PROJECT are required.
#   - Each pair below must differ after normalization.
#
# With GITHUB_OUTPUT set, the normalized values are written there (names in
# lowercase, e.g. staging_worker_name). The deploy jobs read only those outputs,
# so the values that were checked are the values deployed.
# Usage: scripts/check-tiers.sh
set -eu
export LC_ALL=C

nl="$(printf '\nx')"
nl="${nl%x}"
cr="$(printf '\rx')"
cr="${cr%x}"
tab="$(printf '\tx')"
tab="${tab%x}"

fail() {
  echo "::error::$1"
  exit 1
}

host_names="STAGING_SITE_HOST PRODUCTION_SITE_HOST STAGING_ZONE_NAME PRODUCTION_ZONE_NAME"
number_names="STAGING_RATELIMIT_NAMESPACE_ID PRODUCTION_RATELIMIT_NAMESPACE_ID"
token_names="STAGING_WORKER_NAME PRODUCTION_WORKER_NAME STAGING_PAGES_PROJECT PRODUCTION_PAGES_PROJECT"
token_names="$token_names STAGING_D1_DATABASE_ID PRODUCTION_D1_DATABASE_ID"
token_names="$token_names DOPPLER_IDENTITY_ID_STAGING DOPPLER_IDENTITY_ID_PRODUCTION"
shared_names="CLOUDFLARE_ACCOUNT_ID DOPPLER_PROJECT ALLOW_STAGING_DEPLOY ALLOW_PRODUCTION_DEPLOY"
all_names="$host_names $number_names $token_names $shared_names"

staging_required="STAGING_WORKER_NAME STAGING_PAGES_PROJECT STAGING_D1_DATABASE_ID STAGING_RATELIMIT_NAMESPACE_ID STAGING_SITE_HOST DOPPLER_IDENTITY_ID_STAGING"
production_required="PRODUCTION_WORKER_NAME PRODUCTION_PAGES_PROJECT PRODUCTION_D1_DATABASE_ID PRODUCTION_RATELIMIT_NAMESPACE_ID PRODUCTION_SITE_HOST DOPPLER_IDENTITY_ID_PRODUCTION"
production_all="$production_required PRODUCTION_ZONE_NAME"

# Shape first, in this shell (a failure inside $( ) would not stop the script):
# one line, no space or tab around the value.
for name in $all_names; do
  v="${!name:-}"
  case "$v" in
    *"$nl"* | *"$cr"*) fail "$name must be a single line" ;;
    " "* | *" " | *"$tab"*) fail "$name must not have spaces or tabs around it" ;;
  esac
done

declare -A N=()

for name in $token_names; do
  v="${!name:-}"
  case "$v" in
    *[!A-Za-z0-9._-]*) fail "$name must be ASCII letters, digits, dots, hyphens and underscores" ;;
  esac
  N[$name]="${v,,}"
done

for name in $number_names; do
  v="${!name:-}"
  if [ -n "$v" ]; then
    [[ "$v" =~ ^[0-9]{1,12}$ ]] || fail "$name must be ASCII digits only (up to 12)"
    v="${v#"${v%%[!0]*}"}"
    [ -n "$v" ] || v=0
  fi
  N[$name]="$v"
done

host_re='^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$'
for name in $host_names; do
  v="${!name:-}"
  v="${v,,}"
  if [ -n "$v" ]; then
    case "$v" in
      *[!a-z0-9.:-]*) fail "$name must be ASCII letters, digits, dots and hyphens (write an international name in its xn-- form)" ;;
    esac
    if [ "${v%:443}" != "$v" ]; then v="${v%:443}"; elif [ "${v%:80}" != "$v" ]; then v="${v%:80}"; fi
    case "$v" in
      *:*) fail "$name must not carry a port" ;;
    esac
    v="${v%.}"
    [ "${#v}" -le 253 ] && [[ "$v" =~ $host_re ]] || fail "$name is not a plain host name"
  fi
  N[$name]="$v"
done

# Shared values: the account id is 32 lowercase hex, the project a plain name,
# and a switch is on only when it is exactly `true`.
v="${CLOUDFLARE_ACCOUNT_ID:-}"
v="${v,,}"
[[ "$v" =~ ^[0-9a-f]{32}$ ]] || fail "CLOUDFLARE_ACCOUNT_ID is not a 32-character id"
N[CLOUDFLARE_ACCOUNT_ID]="$v"
v="${DOPPLER_PROJECT:-}"
[[ "$v" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$ ]] || fail "DOPPLER_PROJECT is not a plain project name"
N[DOPPLER_PROJECT]="$v"
for name in ALLOW_STAGING_DEPLOY ALLOW_PRODUCTION_DEPLOY; do
  if [ "${!name:-}" = "true" ]; then N[$name]=true; else N[$name]=false; fi
done

# Required settings.
for name in $staging_required; do
  [ -n "${N[$name]}" ] || fail "$name is required"
done
production_on=0
[ "${N[ALLOW_PRODUCTION_DEPLOY]}" = "true" ] && production_on=1
for name in $production_all; do
  [ -z "${N[$name]}" ] || production_on=1
done
if [ "$production_on" = 1 ]; then
  for name in $production_required; do
    [ -n "${N[$name]}" ] || fail "$name is required when production is set up"
  done
fi

differ() { # STAGING_NAME PRODUCTION_NAME [what is shared]
  if [ -n "${N[$1]}" ] && [ "${N[$1]}" = "${N[$2]}" ]; then
    fail "$1 and $2 must differ (${3:-this setting} is per tier)"
  fi
}

differ STAGING_D1_DATABASE_ID PRODUCTION_D1_DATABASE_ID "a D1 database"
differ STAGING_RATELIMIT_NAMESPACE_ID PRODUCTION_RATELIMIT_NAMESPACE_ID "a rate-limit namespace"
differ STAGING_WORKER_NAME PRODUCTION_WORKER_NAME "a Worker (its KV and Durable Object namespaces and bindings go with it)"
differ STAGING_PAGES_PROJECT PRODUCTION_PAGES_PROJECT "a Pages project"
differ STAGING_SITE_HOST PRODUCTION_SITE_HOST "a host or route"
differ DOPPLER_IDENTITY_ID_STAGING DOPPLER_IDENTITY_ID_PRODUCTION "a secret-manager identity"

# The same zone is fine (two hosts in one zone). A host that is the other
# tier's whole zone is refused.
if [ -n "${N[STAGING_SITE_HOST]}" ] && [ "${N[STAGING_SITE_HOST]}" = "${N[PRODUCTION_ZONE_NAME]}" ]; then
  fail "STAGING_SITE_HOST must differ from PRODUCTION_ZONE_NAME"
fi
if [ -n "${N[PRODUCTION_SITE_HOST]}" ] && [ "${N[PRODUCTION_SITE_HOST]}" = "${N[STAGING_ZONE_NAME]}" ]; then
  fail "PRODUCTION_SITE_HOST must differ from STAGING_ZONE_NAME"
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  for name in $all_names; do
    printf '%s=%s\n' "${name,,}" "${N[$name]}" >> "$GITHUB_OUTPUT"
  done
fi
echo "the tier settings are checked and distinct"
