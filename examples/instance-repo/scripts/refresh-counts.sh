#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Refresh the follower counts with the pinned engine image and put the new
# numbers.json in the repository folder. Used by the scheduled
# refresh-counts workflow; you can run it by hand too.
#
# The history file history/social.jsonl (when the config sets
# numbers.history_file) is carried the same way: copied in, written by the
# engine, checked by scripts/verify-history.sh, then put in the repository
# folder.
#
# The fediverse snapshot file numbers.fediverse.json (the engine writes it beside
# numbers.json when site.yaml has numbers.fediverse sources) is carried the same
# way as numbers.json: the committed copy goes in, the engine's copy comes out and
# is checked by `validate-numbers.py --fediverse` (the engine's own size, key and
# anonymity rules; the whole file is refused when any part fails), then it is put
# in the repository folder. It is the only file of the collector that is carried:
# the unit note (.numbers.json.unit), its .bad copy, the pending note and any
# staged or temp copy stay in the scratch folder and are dropped with it.
#
# The new numbers.json is checked by scripts/validate-numbers.py before it is
# copied into the repository folder (and the workflow's publish job checks it
# again after it crosses to the job that may write).
#
# Usage: scripts/refresh-counts.sh [--workdir DIR]
#   --workdir DIR  scratch folder for the engine's result files (default: a
#                  temporary folder that is removed afterwards)
#
# The engine image is the one engine.lock names by digest (read-lock.sh checks
# its shape). The engine runs with a read-only root file system, no
# capabilities and the caller's own user id. Unlike the build, it needs the
# network to ask each platform for a count, so it is NOT started with
# --network none. Only site.yaml, media/ and overrides/ are mounted read-only,
# plus the scratch folder it writes into.
#
# Keys come from the environment only. Of the names below, only those
# that are set and not empty are passed to the engine (by name; the value is
# never on a command line or printed). A source whose key is missing is
# skipped by the engine, not failed. These are the canonical names the engine reads (YOUTUBE_API_KEY, YOUTUBE_USERNAME,
# TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET). The config cannot name another variable.
#
# The engine keeps a suspect value out (a zero, or a fall of more than 90
# percent from 1000 or more) and records it as a failed source. This script
# never overrides that. Exit codes: 0 all sources fine or skipped; 3 from the
# engine (some source failed, the good ones are still written) is also exit 0
# here, with "source_failed=true" appended to $GITHUB_OUTPUT, so the workflow
# can commit the good numbers first and fail afterwards. Any other engine exit
# code (a config problem, a write problem) is returned as it is.
#
# Token health: after the refresh the script runs `counts tokens` once more
# (offline, no keys, read-only mounts) and shows the result as GitHub Actions
# annotations (a warning while a token is expiring, an error once it is expired
# or was rejected) and as a table in $GITHUB_STEP_SUMMARY. It only reports: its
# own exit code never changes this script's, and an engine too old to know the
# command just skips it. Sending the warning to Pushover, Discord or Matrix is
# the instance's own choice (docs/token-health.md in the engine repository).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workdir=""
while [ $# -gt 0 ]; do
  case "$1" in
    --workdir)
      workdir="${2:?--workdir needs a folder}"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

# The refresh deadline in seconds (see the block before the engine runs). Checked
# first so a bad value is refused before anything starts.
deadline="${REFRESH_TIMEOUT_SECONDS:-480}"
case "$deadline" in
  *[!0-9]* | "") deadline=bad ;;
  *) if [ "$deadline" -lt 30 ] || [ "$deadline" -gt 480 ]; then deadline=bad; fi ;;
esac
if [ "$deadline" = bad ]; then
  echo "error: REFRESH_TIMEOUT_SECONDS must be digits, 30 to 480" >&2
  exit 2
fi

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
if [ -z "$workdir" ]; then
  workdir="$(mktemp -d)"
  cleanup="$workdir"
fi
mkdir -p "$workdir"
workdir="$(cd "$workdir" && pwd)"
chmod 700 "$workdir"
trap 'if [ -n "$cleanup" ]; then rm -rf "$cleanup"; fi' EXIT

userns=()
if "$runtime" --version 2>/dev/null | grep -qi podman; then
  userns=(--userns keep-id)
