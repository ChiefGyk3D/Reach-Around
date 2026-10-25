#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Print one value from engine.lock after checking its shape.
# Usage: scripts/read-lock.sh ENGINE_IMAGE|ENGINE_REPO|ENGINE_REF [LOCK_FILE]
#        scripts/read-lock.sh --verify ENGINE_IMAGE|ENGINE_REPO|ENGINE_REF VALUE
#
# --verify only checks VALUE against the same shape and prints it; the
# preview workflow computes the image once from the trusted lock and hands
# it to the other scripts, which verify it again before use.
#
# LOCK_FILE defaults to engine.lock in this repository. A pull request preview
# passes the BASE branch's copy, so a pull request cannot choose the image
# its own preview runs in.
#
# The file is parsed, never sourced, so a bad edit cannot run code. The
# shape checks also make the printed value safe to append to $GITHUB_ENV.
set -euo pipefail

verify=""
if [ "${1:-}" = "--verify" ]; then
  verify=1
  shift
fi
key="${1:-}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
lock="${2:-$here/engine.lock}"

case "$key" in
  ENGINE_IMAGE) shape='^[a-z0-9./_-]+:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$' ;;
  ENGINE_REPO) shape='^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$' ;;
  ENGINE_REF) shape='^[0-9a-f]{40}$' ;;
  *)
    echo "usage: $0 ENGINE_IMAGE|ENGINE_REPO|ENGINE_REF" >&2
    exit 2
    ;;
esac

if [ -n "$verify" ]; then
  printf '%s' "${2:-}" | grep -Eq "$shape" || {
    echo "error: ${key} has the wrong shape" >&2
    exit 1
  }
  printf '%s\n' "$2"
  exit 0
fi

[ -f "$lock" ] && [ ! -L "$lock" ] || {
  echo "error: engine.lock not found" >&2
  exit 1
}

# Take the text after "KEY=" up to the first space (a trailing comment).
value="$(sed -n "s/^${key}=\([^ ]*\).*$/\1/p" "$lock")"
if [ "$(printf '%s\n' "$value" | grep -c .)" -ne 1 ]; then
  echo "error: engine.lock must set ${key} exactly once" >&2
  exit 1
fi
printf '%s' "$value" | grep -Eq "$shape" ||
  {
    echo "error: ${key} in engine.lock has the wrong shape" >&2
    exit 1
  }
printf '%s\n' "$value"
