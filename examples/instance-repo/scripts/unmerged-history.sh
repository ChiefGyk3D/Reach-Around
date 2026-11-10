#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Say whether the open counts branch holds history rows that main does not have
# yet. The refresh job builds on main, so replacing such a branch would throw
# those rows away. Exit 0: safe to replace (the branch has no history file, or
# its history is already a prefix of the one at HEAD). Exit 3: it holds
# unmerged rows; merge or close its pull request first.
#
# Usage: scripts/unmerged-history.sh BRANCH_REF   (run in the repository, HEAD = main)
set -euo pipefail

ref="${1:?usage: unmerged-history.sh BRANCH_REF}"
file="history/social.jsonl"

git cat-file -e "$ref:$file" 2>/dev/null || exit 0

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
git show "$ref:$file" >"$tmp/branch"
if git cat-file -e "HEAD:$file" 2>/dev/null; then
  git show "HEAD:$file" >"$tmp/main"
else
  : >"$tmp/main"
fi

size="$(wc -c <"$tmp/branch")"
if [ "$(wc -c <"$tmp/main")" -ge "$size" ] &&
  head -c "$size" "$tmp/main" | cmp -s - "$tmp/branch"; then
  exit 0
fi
echo "unmerged-history: $ref holds history rows that are not in main yet." >&2
exit 3
