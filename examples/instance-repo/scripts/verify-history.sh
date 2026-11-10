#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Check a social-stats history file with the pinned engine image:
# `reach-around counts history-verify`. The engine runs with no network, a
# read-only root file system, no capabilities and the caller's own user id, and
# sees only the folder that holds the file, read-only. Exit 0: every line
# parses and the hash chain is intact. Anything else: not safe to commit.
#
# Usage: scripts/verify-history.sh FILE
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
file="${1:?usage: verify-history.sh FILE}"

if [ -L "$file" ] || [ ! -f "$file" ]; then
  echo "verify-history: the history must be a regular file, not a link" >&2
  exit 1
fi
dir="$(cd "$(dirname "$file")" && pwd)"
name="$(basename "$file")"

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

userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

"$runtime" run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --pids-limit 64 --memory 512m \
  --tmpfs /tmp \
  --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
  -v "$dir":/history:ro \
  "$image" counts history-verify --file "/history/$name"
