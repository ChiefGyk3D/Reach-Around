#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Read-only check of the old-support-host redirect (issue #888, runbook 3.5).
#
#   SRC_HOST=support-staging.example.com EXP_HOST=links-staging.example.com \
#     scripts/verify-support-redirect.sh
#
# Both hosts are required and must differ (compared case-insensitively, without
# a trailing dot); nothing is defaulted, so a copy made for production cannot
# silently call staging. Optional:
#   EXPECT_STATUS  302 (default) or 301 (after the owner promotes the rule)
#   OTHER_PATH     a path the old host serves that must NOT redirect anywhere
#                  (default /other-path)
#   OTHER_EXPECT_STATUS  the non-redirect status OTHER_PATH really serves:
#                  200 (default), or any 2xx, 4xx or 5xx code, for example 404
#   VERIFY_SCHEME  https (default). http is accepted only for a 127.0.0.1
#                  host, so the tests can use a local server.
#
# Checks, each of which must run and pass (no -L is ever used). Every request
# is a GET whose body is discarded, never HEAD: browsers navigate with GET, and
# a rule or origin can answer HEAD differently from GET.
#   1. two probe requests to the old host return EXPECT_STATUS and exactly one
#      Location header, equal, character for character, to
#      https://$EXP_HOST/support/ plus the exact query that was sent (proves the
#      query string is preserved, with a repeated parameter and encoded
#      characters that are not in the rule; two Location headers fail);
#   2. OTHER_PATH is not redirected at all: it answers OTHER_EXPECT_STATUS (the
#      non-redirect status the page really serves) and sends no Location header;
#   3. the Support page (/support/) answers 200.
# Exit 0 only after all of them ran. Exit 1 on any failed check, 2 on a usage
# error. Every curl failure and every unparsable response is a failure.
set -euo pipefail

fail() { echo "FAIL: $*" >&2; exit 1; }
usage() { echo "usage error: $*" >&2; exit 2; }

SRC_HOST="${SRC_HOST:-}"
EXP_HOST="${EXP_HOST:-}"
EXPECT_STATUS="${EXPECT_STATUS:-302}"
OTHER_PATH="${OTHER_PATH:-/other-path}"
OTHER_EXPECT_STATUS="${OTHER_EXPECT_STATUS:-200}"
SCHEME="${VERIFY_SCHEME:-https}"

# Hosts are DNS names, so compare them case-insensitively and without a
# trailing dot: lowercase, and strip one dot before an optional :port.
normalize_host() {
  local h="${1,,}" port=""
  if [[ "$h" =~ ^(.*):([0-9]+)$ ]]; then
    h="${BASH_REMATCH[1]}"
    port=":${BASH_REMATCH[2]}"
  fi
  h="${h%.}"
  printf '%s%s' "$h" "$port"
}

host_re='^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$'
[ -n "$SRC_HOST" ] || usage "SRC_HOST is required"
[ -n "$EXP_HOST" ] || usage "EXP_HOST is required"
SRC_HOST="$(normalize_host "$SRC_HOST")"
EXP_HOST="$(normalize_host "$EXP_HOST")"
[[ "$SRC_HOST" =~ $host_re ]] || usage "SRC_HOST is not a plain host"
[[ "$EXP_HOST" =~ $host_re ]] || usage "EXP_HOST is not a plain host"
[ "$SRC_HOST" != "$EXP_HOST" ] || usage "SRC_HOST and EXP_HOST must differ"
case "$EXPECT_STATUS" in 301 | 302) ;; *) usage "EXPECT_STATUS must be 301 or 302" ;; esac
[[ "$OTHER_EXPECT_STATUS" =~ ^[245][0-9]{2}$ ]] || usage "OTHER_EXPECT_STATUS must be a 2xx, 4xx or 5xx code"
[[ "$OTHER_PATH" =~ ^/[A-Za-z0-9._~/-]*$ ]] || usage "OTHER_PATH is not a plain path"
case "$SCHEME" in
  https) ;;
  http) [[ "$SRC_HOST" == 127.0.0.1:* && "$EXP_HOST" == 127.0.0.1:* ]] \
          || usage "http is allowed only for 127.0.0.1 hosts" ;;
  *) usage "VERIFY_SCHEME must be https" ;;
esac

# fetch URL: one GET, no redirects followed, body discarded, headers kept.
# Sets STATUS, LOC_COUNT (number of Location header fields, name matched
# case-insensitively, CR removed) and LOC (the first value, or empty).
STATUS=""
LOC_COUNT=0
LOC=""
fetch() {
  local url="$1" out hdr
  out="$(curl -q --proto "=${SCHEME}" --connect-timeout 5 --max-time 20 \
           -sS -D - -o /dev/null "$url")" \
    || fail "request did not complete: ${url%%\?*}"
  hdr="$(printf '%s\n' "$out" | tr -d '\r')"
  STATUS="$(printf '%s\n' "$hdr" | awk 'NR==1 && $1 ~ /^HTTP\// {print $2}')"
  [[ "$STATUS" =~ ^[0-9]{3}$ ]] || fail "no parsable HTTP status line from ${url%%\?*}"
  LOC_COUNT="$(printf '%s\n' "$hdr" | awk 'tolower($0) ~ /^location:/ {n++} END {print n+0}')"
  # No early exit in awk: it must read all input so printf never gets SIGPIPE
  # (which pipefail would turn into a random exit 141).
  LOC="$(printf '%s\n' "$hdr" | awk 'tolower($0) ~ /^location:/ && !seen {
           seen = 1; sub(/^[^:]*:[ \t]*/, ""); sub(/[ \t]+$/, ""); print}')"
}

check_probe() {
  local query="$1" want
  want="${SCHEME}://${EXP_HOST}/support/${query}"
  fetch "${SCHEME}://${SRC_HOST}/${query}"
  [ "$STATUS" = "$EXPECT_STATUS" ] || fail "status is $STATUS, expected $EXPECT_STATUS (query ${query})"
  [ "$LOC_COUNT" -ge 1 ] || fail "no Location header (query ${query})"
  [ "$LOC_COUNT" -eq 1 ] || fail "$LOC_COUNT Location headers, exactly one is required (query ${query})"
  [ "$LOC" = "$want" ] || fail "Location is not exactly the expected URL (query ${query})"
  echo "ok: ${STATUS} ${query} -> exact Location"
}

check_probe "?probe=a%2Fb&probe=two"
check_probe "?q=x%20y&utm_source=mail&empty="

fetch "${SCHEME}://${SRC_HOST}${OTHER_PATH}"
[ "$STATUS" = "$OTHER_EXPECT_STATUS" ] || fail "other path ${OTHER_PATH} answered $STATUS, expected $OTHER_EXPECT_STATUS (it must not redirect)"
[ "$LOC_COUNT" -eq 0 ] || fail "other path ${OTHER_PATH} sends a Location header"
echo "ok: ${OTHER_PATH} answered ${STATUS} with no redirect"

fetch "${SCHEME}://${EXP_HOST}/support/"
[ "$STATUS" = "200" ] || fail "Support page answered $STATUS, expected 200"
echo "ok: Support page 200"

echo "all checks passed"
