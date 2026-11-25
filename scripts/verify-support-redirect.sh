#!/usr/bin/bash
PATH=/usr/bin:/bin
export PATH
readonly ENV_BIN=/usr/bin/env
readonly MKTEMP_BIN=/usr/bin/mktemp
readonly CHMOD_BIN=/usr/bin/chmod
readonly RM_BIN=/usr/bin/rm
readonly STAT_BIN=/usr/bin/stat
readonly READLINK_BIN=/usr/bin/readlink
readonly TR_BIN=/usr/bin/tr
readonly AWK_BIN=/usr/bin/awk
readonly WC_BIN=/usr/bin/wc
readonly CURL_PATH=/usr/bin:/bin

# SPDX-License-Identifier: Apache-2.0
# Read-only check of the old-support-host redirect (issue #888, runbook 3.5).
#
#   SRC_HOST=support-staging.example.com EXP_HOST=links-staging.example.com \
#     scripts/verify-support-redirect.sh
#
# Both hosts are required and their normalized authorities must differ; nothing
# is defaulted, so a copy made for production cannot
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

# Normalize an authority for validation and comparison: lowercase the host,
# strip one trailing dot, validate any port, and omit the scheme's default port.
normalize_authority() {
  local label="$1" h="${2,,}" port="" port_number
  if [[ "$h" =~ ^(.*):([0-9]+)$ ]]; then
    h="${BASH_REMATCH[1]}"
    port_number="${BASH_REMATCH[2]}"
    [[ "$port_number" =~ ^[1-9][0-9]{0,4}$ ]] \
      || usage "$label is not a plain host with a valid port"
    [ "$port_number" -le 65535 ] \
      || usage "$label is not a plain host with a valid port"
    if ! { [ "$SCHEME" = "https" ] && [ "$port_number" = "443" ]; } \
       && ! { [ "$SCHEME" = "http" ] && [ "$port_number" = "80" ]; }; then
      port=":${port_number}"
    fi
  fi
  h="${h%.}"
  printf '%s%s' "$h" "$port"
}

