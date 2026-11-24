#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Release tag eligibility. Exits 0 only when all of this holds right now:
#   1. TAG is exactly vMAJOR.MINOR.PATCH: ASCII, no leading zeros, no prerelease,
#      no build metadata, lowercase v. Anything else fails closed.
#   2. The remote tag TAG exists and resolves (annotated tags peeled) to
#      GITHUB_SHA, the commit this run was triggered for. A tag deleted and
#      recreated elsewhere is refused.
#   3. TAG is the highest vMAJOR.MINOR.PATCH tag on the remote.
#
# Environment: TAG, REPO_URL, GITHUB_SHA. The release workflow runs this before
# the build and again, in the same step, immediately before the moving tags are
# promoted.
set -euo pipefail
export LC_ALL=C

: "${TAG:?TAG is required}"
: "${REPO_URL:?REPO_URL is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"

semver='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
if ! [[ "$TAG" =~ $semver ]]; then
  echo "tag '${TAG}' is not a plain vMAJOR.MINOR.PATCH tag (no prerelease, build metadata or leading zeros); refusing to publish it" >&2
  exit 1
fi
if ! [[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "GITHUB_SHA is not a 40-character commit id; refusing to publish" >&2
  exit 1
fi

# The tag's current target: the peeled commit for an annotated tag, else the
# object the ref names. An empty answer (tag deleted, remote unreachable) fails.
listing="$(git ls-remote "$REPO_URL" "refs/tags/${TAG}" "refs/tags/${TAG}^{}")" || {
  echo "could not read tag '${TAG}' from the remote; refusing to publish" >&2
  exit 1
}
peeled="$(printf '%s\n' "$listing" | awk -v r="refs/tags/${TAG}^{}" '$2 == r { print $1 }' | head -n 1)"
direct="$(printf '%s\n' "$listing" | awk -v r="refs/tags/${TAG}" '$2 == r { print $1 }' | head -n 1)"
target="${peeled:-$direct}"
if [ -z "$target" ]; then
  echo "tag '${TAG}' no longer exists on the remote; refusing to publish" >&2
  exit 1
fi
if [ "$target" != "$GITHUB_SHA" ]; then
  echo "tag '${TAG}' now points at ${target}, not the triggering commit ${GITHUB_SHA}; refusing to publish" >&2
  exit 1
fi

tags="$(git ls-remote --tags --refs "$REPO_URL" 'refs/tags/v*')" || exit 1
highest="$(printf '%s\n' "$tags" | sed 's#.*refs/tags/##' |
  grep -E "$semver" | sort -V | tail -n 1)"
if [ "$highest" != "$TAG" ]; then
  echo "tag '${TAG}' is not the highest release tag ('${highest}'); refusing to publish it over a newer one" >&2
  exit 1
fi
echo "tag '${TAG}' is the highest release tag and points at ${GITHUB_SHA}"
