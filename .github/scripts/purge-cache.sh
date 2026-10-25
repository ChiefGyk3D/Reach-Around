#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Optional backstop for the deploy workflow: purge URL paths from the Cloudflare
# edge cache after a deploy. Correctness does not depend on it, because hashed
# file names change with their content and pages are revalidated; this only
# clears copies of paths that already existed at the edge (a removed file, a
# stable-name file that changed).
#
# Usage: purge-cache.sh PATHS_FILE
#   PATHS_FILE   one URL path per line (from `reach-around deploy-purge-list`);
#                blank lines and lines starting with # are ignored.
# Environment:
#   CLOUDFLARE_PURGE_TOKEN   API token, scope Zone > Cache Purge on ONE zone.
#   ZONE_ID                  the zone's id (32 hex digits).
#   SITE_HOST                the host whose URLs are purged (plain hostname).
#   GITHUB_STEP_SUMMARY      optional; a summary is appended when set.
#
# It never fails the deploy: a missing setting is a notice, an API failure is a
# warning and a line in the job summary. The token goes to curl on stdin as a
# header file ("-H @-"), so it never appears in a process list, and it is never
# echoed. Shell tracing is never turned on here.
set -u

BATCH=30 # URLs per request (the Cloudflare limit this was written against)
API="https://api.cloudflare.com/client/v4"

summary() {
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    printf '%s\n' "$1" >>"$GITHUB_STEP_SUMMARY"
  fi
}

notice() {
  echo "::notice::$1"
  summary "Cache purge: $1"
}

warn() {
  echo "::warning::$1"
  summary "Cache purge WARNING: $1"
}

paths_file="${1:-}"
if [ -z "$paths_file" ] || [ ! -f "$paths_file" ]; then
  warn "no list of paths to purge was given; skipping"
  exit 0
fi

if [ -z "${CLOUDFLARE_PURGE_TOKEN:-}" ] || [ -z "${ZONE_ID:-}" ]; then
  notice "CLOUDFLARE_PURGE_TOKEN or ZONE_ID is not set; skipping (this step is optional, see docs/deploy.md)"
  exit 0
fi

if ! printf '%s' "$ZONE_ID" | grep -Eq '^[0-9a-f]{32}$'; then
  warn "ZONE_ID is not a 32-character zone id; skipping"
  exit 0
fi
if ! printf '%s' "${SITE_HOST:-}" | grep -Eq '^[a-z0-9.-]{3,253}$'; then
  warn "SITE_HOST is not a plain hostname; skipping"
  exit 0
fi

batch_json=""
batch_count=0
total=0
failed=0
requests=0

send() {
  [ "$batch_count" -gt 0 ] || return 0
  requests=$((requests + 1))
  body="{\"files\":[${batch_json}]}"
  response=$(
    printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_PURGE_TOKEN" |
      curl --silent --show-error --proto '=https' --tlsv1.2 --max-time 30 \
        --request POST \
        --header @- \
        --header 'Content-Type: application/json' \
        --data "$body" \
        "${API}/zones/${ZONE_ID}/purge_cache" 2>&1
  )
  status=$?
  if [ "$status" -eq 0 ] &&
    printf '%s' "$response" | grep -Eq '"success"[[:space:]]*:[[:space:]]*true'; then
    total=$((total + batch_count))
  else
    failed=$((failed + batch_count))
    # The response holds Cloudflare's error list, never the token.
    detail=$(printf '%s' "$response" | tr -d '\r\n' | cut -c1-300)
    warn "purge request ${requests} (${batch_count} URLs) failed (curl exit ${status}): ${detail}"
  fi
  batch_json=""
  batch_count=0
}

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in '' | '#'*) continue ;; esac
  # a plain absolute path: no scheme, no //, no query, fragment or whitespace
  if ! printf '%s' "$line" | grep -Eq '^/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$'; then
    warn "skipped a path that is not plain URL path characters"
    continue
  fi
  url="https://${SITE_HOST}${line}"
  if [ "$batch_count" -gt 0 ]; then
    batch_json="${batch_json},"
  fi
  batch_json="${batch_json}\"${url}\""
  batch_count=$((batch_count + 1))
  if [ "$batch_count" -ge "$BATCH" ]; then
    send
  fi
done <"$paths_file"
send

if [ "$requests" -eq 0 ]; then
  notice "nothing to purge"
  exit 0
fi
if [ "$failed" -eq 0 ]; then
  notice "purged ${total} URL(s) in ${requests} request(s)"
else
  warn "purged ${total} URL(s); ${failed} URL(s) were not purged (the deploy itself is unaffected)"
fi
exit 0
