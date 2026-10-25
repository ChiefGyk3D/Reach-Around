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
#   PURGE_MAX                optional cap on URLs purged per run (default 300,
#                            hard ceiling 1000; anything else means the default).
#   PURGE_BUDGET_SECONDS     optional total time budget (default 240, ceiling 600).
# The list may come from an earlier run's artifact, so the work is bounded: the
# first PURGE_MAX paths are purged (the list puts entry points first) and the
# rest are reported, never sent.
#
# It never fails the deploy: a missing setting is a notice, an API failure is a
# warning and a line in the job summary. The token goes to curl on stdin as a
# header file (a mode-0600 file under RUNNER_TEMP, removed on exit), and the
# JSON body goes to curl on stdin (--data-binary @-), so neither the token nor
# the URLs appear in a process list, and the token is never echoed. A path over
# 2000 characters is skipped. Shell tracing is never turned on here.
set -u

BATCH=30 # URLs per request (the Cloudflare limit this was written against)
MAX_PATH_CHARS=2000
MAX_DEFAULT=300
MAX_CEILING=1000
BUDGET_DEFAULT=240
BUDGET_CEILING=600

# A positive integer, clamped to a ceiling; anything else is the default.
bounded() {
  case "$1" in
    '' | *[!0-9]*) echo "$2" ;;
    *)
      if [ "$1" -lt 1 ]; then
        echo "$2"
      elif [ "$1" -gt "$3" ]; then
        echo "$3"
      else
        echo "$1"
      fi
      ;;
  esac
}
max_urls=$(bounded "${PURGE_MAX:-}" "$MAX_DEFAULT" "$MAX_CEILING")
budget=$(bounded "${PURGE_BUDGET_SECONDS:-}" "$BUDGET_DEFAULT" "$BUDGET_CEILING")
started=$SECONDS
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

# The Authorization header lives in a private file for the life of the script.
umask 077
header_file=$(mktemp "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/purge-header.XXXXXX") || {
  warn "could not create a private header file; skipping"
  exit 0
}
trap 'rm -f "$header_file"' EXIT
printf 'Authorization: Bearer %s\n' "$CLOUDFLARE_PURGE_TOKEN" >"$header_file"

batch_json=""
batch_count=0
too_long=0
total=0
failed=0
requests=0
accepted=0
seen=0
out_of_time=0

send() {
  [ "$batch_count" -gt 0 ] || return 0
  if [ $((SECONDS - started)) -ge "$budget" ]; then
    out_of_time=$((out_of_time + batch_count))
    batch_json=""
    batch_count=0
    return 0
  fi
  requests=$((requests + 1))
  body="{\"files\":[${batch_json}]}"
  response=$(
    printf '%s' "$body" |
      curl --silent --show-error --proto '=https' --tlsv1.2 --max-time 30 \
        --request POST \
        --header "@${header_file}" \
        --header 'Content-Type: application/json' \
        --data-binary @- \
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
  seen=$((seen + 1))
  # Past the cap a line is only counted, so a huge list costs almost nothing.
  if [ "$accepted" -ge "$max_urls" ]; then
    continue
  fi
  if [ "${#line}" -gt "$MAX_PATH_CHARS" ]; then
    too_long=$((too_long + 1))
    continue
  fi
  # a plain absolute path: no scheme, no //, no query, fragment or whitespace
  if ! printf '%s' "$line" | grep -Eq '^/([A-Za-z0-9._~-][A-Za-z0-9._~/-]*)?$'; then
    warn "skipped a path that is not plain URL path characters"
    continue
  fi
  accepted=$((accepted + 1))
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

if [ "$seen" -gt "$accepted" ]; then
  warn "the list has ${seen} paths; only the first ${accepted} (entry points first) were purged, the cap is ${max_urls}"
fi
if [ "$too_long" -gt 0 ]; then
  warn "${too_long} path(s) longer than ${MAX_PATH_CHARS} characters were skipped"
fi
if [ "$out_of_time" -gt 0 ]; then
  warn "the ${budget}-second time budget ran out; ${out_of_time} URL(s) were not sent"
fi
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