fi

mounts=()
for item in site.yaml media overrides; do
  path="$here/$item"
  if [ -L "$path" ]; then
    echo "error: $item must not be a symbolic link" >&2
    exit 2
  fi
  if [ -e "$path" ]; then mounts+=(-v "$path":"/config/$item":ro); fi
done
[ -f "$here/site.yaml" ] || {
  echo "error: site.yaml not found" >&2
  exit 2
}
# The read-only mounts, kept for the offline token report below.
config_mounts=("${mounts[@]}")

# Keep numbers entered by hand (platforms without a source): the engine reads
# the old file and only replaces what a source returns.
numbers="$here/numbers.json"
if [ -L "$numbers" ]; then
  echo "error: numbers.json must not be a symbolic link" >&2
  exit 2
fi
if [ -f "$numbers" ]; then cp "$numbers" "$workdir/numbers.json"; fi

# The fediverse snapshot (beside numbers.json). The engine reads the committed
# copy to keep a platform whose read fails, and writes its own copy next to the
# numbers file in the scratch folder.
fediverse="$here/numbers.fediverse.json"
if [ -L "$fediverse" ]; then
  echo "error: numbers.fediverse.json must not be a symbolic link" >&2
  exit 2
fi
if [ -f "$fediverse" ]; then cp "$fediverse" "$workdir/numbers.fediverse.json"; fi

# The history file (numbers.history_file in site.yaml must be
# history/social.jsonl). The engine writes it in place, so the folder is a
# scratch copy mounted writable; the repository's own copy is replaced only
# after the engine's copy passes `counts history-verify`.
history_dir="$here/history"
history="$history_dir/social.jsonl"
if [ -L "$history_dir" ] || [ -L "$history" ]; then
  echo "error: history must not be a symbolic link" >&2
  exit 2
fi
mkdir -p "$workdir/history"

# The repository's history file can change while the engine runs (a
# `counts history-redact`, an edit, a git pull). The result below was built from
# the copy, so installing it over a changed file would undo that change, and a
# redaction would bring the removed values back. Remember which file (device and
# inode) and which bytes (SHA-256 of the copy) the engine started from, and
# refuse to install over anything else.
file_id() { stat -c '%d:%i' "$1" 2>/dev/null || stat -f '%d:%i' "$1"; }
file_sha() { sha256sum "$1" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$1" | cut -d' ' -f1; }
hist_id=""
hist_sha=""
if [ -f "$history" ]; then
  hist_id="$(file_id "$history")"
  cp "$history" "$workdir/history/social.jsonl"
  hist_sha="$(file_sha "$workdir/history/social.jsonl")"
fi
history_unchanged() {
  if [ -z "$hist_id" ]; then
    [ ! -e "$history" ] && [ ! -L "$history" ]
    return
  fi
  [ ! -L "$history" ] && [ -f "$history" ] &&
    [ "$(file_id "$history")" = "$hist_id" ] &&
    [ "$(file_sha "$history")" = "$hist_sha" ]
}
history_changed() {
  echo "refresh-counts: the history file changed while the refresh ran (a redaction, an edit or a pull?); nothing was overwritten. Run the refresh again." >&2
  exit 1
}
mounts+=(-v "$workdir/history":/config/history)

# "-e NAME" with no value copies the variable from this process; nothing is
# written to the command line or the log.
keys=()
for name in YOUTUBE_API_KEY YOUTUBE_USERNAME TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET; do
  if [ -n "${!name:-}" ]; then keys+=(-e "$name"); fi
done

# Hard deadline for the refresh container (issue #768, F-59). A platform that
# accepts a connection and never answers, or an engine that hangs, must not hold
# the runner until the job's own limit (10 minutes in the example workflow, which
# stays as the second boundary). 480 seconds leaves the report and the publish
# step room inside it. REFRESH_TIMEOUT_SECONDS may LOWER it (digits only, 30 to
# 480); a larger or malformed value is refused, never used. `timeout` sends TERM
# (the runtime passes it to the container) and KILL 20 seconds later. Where
# `timeout` is missing (a stock macOS) the refresh runs without the deadline and
# says so; the CI runner has it.
refresh_limit=()
if command -v timeout >/dev/null 2>&1; then
  refresh_limit=(timeout --kill-after=20 "$deadline")
else
  echo "refresh-counts: warning: no 'timeout' command, the refresh has no hard deadline" >&2
fi

set +e
${refresh_limit[@]+"${refresh_limit[@]}"} "$runtime" run --rm --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --pids-limit 256 --memory 1g \
  --tmpfs /tmp \
  --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
  ${keys[@]+"${keys[@]}"} \
  "${mounts[@]}" -v "$workdir":/data \
  "$image" counts refresh --config /config/site.yaml --out /data/numbers.json
status=$?
set -e

case "$status" in
  0) failed=false ;;
  3) failed=true ;;
  124 | 137)
    echo "refresh-counts: the engine did not finish within ${deadline} seconds and was stopped; nothing was changed." >&2
    exit 1
    ;;
  *)
    echo "refresh-counts: the engine stopped with exit code $status; nothing was changed." >&2
    exit "$status"
    ;;
