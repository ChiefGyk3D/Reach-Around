#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Post-deploy smoke test for a Reach Around site and its Worker.
#
# Usage: scripts/smoke.sh [--timeout SECONDS] URL
#   URL  the site root, e.g. https://links.example.test (http or https only;
#        no credentials, query or fragment)
#
# It only reads the page and sends requests the Worker answers with an empty
# 204 without storing anything (unknown ids, no Origin header). It never needs a
# credential and never touches the database. Every check runs even after a
# failure; exit 0 when all pass, 1 when any fail, 2 on bad usage.
#
# Checks: the page answers 200 as HTML; it carries a CSP and nosniff; no
# response sets a cookie; the write-only routes under /e/ answer an empty 204
# (a click POST, a view POST, a view GET); and they never echo a request header
# back (the probe header carries a documentation-range address).
set -uo pipefail

usage() {
    echo "usage: ${0##*/} [--timeout SECONDS] URL" >&2
    exit 2
}

timeout=15
while [ $# -gt 0 ]; do
    case "$1" in
    --timeout)
        [ $# -ge 2 ] || usage
        timeout="$2"
        shift 2
        ;;
    --)
        shift
        break
        ;;
    -*) usage ;;
    *) break ;;
    esac
done
[ $# -eq 1 ] || usage
case "$timeout" in '' | *[!0-9]*) usage ;; esac
# curl reads --max-time 0 as "no limit"; every request must be bounded.
[ "$((10#$timeout))" -gt 0 ] || usage
base="$1"
case "$base" in
http://* | https://*) ;;
*) usage ;;
esac
# No credentials (an @ anywhere), backslash, query or fragment; a host is required.
case "$base" in *[[:space:]@\\?#]*) usage ;; esac
host_part="${base#*://}"
case "$host_part" in '' | /* | :*) usage ;; esac
base="${base%/}"

probe="198.51.100.77"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0

pass() { echo "ok: $*"; }
fail() {
    echo "FAIL: $*"
    fails=$((fails + 1))
}

# fetch NAME METHOD PATH: leaves $work/NAME.head, .body and sets status_NAME.
# A transport error (no answer) is status 000.
fetch() {
    local name="$1" method="$2" path="$3" status
    status="$(curl -sS --globoff -o "$work/$name.body" -D "$work/$name.head" \
        --max-time "$timeout" -X "$method" \
        -H "X-Smoke-Probe: $probe" -w '%{http_code}' \
        "$base$path" 2>"$work/$name.err")" || status="000"
    printf '%s' "$status" >"$work/$name.status"
    [ -e "$work/$name.head" ] || : >"$work/$name.head"
}

status_of() { cat "$work/$1.status"; }
header_of() { grep -i "^$2:" "$work/$1.head" | tail -n 1 | tr -d '\r' || true; }

fetch page GET /
fetch click POST /e/c/smoke-unknown-link
fetch view POST /e/v/smoke-unknown-page
fetch viewget GET /e/v/smoke-unknown-page

# 1. The page.
if [ "$(status_of page)" = "200" ]; then
    pass "page answers 200"
else
    fail "page / answered $(status_of page), expected 200"
fi
if header_of page content-type | grep -qi 'text/html'; then
    pass "page content-type is text/html"
else
    fail "page content-type is not text/html"
fi
if [ -n "$(header_of page content-security-policy)" ]; then
    pass "page has a content-security-policy"
else
    fail "page has no content-security-policy header"
fi
if header_of page x-content-type-options | grep -qi 'nosniff'; then
    pass "page has x-content-type-options nosniff"
else
    fail "page has no x-content-type-options nosniff header"
fi

# 2. The write-only routes: an empty 204, nothing else.
for name in click view viewget; do
    if [ "$(status_of "$name")" = "204" ] && [ ! -s "$work/$name.body" ]; then
        pass "$name route answers an empty 204"
    else
        fail "$name route answered $(status_of "$name") with $(wc -c <"$work/$name.body") body bytes, expected an empty 204"
    fi
done

# 3. No cookie anywhere.
for name in page click view viewget; do
    if [ -n "$(header_of "$name" set-cookie)" ]; then
        fail "$name response sets a cookie (set-cookie header present)"
    fi
done
if ! grep -qil '^set-cookie:' "$work"/*.head 2>/dev/null; then
    pass "no response sets a cookie"
fi

# 4. No echo of a request value, in any header or body.
echoed=0
for name in page click view viewget; do
    if grep -qF "$probe" "$work/$name.head" "$work/$name.body"; then
        echoed=1
        fail "$name response echoes a request header value (echo of the probe)"
    fi
done
[ "$echoed" -eq 0 ] && pass "no response echoes a request value"

if [ "$fails" -eq 0 ]; then
    echo "smoke passed"
    exit 0
fi
echo "smoke failed: $fails check(s)"
exit 1
