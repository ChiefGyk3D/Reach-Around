<!-- SPDX-License-Identifier: Apache-2.0 -->
# Releasing

A release is a `v*` git tag on `main`. The tag starts `.github/workflows/release.yml`,
which calls two reusable workflows from
[git-your-ship-together](https://github.com/ChiefGyk3D/git-your-ship-together)
(pinned by commit, `v1.24.0`):

1. `container-release.yml` lints the `Containerfile` with hadolint, builds `linux/amd64`
   and `linux/arm64`, requires a non-root user, runs `--version` in the image (also with a
   read-only root filesystem), scans with Trivy, then pushes one multi-arch OCI image
   index to GHCR, signs it with cosign (keyless), attaches an SPDX SBOM and records SLSA
   build provenance.
2. `verify-published.yml` checks the signature, the SBOM attestation and the provenance
   from outside, pulls each platform by digest and runs `--version` in it.

The only credential is the run's `GITHUB_TOKEN`. The workflow sets no Doppler or Docker
Hub input and passes no secret. The image is an OCI image, so Docker and Podman both
run it.

## Changelog fragments

A pull request does not edit `CHANGELOG.md`. It adds one file,
`changelog.d/<issue-number>-<slug>.md`, holding one entry (see
[changelog.d/README.md](../changelog.d/README.md)), so parallel pull requests never
conflict. The CI lint step runs `scripts/changelog-assemble.py verify` (every fragment is
valid, no duplicate issue number, no hand-written entry in `[Unreleased]`) and
`check-pr` (the pull request adds exactly one fragment, unless its body has a line
`no-changelog: <reason>` or it has the `no-changelog` label). `assemble` is a preview: it prints the changelog with the
fragments folded into `[Unreleased]` and writes nothing. Only `release` (below) writes.

## Cut a release

1. Set `__version__` in `src/reach_around/__init__.py`. It is the only place the version
   lives; `pyproject.toml` reads it. Create the matching `## [x.y.z]` section by folding
   the pull requests' fragments in `changelog.d/` into `CHANGELOG.md`:

   ```sh
   python3 -I scripts/changelog-assemble.py release 0.3.0-rc.7 --date 2026-11-24
   ```

   It moves the entries under a new `## [x.y.z] - date` heading (highest issue number
   first, byte-stable), leaves `## [Unreleased]` with only its pointer comment, and
   deletes the fragments. Commit the result with the version bump, and put a line
   `no-changelog: release` in that pull request's body (it adds no fragment).
   `tests/test_version.py` fails if the version and the top heading disagree. A release candidate is written
   `x.y.z-rc.N` (for example `0.3.0-rc.6`), so the tag `v0.3.0-rc.6` equals `v` plus
   `__version__`; a failed candidate is never retagged, the next number is used.
2. Merge to `main` and wait for `ci / CI green`.
3. Tag the merge commit and push the tag (the maintainer does this):

   ```sh
   git tag -s v0.1.0 -m "v0.1.0"
   git push origin v0.1.0
   ```

4. Watch the `Release` run. The first job fails the release if the tag is not exactly
   `v` plus `__version__`.
5. Image tags come from the tag: `0.1.0`, `0.1`, `latest` and `sha-<short>`. There is no
   major-only tag while the project is `0.x`. `0.1.0` and `sha-<short>` are written by
   the build; `0.1` and `latest` move in a last job, after the image is verified, and
   only if the tag still points at the released commit and is still the highest tag.
6. Confirm the package is visible the way you want it. A new GHCR package inherits
   the repository's visibility. This repository is public, so the image is public and
   pulling it needs no sign-in. A fork that is private gets a private image, and pulling
   that one needs `docker login ghcr.io` or `podman login ghcr.io` with a token that
   can read packages.

## Verify an image

Replace `0.1.0` with the version, and `<digest>` with the digest the `Release` run
prints (the `release` job's `digest` output) or `podman inspect` shows.

```sh
IMAGE=ghcr.io/chiefgyk3d/reach-around

# Signature and SBOM attestation (the signing identity is the reusable workflow).
cosign verify "$IMAGE:0.1.0" \
  --certificate-identity-regexp '^https://github.com/ChiefGyk3D/git-your-ship-together/\.github/workflows/container-release\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
cosign verify-attestation --type spdxjson "$IMAGE:0.1.0" \
  --certificate-identity-regexp '^https://github.com/ChiefGyk3D/git-your-ship-together/\.github/workflows/container-release\.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# Build provenance.
gh attestation verify "oci://$IMAGE:0.1.0" --owner ChiefGyk3D
```

Run by digest, not by tag, when it matters:

```sh
podman pull "$IMAGE@sha256:<digest>"
podman run --rm "$IMAGE@sha256:<digest>" --version      # reach-around 0.1.0
docker run --rm "$IMAGE@sha256:<digest>" --version
```

## Smoke test

These are the commands to run against any image (a published one, or one built locally
with `podman build -f Containerfile -t reach-around:local .` or `docker build ...`). They
build `examples/instance/site.yaml` inside the container with the same hardening as
`compose.yaml`. The output directory must be writable by uid 10001.

```sh
mkdir -p out && chmod 0777 out        # throwaway directory
podman run --rm reach-around:local --version
podman run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /tmp \
  -v ./examples/instance:/config:ro -v ./out:/out \
  reach-around:local build --config /config/site.yaml --out /out \
    --manifest /tmp/manifest.json
ls out/index.html
```

Swap `podman` for `docker` to use Docker. For a full check, `tests/test_container_smoke.sh`
also proves the container cannot write outside `/out` and `/tmp`:

```sh
CONTAINER_ENGINE=podman bash tests/test_container_smoke.sh
CONTAINER_ENGINE=docker bash tests/test_container_smoke.sh
```

## Roll back

Nothing here deletes history, and a published tag is never moved.

- **A user pins the previous version.** Run `ghcr.io/chiefgyk3d/reach-around:0.1.0`
  (or its digest) instead of the new tag. `latest` is the only moving tag; point it back
  by re-running the last good release's `Release` workflow, or stop using it.
- **The release is bad.** Fix forward: bump the patch version, add a `CHANGELOG.md`
  entry that names the problem, and tag the fix. Do not reuse or move the bad tag.
- **The image must disappear** (a leaked secret, for example). Delete that version in the
  GHCR package settings, delete the git tag and the GitHub release, rotate whatever
  leaked, and say so in the next `CHANGELOG.md` entry.
