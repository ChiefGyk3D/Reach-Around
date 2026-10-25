#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fail if the repository holds files that must never be committed. This is a
# filename check, not a secret scanner; keep GitHub secret scanning on too.
set -euo pipefail

# Usage: scripts/check-repo.sh [--root DIR]
#   --root DIR  check the repository in DIR (default: the one this script
#               belongs to). A pull request preview runs a trusted copy of
#               this script against the pull request's files.
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${1:-}" = "--root" ]; then root="${2:?--root needs a folder}"; fi
cd "$root"

# In a Git checkout, look at tracked files; otherwise (an unpacked copy) at
# everything on disk.
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  files="$(git ls-files)"
else
  files="$(find . -type f -not -path './.git/*' | sed 's|^\./||')"
fi

bad="$(printf '%s\n' "$files" | grep -E \
  '(^|/)(\.env(\..*)?|wrangler\.deploy\.toml|.*\.bak|.*\.settings|id_(rsa|ed25519)|.*\.(pem|key|p12))$' ||
  true)"

if [ -n "$bad" ]; then
  echo "These files must not be in the repository:" >&2
  printf '%s\n' "$bad" | sed 's/^/  /' >&2
  echo "Remove them from Git (git rm --cached <file>) and keep them private." >&2
  echo "If one held a real token, revoke the token first." >&2
  exit 1
fi
echo "check-repo: no forbidden files."