validate_host() {
  local label="$1" authority="$2" host="$2" part octet
  if [[ "$authority" =~ ^(.*):[0-9]+$ ]]; then
    host="${BASH_REMATCH[1]}"
  fi
  [ -n "$host" ] || usage "$label is not a valid DNS name or IPv4 address"
  [ "${#host}" -le 253 ] || usage "$label is not a valid DNS name or IPv4 address"
  [[ "$host" =~ ^[a-z0-9.-]+$ ]] \
    || usage "$label is not a valid DNS name or IPv4 address"
  if [[ "$host" =~ ^(0|[1-9][0-9]{0,2})(\.(0|[1-9][0-9]{0,2})){3}$ ]]; then
    IFS=. read -r -a octets <<< "$host"
    for octet in "${octets[@]}"; do
      [ "$octet" -le 255 ] 2>/dev/null \
        || usage "$label is not a valid DNS name or IPv4 address"
    done
    return
  fi
  if [[ "$host" =~ ^([0-9]+|0x[0-9a-f]+)(\.([0-9]+|0x[0-9a-f]+))*$ ]]; then
    usage "$label is not a valid DNS name or IPv4 address"
  fi
  IFS=. read -r -a labels <<< "${host}."
  for part in "${labels[@]}"; do
    [ -n "$part" ] && [ "${#part}" -le 63 ] \
      && [[ "$part" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] \
      || usage "$label is not a valid DNS name or IPv4 address"
  done
}

[ -n "$SRC_HOST" ] || usage "SRC_HOST is required"
[ -n "$EXP_HOST" ] || usage "EXP_HOST is required"
SRC_HOST="$(normalize_authority SRC_HOST "$SRC_HOST")"
EXP_HOST="$(normalize_authority EXP_HOST "$EXP_HOST")"
validate_host SRC_HOST "$SRC_HOST"
validate_host EXP_HOST "$EXP_HOST"
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

path_meta() { "$STAT_BIN" -Lc '%u %a' -- "$1" 2>/dev/null; }

validate_trusted_path() {
  local path="$1" meta owner mode
  while :; do
    meta="$(path_meta "$path")" \
      || usage "could not inspect trusted curl path"
    owner="${meta%% *}"
    mode="${meta#* }"
    [[ "$owner" =~ ^[0-9]+$ && "$mode" =~ ^[0-7]{3,4}$ ]] \
      || usage "trusted curl path has invalid ownership or permissions"
    [ "$owner" = "0" ] \
      || usage "trusted curl and its parent directories must be owned by root"
    [[ "${mode: -2:1}" != [2367] && "${mode: -1}" != [2367] ]] \
      || usage "trusted curl and its parent directories must not be group or world writable"
    [ "$path" = "/" ] && break
    path="${path%/*}"
    [ -n "$path" ] || path=/
  done
}

readonly CURL_PRIMARY=/usr/bin/curl
readonly CURL_FALLBACK=/bin/curl
if [ -f "$CURL_PRIMARY" ] && [ -x "$CURL_PRIMARY" ]; then
  CURL_CANDIDATE="$CURL_PRIMARY"
elif [ -f "$CURL_FALLBACK" ] && [ -x "$CURL_FALLBACK" ]; then
  CURL_CANDIDATE="$CURL_FALLBACK"
else
  usage "trusted curl is not available"
fi
CURL_BIN="$("$READLINK_BIN" -f -- "$CURL_CANDIDATE" 2>/dev/null)" \
  || usage "could not resolve trusted curl"
readonly CURL_BIN
[ -f "$CURL_BIN" ] && [ -x "$CURL_BIN" ] \
  || usage "trusted curl is not a regular executable file"
validate_trusted_path "$CURL_BIN"

CURL_HOME="$("$MKTEMP_BIN" -d)" || fail "could not create private curl home"
"$CHMOD_BIN" 700 "$CURL_HOME" || fail "could not protect private curl home"
cleanup() { "$RM_BIN" -rf -- "$CURL_HOME"; }
trap cleanup EXIT

# fetch URL: one GET, no redirects followed, body discarded, headers bounded.
# Sets STATUS, LOC_COUNT (number of Location header fields, name matched
# case-insensitively, CR removed) and LOC (the first value, or empty).
STATUS=""
LOC_COUNT=0
LOC=""
fetch() {
  local url="$1" header_file all_headers hdr
  header_file="$CURL_HOME/headers"
  : > "$header_file" || fail "could not prepare response header file"
  # Start curl with a fixed PATH, C locale and empty private HOME. The empty
  # environment excludes proxy, curl, certificate and TLS key-log settings.
  # Redirect following remains disabled: no -L is ever used. Limit the header
  # file to 128 512-byte blocks, or 64 KiB.
  if ! (
    ulimit -f 128
    "$ENV_BIN" -i PATH="$CURL_PATH" HOME="$CURL_HOME" LC_ALL=C \
      "$CURL_BIN" -q --noproxy '*' --proto "=${SCHEME}" \
        --connect-timeout 5 --max-time 20 -sS \
        -D "$header_file" -o /dev/null "$url"
  ); then
    fail "request did not complete: ${url%%\?*}"
  fi
  all_headers="$("$TR_BIN" -d '\r' < "$header_file")"
  printf '%s\n' "$all_headers" | "$AWK_BIN" '/^[ \t]/ {found=1} END {exit !found}' \
    && fail "obsolete folded header in response from ${url%%\?*}"
  # Curl can print more than one response header block. Only the last response
  # is authoritative, and it must begin with an HTTP status line.
  hdr="$(printf '%s\n' "$all_headers" |
    "$AWK_BIN" 'BEGIN {RS=""; ORS=""} {last=$0} END {print last}')"
  STATUS="$(printf '%s\n' "$hdr" | "$AWK_BIN" 'NR==1 && $1 ~ /^HTTP\// {print $2}')"
  [[ "$STATUS" =~ ^[0-9]{3}$ ]] || fail "no parsable HTTP status line from ${url%%\?*}"
  LOC_COUNT="$(printf '%s\n' "$hdr" | "$AWK_BIN" 'tolower($0) ~ /^location:/ {n++} END {print n+0}')"
  # No early exit in awk: it must read all input so printf never gets SIGPIPE
  # (which pipefail would turn into a random exit 141).
  LOC="$(printf '%s\n' "$hdr" | "$AWK_BIN" 'tolower($0) ~ /^location:/ && !seen {
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
