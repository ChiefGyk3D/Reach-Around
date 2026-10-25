#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the image and prove it runs hardened: read-only root, non-root user, no
# capabilities, writes allowed only in /out and /tmp.
# Usage: CONTAINER_ENGINE=podman bash tests/test_container_smoke.sh   (or docker)
set -euo pipefail

engine="${CONTAINER_ENGINE:-podman}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="reach-around:smoke-$$"
out="$(mktemp -d)"
mdir="$(mktemp -d)"
cleanup() {
    # Files written by the container belong to its uid (a subuid when rootless), so
    # empty the directory from inside a container before removing it from the host.
    "$engine" run --rm --user 0 --entrypoint sh -v "$out:/out" "$image" \
        -c 'find /out -mindepth 1 -delete' >/dev/null 2>&1 || true
    "$engine" rmi -f "$image" >/dev/null 2>&1 || true
    "$engine" run --rm --user 0 --entrypoint sh -v "$mdir:/manifest" "$image" \
        -c 'find /manifest -mindepth 1 -delete' >/dev/null 2>&1 || true
    rm -rf "$out" "$mdir"
}
trap cleanup EXIT

fail() { echo "FAIL: $*" >&2; exit 1; }

"$engine" build -f "$root/Containerfile" -t "$image" "$root"

# Same hardening as compose.yaml. The empty /out is made world-writable because the
# container runs as a fixed non-root uid that differs from the host user.
chmod 0777 "$out" "$mdir"
run=("$engine" run --rm --read-only --cap-drop ALL
    --security-opt no-new-privileges:true --tmpfs /tmp
    -v "$root/examples/instance:/config:ro" -v "$out:/out" -v "$mdir:/manifest")

"${run[@]}" "$image" build --config /config/site.yaml --out /out \
    --manifest /manifest/manifest.json \
    --assets-inventory /manifest/assets.json \
    || fail "build exited non-zero"

[ -f "$out/index.html" ] || fail "/out/index.html missing"
[ -f "$mdir/manifest.json" ] || fail "manifest.json missing"
[ ! -e "$out/manifest.json" ] || fail "manifest.json was published inside /out"
[ -f "$mdir/assets.json" ] || fail "assets.json missing"
[ ! -e "$out/assets.json" ] || fail "assets.json was published inside /out"

uid="$("${run[@]}" --entrypoint id "$image" -u)"
[ "$uid" != "0" ] || fail "container process runs as uid 0"

# A write outside /out and /tmp must fail (read-only root filesystem and mount).
if "${run[@]}" --entrypoint sh "$image" -c 'echo x > /etc/escape' 2>/dev/null; then
    fail "write to /etc succeeded"
fi
if "${run[@]}" --entrypoint sh "$image" -c 'echo x > /config/escape' 2>/dev/null; then
    fail "write to read-only /config succeeded"
fi
# /tmp stays writable, so the failures above are not failing for a bad reason.
"${run[@]}" --entrypoint sh "$image" -c 'echo x > /tmp/ok' \
    || fail "/tmp is not writable"

echo "PASS: $engine image builds, runs read-only as uid $uid, writes only /out and /tmp"
