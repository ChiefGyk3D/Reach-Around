#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Point the moving image tags (latest and MAJOR.MINOR) at the image this run
# published and verified. The tag check runs first, in this same step, so the
# decision is never older than the push: if the release tag is no longer the
# highest, or no longer points at the triggering commit, nothing is written.
#
# Environment: TAG, REPO_URL, GITHUB_SHA (see check-release-tag.sh), IMAGE (the
# published image by digest, ghcr.io/<name>@sha256:<64 hex>), GH_TOKEN, ACTOR.
set -euo pipefail
export LC_ALL=C

: "${IMAGE:?IMAGE is required}"
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${ACTOR:?ACTOR is required}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$here/check-release-tag.sh"

if ! [[ "$IMAGE" =~ ^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "the published image reference is not a GHCR digest reference; refusing to promote" >&2
  exit 1
fi
repo="${IMAGE%@*}"
version="${TAG#v}"
minor="${version%.*}"

printf '%s' "$GH_TOKEN" | docker login ghcr.io --username "$ACTOR" --password-stdin
docker buildx imagetools create \
  --tag "${repo}:latest" \
  --tag "${repo}:${minor}" \
  "$IMAGE"
echo "promoted ${IMAGE} to ${repo}:latest and ${repo}:${minor}"
