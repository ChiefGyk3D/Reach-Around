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
#     check, the moving tags are put back to their previous digests, and the job
#     fails. Between the writes and that rollback the moving tags can briefly point
#     at the new image. The only full fix is immutable release tags (a tag ruleset).
#   * The two moving tags are written one at a time (MAJOR.MINOR first, latest
#     last) with their previous digests recorded. A failed write, a tag that does
#     not read back as the new digest, or a failed second check rolls back every tag
#     already written. A rollback is best effort and is reported; a tag that had no
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

declare -A previous=()
for t in "${moving[@]}"; do
  previous[$t]="$(docker buildx imagetools inspect "${repo}:${t}" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"
done

written=()
rollback() {
  local t rc=0
  for t in "${written[@]}"; do
    if [ -n "${previous[$t]}" ]; then
      if docker buildx imagetools create --tag "${repo}:${t}" "${repo}@${previous[$t]}"; then
        echo "rolled ${repo}:${t} back to ${previous[$t]}" >&2
      else
        echo "ROLLBACK FAILED: ${repo}:${t} still points at ${digest}; set it to ${previous[$t]} by hand" >&2
        rc=1
      fi
    else
      echo "${repo}:${t} had no previous digest and still points at ${digest}; remove it by hand if this release is wrong" >&2
      rc=1
    fi
  done
  return "$rc"
}
fail() {
  echo "$1" >&2
  rollback || true
  exit 1
}

for t in "${moving[@]}"; do
  # A failed write may still have landed: count the tag as written either way.
  written+=("$t")
  if ! docker buildx imagetools create --tag "${repo}:${t}" "$IMAGE"; then
    fail "writing ${repo}:${t} failed; promotion is partial or absent, see the rollback lines above"
  fi
done

for t in "${moving[@]}"; do
  got="$(docker buildx imagetools inspect "${repo}:${t}" --format '{{.Manifest.Digest}}' 2>/dev/null || true)"
  if [ "$got" != "$digest" ]; then
    fail "${repo}:${t} reads back as '${got}', not ${digest}; promotion is partial"
  fi
done

# The tag may have moved while the writes ran. Detect it, undo, and fail.
if ! bash "$here/check-release-tag.sh"; then
  fail "the release tag changed while the moving tags were written; they were put back"
fi
echo "promoted ${IMAGE} to ${repo}:latest and ${repo}:${minor}"
