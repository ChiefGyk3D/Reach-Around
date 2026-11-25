#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Read-only check of the old-support-host redirect (issue #888, runbook 3.5).
#
#   SRC_HOST=support-staging.example.com EXP_HOST=links-staging.example.com \
#     scripts/verify-support-redirect.sh
#
# Both hosts are required and must differ; nothing is defaulted, so a copy
# made for production cannot silently call staging. Optional:
#   EXPECT_STATUS  302 (default) or 301 (after the owner promotes the rule)
#   OTHER_PATH     a path the old host serves that must NOT redirect to Support
#                  (default /other-path)
#   VERIFY_SCHEME  https (default). http is accepted only for a 127.0.0.1
#                  host, so the tests can use a local server.
#
# Checks, each of which must run and pass (no -L is ever used):
#   1. two probe requests to the old host return EXPECT_STATUS and a Location
#      equal, character for character, to https://$EXP_HOST/support/ plus the
#      exact query that was sent (proves the query string is preserved, with a
#      repeated parameter and encoded characters that are not in the rule);
#   2. OTHER_PATH does not redirect to the Support page;
#   3. the Support page answers 200.
# Exit 0 only after all of them ran. Exit 1 on any failed check, 2 on a usage
# error. Every curl failure and every unparsable response is a failure.
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
usage() { echo "usage error: $*" >&2; exit 2; }

SRC_HOST="${SRC_HOST:-}"
EXP_HOST="${EXP_HOST:-}"
EXPECT_STATUS="${EXPECT_STATUS:-302}"
OTHER_PATH="${OTHER_PATH:-/other-path}"
SCHEME="${VERIFY_SCHEME:-https}"

host_re='^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'
[ -n "$SRC_HOST" ] || usage "SRC_HOST is required"
[ -n "$EXP_HOST" ] || usage "EXP_HOST is required"
[[ "$SRC_HOST" =~ $host_re ]] || usage "SRC_HOST is not a plain host"
[[ "$EXP_HOST" =~ $host_re ]] || usage "EXP_HOST is not a plain host"
[ "$SRC_HOST" != "$EXP_HOST" ] || usage "SRC_HOST and EXP_HOST must differ"
case "$EXPECT_STATUS" in 301 | 302) ;; *) usage "EXPECT_STATUS must be 301 or 302" ;; esac
[[ "$OTHER_PATH" =~ ^/[A-Za-z0-9._~/-]*$ ]] || usage "OTHER_PATH is not a plain path"
case "$SCHEME" in
  https) ;;
  http) [[ "$SRC_HOST" == 127.0.0.1:* && "$EXP_HOST" == 127.0.0.1:* ]] \
          || usage "http is allowed only for 127.0.0.1 hosts" ;;
  *) usage "VERIFY_SCHEME must be https" ;;
esac

# head_of URL: print the response header block (one response, no redirects
# followed). Fails if curl fails or no HTTP status line arrives.
HDR=""
STATUS=""
LOC=""
fetch() {
  local url="$1" out
  out="$(curl --proto "=${SCHEME}" -sSI --max-time 20 "$url")" \
    || fail "request did not complete: ${url%%\?*}"
  HDR="$(printf '%s\n' "$out" | tr -d '\r')"
  STATUS="$(printf '%s\n' "$HDR" | awk 'NR==1 && $1 ~ /^HTTP\// {print $2; exit}')"
  [[ "$STATUS" =~ ^[0-9]{3}$ ]] || fail "no parsable HTTP status line from ${url%%\?*}"
  LOC="$(printf '%s\n' "$HDR" | awk 'tolower($1)=="location:"{print $2; exit}')"
}

check_probe() {
  local query="$1" want
  want="${SCHEME}://${EXP_HOST}/support/${query}"
  fetch "${SCHEME}://${SRC_HOST}/${query}"
  [ "$STATUS" = "$EXPECT_STATUS" ] || fail "status is $STATUS, expected $EXPECT_STATUS (query ${query})"
  [ -n "$LOC" ] || fail "no Location header (query ${query})"
  [ "$LOC" = "$want" ] || fail "Location is not exactly the expected URL (query ${query})"
  echo "ok: ${STATUS} ${query} -> exact Location"
}

check_probe "?probe=a%2Fb&probe=two"
check_probe "?q=x%20y&utm_source=mail&empty="

fetch "${SCHEME}://${SRC_HOST}${OTHER_PATH}"
case "$STATUS" in
  3??) case "$LOC" in
         *"${EXP_HOST}/support"*) fail "other path ${OTHER_PATH} redirects to the Support page" ;;
       esac ;;
esac
echo "ok: ${OTHER_PATH} answered ${STATUS}, not a redirect to Support"

fetch "${SCHEME}://${EXP_HOST}/support/"
[ "$STATUS" = "200" ] || fail "Support page answered $STATUS, expected 200"
echo "ok: Support page 200"

echo "all checks passed"
