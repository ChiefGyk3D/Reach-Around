#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Point the moving image tags (MAJOR.MINOR and latest) at the image this run
# published and verified.
#
# Environment: TAG, REPO_URL, GITHUB_SHA (see check-release-tag.sh), REPOSITORY
# (owner/name of this repository, from the workflow context), IMAGE (the
# published image by digest, from the build job), GH_TOKEN, ACTOR.
#
# What this does and does not guarantee (stated plainly):
#   * The tag check runs just before the first registry write and again right
#     after the last one. A registry write and a tag check are separate network
#     operations, so a tag moved or a newer tag pushed in the seconds between the
#     first check and the writes cannot be prevented. It is detected by the second
#     check, a rollback to the previous digests is attempted, and the job fails. Between the writes and that rollback the moving tags can briefly point
#     at the new image. The only full fix is immutable release tags (a tag ruleset).
#   * The two moving tags are written one at a time (MAJOR.MINOR first, latest
#     last) with their previous digests recorded. A failed write, a tag that does
#     not read back as the new digest, or a failed second check rolls back every tag
#     attempted. If a previous digest cannot be read (anything but the registry's
#     exact "not found" answer), nothing is written. A rollback reads a tag first
#     and restores it only if it still holds this run's digest, then reads it back;
#     a tag someone else changed is left alone and reported. That read-then-write
#     is NOT atomic: the registry offers no compare-and-swap for a tag (an OCI
#     manifest PUT is unconditional), so a writer who changes a tag between the
#     rollback's read and its write can be overwritten. The release workflow's
#     repository-wide concurrency group stops other releases; nothing here stops a
#     manual push by someone with write access to the package. A tag that had no
#     previous digest cannot be removed here and is named for manual cleanup.
set -euo pipefail
export LC_ALL=C

: "${IMAGE:?IMAGE is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${ACTOR:?ACTOR is required}"
: "${REPOSITORY:?REPOSITORY is required}"
: "${TAG:?TAG is required}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The image must be this repository's own GHCR package, named from the workflow
# context, not taken from the build job's output alone.
if ! [[ "$REPOSITORY" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "REPOSITORY is not owner/name; refusing to promote" >&2
  exit 1
fi
owner="$(printf '%s' "${REPOSITORY%%/*}" | tr '[:upper:]' '[:lower:]')"
name="$(printf '%s' "${REPOSITORY#*/}" | tr '[:upper:]' '[:lower:]')"
expected="ghcr.io/${owner}/${name}"
if ! [[ "$IMAGE" =~ ^(ghcr\.io/[a-z0-9._/-]+)@(sha256:[0-9a-f]{64})$ ]]; then
  echo "the published image reference is not a GHCR digest reference; refusing to promote" >&2
  exit 1
fi
repo="${BASH_REMATCH[1]}"
digest="${BASH_REMATCH[2]}"
if [ "$repo" != "$expected" ]; then
  echo "the published image is '${repo}', not this repository's package '${expected}'; refusing to promote" >&2
  exit 1
fi

bash "$here/check-release-tag.sh"

version="${TAG#v}"
minor="${version%.*}"
# Order matters: latest is written last, so a failure earlier leaves it alone.
moving=("$minor" latest)

printf '%s' "$GH_TOKEN" | docker login ghcr.io --username "$ACTOR" --password-stdin

digest_re='^sha256:[0-9a-f]{64}$'
# Anything printed from outside this script is reduced to a safe charset first.
safe() { printf '%s' "${1:0:80}" | tr -c 'A-Za-z0-9:._/@-' '?'; }

# Read one tag's digest. Prints the digest, or nothing when the registry
# confirms the tag does not exist. "Confirms" is exact: the command failed and its
# whole stderr is the single line `ERROR: <repo>:<tag>: not found`. Any other
# failure or wording (transport, auth, a longer message that merely contains
# "not found"), or a malformed digest, returns non-zero: callers must not treat
# it as "absent".
read_tag() {
  local out err rc=0
  err="$(mktemp)"
  out="$(docker buildx imagetools inspect "${repo}:$1" --format '{{.Manifest.Digest}}' 2>"$err")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    local msg
    msg="$(cat "$err")"
    rm -f "$err"
    [ "$msg" = "ERROR: ${repo}:$1: not found" ] && return 0
    return 1
  fi
  rm -f "$err"
  [[ "$out" =~ $digest_re ]] || return 1
  printf '%s' "$out"
}

# Fail closed: without a readable baseline for every moving tag no write is made.
declare -A previous=()
for t in "${moving[@]}"; do
  if ! previous[$t]="$(read_tag "$t")"; then
    echo "could not read the current state of ${repo}:${t}; refusing to promote without a rollback baseline" >&2
    exit 1
  fi
done

written=()
# Undo only what this promotion still appears to own. A tag is restored only when
# a fresh read shows it holds the digest this run was installing; a tag that holds
# anything else is left alone and reported. Every restore is read back. The read
# and the restore are separate registry calls, so this narrows but does not close
# the window for a concurrent writer (see the header).
rollback() {
  local t cur rc=0 now
  for t in "${written[@]}"; do
    if ! cur="$(read_tag "$t")"; then
      echo "ROLLBACK SKIPPED: could not read ${repo}:${t}; check it by hand (this run installed ${digest}, it was ${previous[$t]:-absent})" >&2
      rc=1
      continue
    fi
    if [ "$cur" = "${previous[$t]}" ]; then
      echo "${repo}:${t} already holds its previous state; nothing to undo" >&2
      continue
    fi
    if [ "$cur" != "$digest" ]; then
      echo "ROLLBACK SKIPPED: ${repo}:${t} was changed by someone else to $(safe "$cur"); left alone" >&2
      rc=1
      continue
    fi
    if [ -z "${previous[$t]}" ]; then
      echo "${repo}:${t} had no previous digest and still points at ${digest}; remove it by hand if this release is wrong" >&2
      rc=1
      continue
    fi
    docker buildx imagetools create --tag "${repo}:${t}" "${repo}@${previous[$t]}" >&2 || true
    if now="$(read_tag "$t")" && [ "$now" = "${previous[$t]}" ]; then
      echo "rolled ${repo}:${t} back to ${previous[$t]}" >&2
    else
      echo "ROLLBACK FAILED: ${repo}:${t} does not read back as ${previous[$t]}; set it by hand" >&2
      rc=1
    fi
  done
  return "$rc"
}
fail() {
  echo "$1" >&2
  if rollback; then
    echo "rollback finished: every tag this run wrote holds its previous state" >&2
  else
    echo "ROLLBACK INCOMPLETE: at least one moving tag was not restored; see the lines above and fix it by hand" >&2
  fi
  exit 1
}

for t in "${moving[@]}"; do
  # A failed write may still have landed: count the tag as attempted either way.
  # Rollback reads the tag first, so an attempt that never landed is not undone.
  written+=("$t")
  if ! docker buildx imagetools create --tag "${repo}:${t}" "$IMAGE"; then
    fail "writing ${repo}:${t} failed; promotion is partial or absent"
  fi
done

for t in "${moving[@]}"; do
  got="$(read_tag "$t" || true)"
  if [ "$got" != "$digest" ]; then
    fail "${repo}:${t} reads back as '$(safe "$got")', not ${digest}; promotion is partial"
  fi
done

# The tag may have moved while the writes ran. Detect it, undo, and fail.
if ! bash "$here/check-release-tag.sh"; then
  fail "the release tag changed while the moving tags were written"
fi
echo "promoted ${IMAGE} to ${repo}:latest and ${repo}:${minor}"
