#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Say whether a new history file only appends to the old one, or is a valid
# tombstone-only rewrite: `reach-around counts history-compare` in the pinned
# engine image (no network, read-only root, no capabilities, the caller's own
# user id, bounded memory, processes and time). The inputs are copied into a
# private folder and mounted read-only.
#
# Usage: scripts/verify-history-rewrite.sh OLD NEW [--allow-tombstones]
#          [--manifest FILE --pr-day YYYY-MM-DD]
#          [--numbers-old FILE] [--numbers-new FILE]
#
# Exit 0, printing "mode=append" or "mode=rewrite" (also written to
# $GITHUB_OUTPUT when it is set): the change is acceptable. Anything else,
# including a rewrite the caller did not allow: not acceptable. The engine's
# status 0 means append-only and 10 means a valid rewrite; every other status
# (1, 2, 124 timeout, 125 to 127 container runtime, 137 and 143 signals) is a
# refusal and is never treated as a pass. The wrapper never prints a value.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
old="${1:?usage: verify-history-rewrite.sh OLD NEW [options]}"
new="${2:?usage: verify-history-rewrite.sh OLD NEW [options]}"
shift 2

allow=()
manifest=""
pr_day=""
numbers_old=""
numbers_new=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --allow-tombstones)
      allow=(--allow-tombstones)
      shift
      ;;
    --manifest)
      manifest="${2:?--manifest needs a file}"
      shift 2
      ;;
    --pr-day)
      pr_day="${2:?--pr-day needs a date}"
      shift 2
      ;;
    --numbers-old)
      numbers_old="${2:?--numbers-old needs a file}"
      shift 2
      ;;
    --numbers-new)
      numbers_new="${2:?--numbers-new needs a file}"
      shift 2
      ;;
    *)
      echo "verify-history-rewrite: unknown option" >&2
      exit 2
      ;;
  esac
done

regular() {
  if [ -L "$1" ] || [ ! -f "$1" ]; then
    echo "verify-history-rewrite: an input is not a regular file" >&2
    exit 1
  fi
}
regular "$old"
regular "$new"
[ -z "$manifest" ] || regular "$manifest"
[ -z "$numbers_old" ] || regular "$numbers_old"
[ -z "$numbers_new" ] || regular "$numbers_new"

runtime=""
for candidate in docker podman; do
  if command -v "$candidate" >/dev/null 2>&1; then
    runtime="$candidate"
    break
  fi
done
[ -n "$runtime" ] || {
  echo "error: Docker or Podman is required" >&2
  exit 1
}

image="$("$here/scripts/read-lock.sh" ENGINE_IMAGE)"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
chmod 755 "$stage"
cp -- "$old" "$stage/old"
cp -- "$new" "$stage/new"
args=(--old /in/old --new /in/new "${allow[@]+"${allow[@]}"}")
if [ -n "$manifest" ]; then
  cp -- "$manifest" "$stage/manifest"
  args+=(--manifest /in/manifest --manifest-name "$(basename -- "$manifest")")
fi
if [ -n "$pr_day" ]; then
  args+=(--pr-day "$pr_day")
fi
if [ -n "$numbers_old" ]; then
  cp -- "$numbers_old" "$stage/numbers-old"
  args+=(--numbers-old /in/numbers-old)
fi
if [ -n "$numbers_new" ]; then
  cp -- "$numbers_new" "$stage/numbers-new"
  args+=(--numbers-new /in/numbers-new)
fi
chmod 644 "$stage"/*

userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

# The status is read on purpose: no `!` and no bare command under `set -e`.
rc=0
timeout 120 "$runtime" run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --pids-limit 64 --memory 512m \
  --tmpfs /tmp \
  --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
  -v "$stage":/in:ro \
  "$image" counts history-compare "${args[@]}" || rc=$?

case "$rc" in
  0) mode=append ;;
  10) mode=rewrite ;;
  *)
    echo "history compare failed (status $rc)" >&2
    exit 1
    ;;
esac
echo "mode=$mode"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "mode=$mode" >>"$GITHUB_OUTPUT"
fi
