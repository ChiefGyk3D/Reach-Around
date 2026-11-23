#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Plan only. Shows what OpenTofu would change on the zone; changes nothing.
#
#   TF_VAR_zone_id=<zone id> CLOUDFLARE_API_TOKEN=<token> deploy/edge/plan.sh
#
# There is no apply here on purpose: an apply is the owner's explicit step,
# typed by hand after reading this plan, and that path is NOT approved yet
# (docs/edge.md). The script reads the token from the environment and never
# prints it or puts it on a command line.
#
# Isolation. tofu and the provider run under `env -i` with an allow-list, so no
# other variable of the caller reaches them (no TF_CLI_CONFIG_FILE with
# dev_overrides, no plugin cache, no proxy or CA variables, no other TF_VAR_*,
# no CLOUDFLARE_* but the token). They get a fixed PATH, a fresh scratch HOME and
# data directory (removed again), the reviewed tofu.rc, and a tofu binary found
# at a fixed absolute path that is checked first (TOFU_BIN may name another). The token reaches them through
# a file descriptor, not argv.
#
# Exit: 64 usage, 65 no trusted tofu, otherwise tofu's own code
# (-detailed-exitcode: 0 no changes, 1 error, 2 changes planned).
set -eu

# First, drop everything but the three variables this script needs, so even its
# own helper commands (stat, readlink, mktemp) run without LD_PRELOAD, BASH_ENV,
# a hostile PATH and the like.
for name in $(compgen -e); do
    case "$name" in
        HOME | CLOUDFLARE_API_TOKEN | TF_VAR_zone_id | TOFU_BIN) ;;
        *) unset "$name" 2>/dev/null || true ;;
    esac
done
PATH=/usr/bin:/bin
export PATH

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "${CLOUDFLARE_API_TOKEN:-}" ]; then
    echo "plan: CLOUDFLARE_API_TOKEN is not set" >&2
    exit 64 # not 2: with -detailed-exitcode, 2 means "changes planned"
fi
zone="${TF_VAR_zone_id:-}"
if [ -z "$zone" ]; then
    echo "plan: TF_VAR_zone_id is not set" >&2
    exit 64
fi
if ! [[ "$zone" =~ ^[0-9a-f]{32}$ ]]; then
    echo "plan: TF_VAR_zone_id must be 32 lowercase hex characters" >&2
    exit 64
fi

me="$(id -u)"
mygid="$(id -g)"

# A path is trusted when its owner is root or the current user and nobody else
# can write to it (group write is accepted only for the user's own group; a
# world-writable directory only when sticky).
trusted_node() {
    local node="$1" owner mode group
    read -r owner mode group < <(stat -c '%u %a %g' -- "$node") || return 1
    [ "$owner" = 0 ] || [ "$owner" = "$me" ] || return 1
    mode=$((8#$mode))
    if (( mode & 8#020 )) && [ "$group" != "$mygid" ] && [ "$group" != 0 ]; then
        return 1
    fi
    if (( mode & 8#002 )); then
        [ -d "$node" ] && (( mode & 8#1000 )) || return 1
    fi
    return 0
}

trusted_chain() {
    local path="$1"
    while :; do
        trusted_node "$path" || return 1
        [ "$path" = / ] && return 0
        path="$(dirname -- "$path")"
    done
}

# TOFU_BIN (an absolute path) replaces the default list; it is verified the same
# way, so it cannot name a binary that others can write to. It is how the tests
# point at a fake tofu and never reach a real one.
if [ -n "${TOFU_BIN:-}" ]; then
    case "$TOFU_BIN" in
        /*) candidates=("$TOFU_BIN") ;;
        *)
            echo "plan: TOFU_BIN must be an absolute path" >&2
            exit 65
            ;;
    esac
else
    candidates=(/usr/local/bin/tofu /usr/bin/tofu "${HOME:-/nonexistent}/.local/bin/tofu")
fi
tofu_bin=""
for candidate in "${candidates[@]}"; do
    [ -f "$candidate" ] && [ -x "$candidate" ] || continue
    resolved="$(readlink -f -- "$candidate")" || continue
    [ -f "$resolved" ] && [ -x "$resolved" ] || continue
    if trusted_chain "$resolved" && trusted_chain "$(dirname -- "$candidate")"; then
        tofu_bin="$resolved"
        break
    fi
done
if [ -z "$tofu_bin" ]; then
    echo "plan: no trusted tofu found (looked in TOFU_BIN or /usr/local/bin, /usr/bin and ~/.local/bin; it must be owned by root or you and not writable by others)" >&2
    exit 65
fi

scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
mkdir -- "$scratch/home" "$scratch/data" "$scratch/tmp"

run_tofu() {
    # The token comes in on fd 3 and is exported only inside the isolated shell.
    env -i \
        PATH=/usr/bin:/bin \
        HOME="$scratch/home" \
        TMPDIR="$scratch/tmp" \
        TF_DATA_DIR="$scratch/data" \
        TF_VAR_zone_id="$zone" \
        TF_CLI_CONFIG_FILE="$here/tofu.rc" \
        /bin/bash --noprofile --norc -c \
        'IFS= read -r CLOUDFLARE_API_TOKEN <&3 || exit 70
         export CLOUDFLARE_API_TOKEN
         unset PWD OLDPWD SHLVL
         cd "$1" && shift
         exec "$@" 3<&-' \
        bash "$here" "$tofu_bin" "$@" 3<<<"$CLOUDFLARE_API_TOKEN"
}

set +e
run_tofu init -input=false -lockfile=readonly >/dev/null
rc=$?
if [ "$rc" -ne 0 ]; then
    exit "$rc"
fi
run_tofu plan -input=false -lock-timeout=60s -detailed-exitcode
exit $?
