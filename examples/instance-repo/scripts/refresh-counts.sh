#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Refresh the follower counts with the pinned engine image and put the new
# numbers.json in the repository folder. Used by the scheduled
# refresh-counts workflow; you can run it by hand too.
#
# The new numbers.json is checked by scripts/validate-numbers.py before it is
# copied into the repository folder (and the workflow's publish job checks it
# again after it crosses to the job that may write).
#
# Usage: scripts/refresh-counts.sh [--workdir DIR]
#   --workdir DIR  scratch folder for the engine's result files (default: a
#                  temporary folder that is removed afterwards)
#
# The engine image is the one engine.lock names by digest (read-lock.sh checks
# its shape). The engine runs with a read-only root file system, no
# capabilities and the caller's own user id. Unlike the build, it needs the
# network to ask each platform for a count, so it is NOT started with
# --network none. Only site.yaml, media/ and overrides/ are mounted read-only,
# plus the scratch folder it writes into.
#
# Keys come from the environment only. Of the three names below, only those
# that are set and not empty are passed to the engine (by name; the value is
# never on a command line or printed). A source whose key is missing is
# skipped by the engine, not failed. The names must match what site.yaml
# names under numbers.sources (api_key_env, client_id_env, client_secret_env).
#
# The engine keeps a suspect value out (a zero, or a fall of more than 90
# percent from 1000 or more) and records it as a failed source. This script
# never overrides that. Exit codes: 0 all sources fine or skipped; 3 from the
# engine (some source failed, the good ones are still written) is also exit 0
# here, with "source_failed=true" appended to $GITHUB_OUTPUT, so the workflow
# can commit the good numbers first and fail afterwards. Any other engine exit
# code (a config problem, a write problem) is returned as it is.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --workdir)
      workdir="${2:?--workdir needs a folder}"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

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

cleanup=""
if [ -z "$workdir" ]; then
  workdir="$(mktemp -d)"
  cleanup="$workdir"
fi
mkdir -p "$workdir"
workdir="$(cd "$workdir" && pwd)"
chmod 700 "$workdir"
trap 'if [ -n "$cleanup" ]; then rm -rf "$cleanup"; fi' EXIT

userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

mounts=()
for item in site.yaml media overrides; do
  path="$here/$item"
  if [ -L "$path" ]; then
    echo "error: $item must not be a symbolic link" >&2
    exit 2
  fi
  if [ -e "$path" ]; then mounts+=(-v "$path":"/config/$item":ro); fi
done
[ -f "$here/site.yaml" ] || {
  echo "error: site.yaml not found" >&2
  exit 2
}

# Keep numbers entered by hand (platforms without a source): the engine reads
# the old file and only replaces what a source returns.
numbers="$here/numbers.json"
if [ -L "$numbers" ]; then
  echo "error: numbers.json must not be a symbolic link" >&2
  exit 2
fi
if [ -f "$numbers" ]; then cp "$numbers" "$workdir/numbers.json"; fi

# "-e NAME" with no value copies the variable from this process; nothing is
# written to the command line or the log.
keys=()
for name in YOUTUBE_API_KEY TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET; do
  if [ -n "${!name:-}" ]; then keys+=(-e "$name"); fi
done

set +e
"$runtime" run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --pids-limit 256 --memory 1g \
  --tmpfs /tmp \
  --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
  ${keys[@]+"${keys[@]}"} \
  "${mounts[@]}" -v "$workdir":/data \
  "$image" counts refresh --config /config/site.yaml --out /data/numbers.json
status=$?
set -e

case "$status" in
  0) failed=false ;;
  3) failed=true ;;
  *)
    echo "refresh-counts: the engine stopped with exit code $status; nothing was changed." >&2
    exit "$status"
    ;;
esac

# The engine's file is data from a container that talked to the network. It
# reaches the repository only through the strict validator, which writes a
# fresh copy to a temporary file and renames it into place.
if [ -f "$workdir/numbers.json" ] && [ ! -L "$workdir/numbers.json" ]; then
  python3 -I "$here/scripts/validate-numbers.py" \
    --in "$workdir/numbers.json" --out "$numbers" || {
    echo "refresh-counts: the engine's numbers.json failed the check; nothing was changed." >&2
    exit 1
  }
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "source_failed=$failed" >>"$GITHUB_OUTPUT"
fi
if [ "$failed" = true ]; then
  echo "refresh-counts: at least one source failed; the numbers that did refresh were kept." >&2
fi
exit 0
