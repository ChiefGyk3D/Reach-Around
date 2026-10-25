#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Check site.yaml with the pinned engine image and say what is wrong in plain
# language: which tab, which block, which field, and how to fix it. No config
# value is printed, only those names.
#
# Usage: scripts/check-config.sh [--format plain|json] [--against FILE] [--out FILE]
#   --format FMT    plain sentences (default) or json
#   --against FILE  a base site.yaml (a pull request's base) to list the tabs
#                   and blocks that changed against; names only, never values
#   --out FILE      also write the result to FILE
#
# Exit status: 0 the config is valid; 2 it is not; 3 the check itself could
# not run (no Docker or Podman, or an engine image too old to have `check`).
#
# Needs an engine image with the `check` command (the engine release after
# 0.1.0; see README.md). The engine runs with no network, a read-only root
# filesystem, no capabilities and the caller's own user id, and the repository
# is mounted read-only.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
format="plain"
against=""
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --format)
      format="${2:?--format needs plain or json}"
      shift 2
      ;;
    --against)
      against="${2:?--against needs a file}"
      shift 2
      ;;
    --out)
      out="${2:?--out needs a file path}"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 3
      ;;
  esac
done
case "$format" in
  plain | json) ;;
  *)
    echo "error: --format must be plain or json" >&2
    exit 3
    ;;
esac

runtime=""
for candidate in docker podman; do
  if command -v "$candidate" >/dev/null 2>&1; then
    runtime="$candidate"
    break
  fi
done
[ -n "$runtime" ] || {
  echo "error: Docker or Podman is required" >&2
  exit 3
}

image="$("$here/scripts/read-lock.sh" ENGINE_IMAGE)"

mounts=(-v "$here":/config:ro)
args=(check --config /config/site.yaml --format "$format")
if [ -n "$against" ]; then
  [ -f "$against" ] || {
    echo "error: --against file not found" >&2
    exit 3
  }
  base_dir="$(cd "$(dirname "$against")" && pwd)"
  mounts+=(-v "$base_dir":/base:ro)
  args+=(--against "/base/$(basename "$against")")
fi

# Rootless Podman maps the caller to a different id inside the container;
# keep-id preserves it so the read-only mount stays readable.
userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

stdout_file="$(mktemp)"
stderr_file="$(mktemp)"
trap 'rm -f "$stdout_file" "$stderr_file"' EXIT

set +e
"$runtime" run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp \
  --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
  "${mounts[@]}" "$image" "${args[@]}" >"$stdout_file" 2>"$stderr_file"
status=$?
set -e

# A real answer never writes to stderr. Anything there means the engine did
# not run the check (a bad option, or an image without `check`).
if [ -s "$stderr_file" ] || { [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; }; then
  cat "$stderr_file" >&2
  if grep -q "invalid choice" "$stderr_file"; then
    echo "check-config: the pinned engine image is too old: it has no check" >&2
    echo "check-config: command. This template needs the engine release after" >&2
    echo "check-config: 0.1.0; update engine.lock to it (see README.md)." >&2
  else
    echo "check-config: the engine could not run the check (status $status)" >&2
  fi
  exit 3
fi

cat "$stdout_file"
if [ -n "$out" ]; then
  mkdir -p "$(dirname "$out")"
  cat "$stdout_file" >"$out"
fi
exit "$status"