esac

# Token health report. Best effort and read only: the container has no network
# and no keys, sees the config and the status file the engine just wrote, and
# prints fixed words and day counts, never a token. Annotations go to the step
# log, the table to the step summary. Whatever it returns is ignored on purpose
# (a refusal to report must not hide the numbers); the engine's own exit code,
# already handled above, is what turns a run red.
# A hung report is cut after 60 seconds where `timeout` exists (GNU coreutils;
# not on a stock macOS), so it can never hold the refresh job.
limit=()
if command -v timeout >/dev/null 2>&1; then limit=(timeout 60); fi
report_tokens() {
  local format="$1"
  ${limit[@]+"${limit[@]}"} "$runtime" run --rm --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --pids-limit 64 --memory 256m \
    --network none --tmpfs /tmp \
    --user "$(id -u):$(id -g)" ${userns[@]+"${userns[@]}"} \
    "${config_mounts[@]}" -v "$workdir":/data:ro \
    "$image" counts tokens --config /config/site.yaml \
    --status /data/numbers.status.json --format "$format" 2>/dev/null
}
set +e
report_tokens github
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  report_tokens markdown >>"$GITHUB_STEP_SUMMARY"
fi
set -e

# The engine's file is data from a container that talked to the network. It
# reaches the repository only through the strict validator, which writes a
# fresh copy to a temporary file and renames it into place.
# Only history/social.jsonl is carried. Any other file the engine left in the
# history folder (a config naming another file) would be dropped silently, so
# refuse it, before anything is validated or installed. The engine's own lock
# file for the history (.social.jsonl.lock) is the one expected extra; it is
# not carried.
extra="$(find "$workdir/history" -mindepth 1 ! -name social.jsonl ! -name .social.jsonl.lock -printf '%f ' 2>/dev/null || true)"
if [ -n "$extra" ]; then
  echo "refresh-counts: the engine wrote other files in the history folder ($extra); only history/social.jsonl is carried, so set numbers.history_file to history/social.jsonl. Nothing was changed." >&2
  exit 1
fi
new_history=""
if [ -e "$workdir/history/social.jsonl" ]; then
  "$here/scripts/verify-history.sh" "$workdir/history/social.jsonl" || {
    echo "refresh-counts: the engine's history file failed the check; nothing was changed." >&2
    exit 1
  }
  new_history="$workdir/history/social.jsonl"
fi
# Before anything is written: still the file the engine started from?
if ! history_unchanged; then history_changed; fi
# The final move is only safe against a redaction under the lock the engine's
# writers take, so without flock nothing is installed (a check and a move are not
# one step).
if [ -n "$new_history" ] && ! command -v flock >/dev/null 2>&1; then
  echo "refresh-counts: flock is required to install the history file safely and was not found; nothing was overwritten." >&2
  exit 1
