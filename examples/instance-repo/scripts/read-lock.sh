#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Print one value from engine.lock after checking its shape.
# Usage: scripts/read-lock.sh ENGINE_IMAGE|ENGINE_REPO|ENGINE_REF [LOCK_FILE]
#        scripts/read-lock.sh --verify ENGINE_IMAGE|ENGINE_REPO|ENGINE_REF VALUE
#        scripts/read-lock.sh --release [LOCK_FILE]
#
# --release checks that engine.release.json (beside the lock) and the lock name
# one release: see the block below.
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

# --release [LOCK_FILE]: prove the three pins describe ONE release (issue #768,
# F-11). engine.release.json, next to the lock, lists the image digest, the
# repository, the commit, the tag and the D1 schema (highest migration number)
# of that release. Every field must match the lock, and the image tag (a label
# in ENGINE_IMAGE) and the "# vX.Y.Z" comment on ENGINE_REF must name the manifest's
# tag, so an image from release A with a commit from release B fails here.
# The manifest is reviewed in the same pull request as the lock; it is NOT
# signed, so it binds the pins to each other, not to a publisher. The deploy
# script also checks the commit against the checkout (deploy-local.sh).
if [ "${1:-}" = "--release" ]; then
  self="${BASH_SOURCE[0]}"
  lock_arg="${2:-}"
  here_dir="$(cd "$(dirname "$self")/.." && pwd)"
  lock_file="${lock_arg:-$here_dir/engine.lock}"
  manifest="$(dirname "$lock_file")/engine.release.json"
  fail_release() {
    echo "error: engine.release.json does not match engine.lock: $*" >&2
    exit 1
  }
  command -v jq >/dev/null || fail_release "jq is required"
  [ -f "$manifest" ] && [ ! -L "$manifest" ] || fail_release "engine.release.json not found beside the lock"
  m_image="$(jq -er '.image | strings' "$manifest")" || fail_release "image missing"
  m_repo="$(jq -er '.repo | strings' "$manifest")" || fail_release "repo missing"
  m_commit="$(jq -er '.commit | strings' "$manifest")" || fail_release "commit missing"
  m_tag="$(jq -er '.tag | strings' "$manifest")" || fail_release "tag missing"
  m_schema="$(jq -er '.d1_schema | strings' "$manifest")" || fail_release "d1_schema missing"
  [ "$(jq -er '.manifest_version' "$manifest")" = "1" ] || fail_release "manifest_version must be 1"
  printf '%s' "$m_tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' || fail_release "tag has the wrong shape"
  printf '%s' "$m_schema" | grep -Eq '^[0-9]{4}$' || fail_release "d1_schema has the wrong shape"
  l_image="$("$self" ENGINE_IMAGE "$lock_file")"
  l_repo="$("$self" ENGINE_REPO "$lock_file")"
  l_ref="$("$self" ENGINE_REF "$lock_file")"
  [ "$m_image" = "$l_image" ] || fail_release "image differs"
  [ "$m_repo" = "$l_repo" ] || fail_release "repo differs"
  [ "$m_commit" = "$l_ref" ] || fail_release "commit differs"
  l_tag="$(sed -n 's/^ENGINE_REF=[0-9a-f]\{40\} *# *\(v[0-9.]*\) *$/\1/p' "$lock_file")"
  [ "$l_tag" = "$m_tag" ] || fail_release "the tag comment on ENGINE_REF is not ${m_tag}"
  image_tag="${l_image#*:}"
  image_tag="${image_tag%%@*}"
  [ "v$image_tag" = "$m_tag" ] || fail_release "the image tag is not ${m_tag}"
  echo "engine release ${m_tag} (schema ${m_schema}): image, repo and commit agree"
  exit 0
fi

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
