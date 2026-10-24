#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the site with the pinned engine image. The build is the validator: a
# bad config stops it with the file and the key path.
#
# Usage: scripts/validate.sh [--out DIR] [--summary FILE]
#   --out DIR       keep the built site in DIR (default: a temporary directory
#                   that is removed afterwards)
#   --summary FILE  append a plain-language result (Markdown) to FILE; the
#                   pull-request workflow passes the job summary file here
#
# Needs Docker or Podman. The engine runs with no network, a read-only root
# filesystem, no capabilities and the caller's own user id. Nothing in the
# build can see a secret: none is passed in.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out=""
summary=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out) out="${2:?--out needs a directory}"; shift 2 ;;
    --summary) summary="${2:?--summary needs a file}"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

runtime=""
for candidate in docker podman; do
  if command -v "$candidate" >/dev/null 2>&1; then runtime="$candidate"; break; fi
done
[ -n "$runtime" ] || { echo "error: Docker or Podman is required" >&2; exit 1; }

image="$("$here/scripts/read-lock.sh" ENGINE_IMAGE)"

cleanup=""
if [ -z "$out" ]; then
  out="$(mktemp -d)"
  cleanup="$out"
fi
mkdir -p "$out"
log="$(mktemp)"
trap 'rm -f "$log"; if [ -n "$cleanup" ]; then rm -rf "$cleanup"; fi' EXIT

# Rootless Podman maps the caller to a different id inside the container, so
# the engine could not write to the output folder. keep-id preserves the id.
userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

overrides=()
if [ -d "$here/overrides" ]; then overrides=(--overrides /config/overrides); fi

set +e
"$runtime" run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp \
  --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
  -v "$here":/config:ro -v "$out":/out \
  "$image" build --config /config/site.yaml --out /out \
  ${overrides[@]+"${overrides[@]}"} >"$log" 2>&1
status=$?
set -e

# Write a Markdown note for a person who does not read build logs.
note() {
  if [ -n "$summary" ]; then printf '%s\n' "$@" >>"$summary"; fi
}

if [ "$status" -eq 0 ]; then
  pages="$(find "$out" -name '*.html' | wc -l | tr -d ' ')"
  echo "validate: the site builds ($pages pages)."
  note "## The site builds" "" "The config is valid. The site has $pages pages."
  exit 0
fi

echo "validate: the build failed:" >&2
cat "$log" >&2
if [ -n "$summary" ]; then
  # The log can quote text from the change, so keep it inside a code block
  # that the text cannot close.
  safe="$(sed 's/```/'"'''"'/g' "$log")"
  {
    echo "## The site did not build"
    echo
    echo "Nothing was deployed. Each line below says which file and which"
    echo "setting is wrong. Fix it (the editor or the \`add-link\` commands in"
    echo "docs/EDITING.md), commit again, and this check runs again."
    echo
    echo '```text'
    echo "$safe"
    echo '```'
    echo
    echo "How to read a line: \`site.yaml: theme.mode: Input should be 'auto',"
    echo "'light' or 'dark'\` means: in site.yaml, the setting theme, then mode,"
    echo "has a value that is not allowed, and the allowed values follow."
  } >>"$summary"
fi
exit "$status"
