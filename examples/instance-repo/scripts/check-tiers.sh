#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Require the staging and production settings to be distinct. Reads plain values from the environment (STAGING_* and
# PRODUCTION_*), prints only the NAME of a setting that is not distinct, never a value.
# A pair is compared only when both sides are set (production may be off), and
# compared after lowercasing, on the whole value. A value with a second line
# or a space around it is refused, not trimmed.
# Usage: scripts/check-tiers.sh
set -eu

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

names="STAGING_WORKER_NAME PRODUCTION_WORKER_NAME STAGING_PAGES_PROJECT PRODUCTION_PAGES_PROJECT"
names="$names STAGING_D1_DATABASE_ID PRODUCTION_D1_DATABASE_ID"
names="$names STAGING_RATELIMIT_NAMESPACE_ID PRODUCTION_RATELIMIT_NAMESPACE_ID"
names="$names STAGING_SITE_HOST PRODUCTION_SITE_HOST STAGING_ZONE_NAME PRODUCTION_ZONE_NAME"
names="$names DOPPLER_IDENTITY_ID_STAGING DOPPLER_IDENTITY_ID_PRODUCTION"

# Shape first, in this shell (a failure inside $( ) would not stop the script):
# one line, no space or tab around the value.
for name in $names; do
  v="${!name:-}"
  case "$v" in
    *"$nl"* | *"$cr"*) fail "$name must be a single line" ;;
    " "* | *" " | *"$tab"*) fail "$name must not have spaces or tabs around it" ;;
  esac
done

norm() { # name -> lowercased value on stdout
  local v="${!1:-}"
  printf '%s' "${v,,}"
}

differ() { # STAGING_NAME PRODUCTION_NAME [what is shared]
  local a b
  a="$(norm "$1")"
  b="$(norm "$2")"
  if [ -n "$a" ] && [ -n "$b" ] && [ "$a" = "$b" ]; then
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
sh="$(norm STAGING_SITE_HOST)"
pz="$(norm PRODUCTION_ZONE_NAME)"
ph="$(norm PRODUCTION_SITE_HOST)"
sz="$(norm STAGING_ZONE_NAME)"
if [ -n "$sh" ] && [ -n "$pz" ] && [ "$sh" = "$pz" ]; then
  fail "STAGING_SITE_HOST must differ from PRODUCTION_ZONE_NAME"
fi
if [ -n "$ph" ] && [ -n "$sz" ] && [ "$ph" = "$sz" ]; then
  fail "PRODUCTION_SITE_HOST must differ from STAGING_ZONE_NAME"
fi
echo "the tier settings are distinct"