fi
# The two outputs are installed as an ordered pair, not atomically. numbers.json
# is checked into a scratch file now but not put in the repository folder yet:
# every refusal before the install (a failed check, a changed history file, a
# held lock, a failed staging copy) leaves BOTH files as they were. The install
# is then two renames under the lock, history first. If the first fails, neither
# file is installed; if the second fails after the first succeeded, the history
# is new and numbers.json is old, the message says so, and the fix is to run the
# refresh again.
checked_numbers=""
if [ -f "$workdir/numbers.json" ] && [ ! -L "$workdir/numbers.json" ]; then
  checked_numbers="$workdir/numbers.checked.json"
  python3 -I "$here/scripts/validate-numbers.py" \
    --in "$workdir/numbers.json" --out "$checked_numbers" || {
    echo "refresh-counts: the engine's numbers.json failed the check; nothing was changed." >&2
    exit 1
  }
fi

# The engine's snapshot file, checked the same way (into a scratch file; the
# whole file is refused when any part fails, and then nothing is changed).
checked_fediverse=""
if [ -f "$workdir/numbers.fediverse.json" ] && [ ! -L "$workdir/numbers.fediverse.json" ]; then
  checked_fediverse="$workdir/numbers.fediverse.checked.json"
  python3 -I "$here/scripts/validate-numbers.py" --fediverse \
    --in "$workdir/numbers.fediverse.json" --out "$checked_fediverse" || {
    echo "refresh-counts: the engine's fediverse snapshot failed the check; nothing was changed." >&2
    exit 1
  }
fi

staged_history=""
staged_numbers=""
staged_fediverse=""
# A staged file that was not moved into place is removed on any exit.
trap 'rm -f "$staged_history" "$staged_numbers" "$staged_fediverse"; if [ -n "$cleanup" ]; then rm -rf "$cleanup"; fi' EXIT

if [ -n "$new_history" ]; then
  mkdir -p "$history_dir"
  # Take the lock the engine's history writers (append, redact) take, so a
  # redaction cannot run between the last check and the move. flock is required
  # (checked above); the checks before and after the lock catch a change made
  # while the run waited. The lock is held (fd 9) until the script exits, which
  # is after both files are in place.
  lock_file="$history_dir/.social.jsonl.lock"
  if [ -L "$lock_file" ]; then
    echo "error: the history lock file must not be a symbolic link" >&2
    exit 2
  fi
  exec 9>>"$lock_file"
  chmod 600 "$lock_file" 2>/dev/null || true
  flock -w "${HISTORY_LOCK_WAIT_SECONDS:-30}" 9 || {
    echo "refresh-counts: the history file is locked by another run (a redaction?); nothing was overwritten." >&2
    exit 1
  }
  if ! history_unchanged; then history_changed; fi
  staged_history="$(mktemp "$history_dir/.social.XXXXXX")"
  cp "$new_history" "$staged_history"
fi
if [ -n "$checked_fediverse" ]; then
  staged_fediverse="$(mktemp "$here/.numbers.fediverse.XXXXXX")"
  cp "$checked_fediverse" "$staged_fediverse"
  chmod 644 "$staged_fediverse" # a public file; mktemp makes it 0600
fi
if [ -n "$checked_numbers" ]; then
  staged_numbers="$(mktemp "$here/.numbers.XXXXXX")"
  cp "$checked_numbers" "$staged_numbers"
  chmod 644 "$staged_numbers" # numbers.json is a public file; mktemp makes it 0600
fi

# Install, still under the lock: the history first, numbers.json only after
# that move succeeded. Each move is one rename; a failure of the first leaves
# both files as they were.
if [ -n "$staged_history" ]; then
  mv -f "$staged_history" "$history" || {
    echo "refresh-counts: the history file could not be installed; nothing was overwritten." >&2
    exit 1
  }
  staged_history=""
fi
if [ -n "$staged_fediverse" ]; then
  mv -f "$staged_fediverse" "$fediverse" || {
    echo "refresh-counts: numbers.fediverse.json could not be installed; run the refresh again." >&2
    exit 1
  }
  staged_fediverse=""
fi
if [ -n "$staged_numbers" ]; then
  mv -f "$staged_numbers" "$numbers" || {
    echo "refresh-counts: numbers.json could not be installed after the history file was; run the refresh again." >&2
    exit 1
  }
  staged_numbers=""
fi

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "source_failed=$failed" >>"$GITHUB_OUTPUT"
fi
if [ "$failed" = true ]; then
  echo "refresh-counts: at least one source failed; the numbers that did refresh were kept." >&2
fi
exit 0
