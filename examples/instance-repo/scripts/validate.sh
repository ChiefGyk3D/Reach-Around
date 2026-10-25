#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Build the site with the pinned engine image. The build is the validator: a
# bad config stops it with the file and the key path.
#
# Usage: scripts/validate.sh [--out DIR] [--manifest FILE] [--summary FILE]
#   --out DIR       keep the built site in DIR (default: a temporary directory
#                   that is removed afterwards)
#   --manifest FILE keep the link manifest at FILE (default: a temporary file
#                   that is removed afterwards). FILE must be OUTSIDE --out:
#                   the manifest is never part of the published site. Its
#                   directory is mounted writable at /manifest, separate from
#                   the site output.
#   --summary FILE  append a plain-language result (Markdown) to FILE; the
#                   pull-request workflow passes the job summary file here
#
# Needs an engine image that supports --manifest (the engine release after
# 0.1.0; see README.md). Needs Docker or Podman. The engine runs with no network, a read-only root
# filesystem, no capabilities and the caller's own user id. Nothing in the
# build can see a secret: none is passed in.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out=""
manifest=""
summary=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out)
      out="${2:?--out needs a directory}"
      shift 2
      ;;
    --manifest)
      manifest="${2:?--manifest needs a file path}"
      shift 2
      ;;
    --summary)
      summary="${2:?--summary needs a file}"
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
if [ -z "$out" ]; then
  out="$(mktemp -d)"
  cleanup="$out"
fi
mkdir -p "$out"
out="$(cd "$out" && pwd)"

# The manifest goes to its own directory, never into the published site.
manifest_cleanup=""
if [ -z "$manifest" ]; then
  manifest_cleanup="$(mktemp -d)"
  manifest="$manifest_cleanup/manifest.json"
fi
manifest_name="$(basename "$manifest")"
mkdir -p "$(dirname "$manifest")"
manifest_dir="$(cd "$(dirname "$manifest")" && pwd)"
case "$manifest_dir/" in
  "$out"/*)
    echo "error: --manifest must be outside --out ($out)" >&2
    exit 2
    ;;
esac
case "$out/" in
  "$manifest_dir"/*)
    echo "error: --out must not sit inside the manifest directory" >&2
    exit 2
    ;;
esac

log="$(mktemp)"
trap 'rm -f "$log"; if [ -n "$cleanup" ]; then rm -rf "$cleanup"; fi; if [ -n "$manifest_cleanup" ]; then rm -rf "$manifest_cleanup"; fi' EXIT

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
  -v "$here":/config:ro -v "$out":/out -v "$manifest_dir":/manifest \
  "$image" build --config /config/site.yaml --out /out \
  --manifest "/manifest/$manifest_name" \
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
if grep -q 'unrecognized arguments: .*--manifest' "$log"; then
  echo "validate: the pinned engine image is too old: it has no --manifest" >&2
  echo "validate: option. This template needs the engine release after 0.1.0;" >&2
  echo "validate: update engine.lock to it (see README.md)." >&2
fi
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
