#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Check site.yaml with the pinned engine image and say what is wrong in plain
# language: which tab, which block, which field, and how to fix it. No config
# value is printed. What is printed: a tab title and a block id (reduced to
# plain characters; they are names that appear on the published site), and a
# setting name only when the config schema defines it.
#
# Usage: scripts/check-config.sh [--lock FILE] [--format plain|json]
#                                [--against FILE] [--out FILE]
#   --lock FILE     read the engine pin from FILE instead of engine.lock. A pull
#                   request preview passes the BASE branch's engine.lock, so the
#                   pull request cannot choose the image its own preview runs in.
#   --format FMT    plain sentences (default) or json
#   --against FILE  a base site.yaml (a pull request's base) to list the tabs
#                   and blocks that changed against; names only, never values
#   --out FILE      also write the result to FILE. FILE must not exist yet, and
#                   neither it nor its folder may be a symbolic link.
#
# Exit status: 0 the config is valid; 2 it is not; 3 the check itself could
# not run (no Docker or Podman, an engine image too old to have `check`, a bad
# option or output path).
#
# Needs an engine image with the `check` command (the engine release after
# 0.1.0; see README.md). The engine runs with no network, a read-only root
# filesystem, no capabilities, no new privileges, a process and a memory limit,
# and the caller's own user id. Only site.yaml, media/ and the --against file
# are mounted, each read-only and each by itself; the rest of the checkout is
# not visible to the engine.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
format="plain"
against=""
out=""
lock=""
while [ $# -gt 0 ]; do
  case "$1" in
    --lock)
      lock="${2:?--lock needs a file}"
      shift 2
      ;;
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

# Refuse an output path that already exists, is a symlink, or sits under one,
# before anything runs.
if [ -n "$out" ]; then
  out_parent="$(dirname "$out")"
  if [ -e "$out" ] || [ -L "$out" ]; then
    echo "error: --out must not exist yet" >&2
    exit 3
  fi
  [ -d "$out_parent" ] || {
    echo "error: the folder for --out does not exist" >&2
    exit 3
  }
  if [ "$(cd "$out_parent" && pwd -P)" != "$(cd "$out_parent" && pwd -L)" ]; then
    echo "error: --out must not be in or under a symbolic link" >&2
    exit 3
  fi
fi

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

image="$("$here/scripts/read-lock.sh" ENGINE_IMAGE ${lock:+"$lock"})"

# Mount only what the check reads, one path at a time. A symlink is refused:
# the runtime would resolve it on the host and mount what it points at.
for item in site.yaml media; do
  if [ -L "$here/$item" ]; then
    echo "error: $item must not be a symbolic link" >&2
    exit 3
  fi
done
[ -f "$here/site.yaml" ] || {
  echo "error: site.yaml not found" >&2
  exit 3
}
mounts=(-v "$here/site.yaml":/config/site.yaml:ro)
if [ -d "$here/media" ]; then mounts+=(-v "$here/media":/config/media:ro); fi

args=(check --config /config/site.yaml --format "$format")
if [ -n "$against" ]; then
  if [ ! -f "$against" ] || [ -L "$against" ]; then
    echo "error: --against must be a regular file" >&2
    exit 3
  fi
  against_abs="$(cd "$(dirname "$against")" && pwd -P)/$(basename "$against")"
  mounts+=(-v "$against_abs":/base/site.yaml:ro)
  args+=(--against /base/site.yaml)
fi

# Rootless Podman maps the caller to a different id inside the container;
# keep-id preserves it so the read-only mounts stay readable.
userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stdout_file="$work/stdout"
stderr_file="$work/stderr"

set +e
"$runtime" run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --pids-limit 128 --memory 512m \
  --tmpfs /tmp \
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
  # noclobber makes the redirect an exclusive create (O_EXCL): it fails if
  # the path appeared since the check above, even as a symlink.
  (
    set -o noclobber
    cat "$stdout_file" >"$out"
  ) || {
    echo "error: could not create --out (it must not exist yet)" >&2
    exit 3
  }
fi
exit "$status"
