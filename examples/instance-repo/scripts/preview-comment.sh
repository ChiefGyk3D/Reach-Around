#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Write the pull request comment for the preview, in plain language.
#
# Usage: scripts/preview-comment.sh --check-status N [--build-status N]
#          --check FILE --out FILE [--artifact-url URL] [--run-url URL]
#   --check-status N   exit status of scripts/check-config.sh (0, 2 or 3)
#   --build-status N   exit status of the site build (leave out if it did not run)
#   --check FILE       the plain-language text check-config.sh wrote
#   --out FILE         where to write the Markdown comment
#   --artifact-url URL link to the `preview` artifact (https://github.com/... only)
#   --run-url URL      link to the workflow run (https://github.com/... only)
#
# The comment holds fixed text, the check's output (which names tabs, blocks
# and fields and never prints a value) and two links. Nothing else from the
# change is copied into it. The first line is a marker the workflow uses to
# recognize its own comment.
set -euo pipefail

check_status=""
build_status=""
check_file=""
out=""
artifact_url=""
run_url=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check-status)
      check_status="${2:?}"
      shift 2
      ;;
    --build-status)
      build_status="${2-}"
      shift 2
      ;;
    --check)
      check_file="${2:?}"
      shift 2
      ;;
    --out)
      out="${2:?}"
      shift 2
      ;;
    --artifact-url)
      artifact_url="${2-}"
      shift 2
      ;;
    --run-url)
      run_url="${2-}"
      shift 2
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done
[ -n "$check_status" ] && [ -n "$out" ] || {
  echo "usage: see the header" >&2
  exit 2
}

# Links go into Markdown, so accept only a plain GitHub address.
link_ok() { printf '%s' "$1" | grep -Eq '^https://github\.com/[A-Za-z0-9._/-]{1,200}$'; }
link_ok "$artifact_url" || artifact_url=""
link_ok "$run_url" || run_url=""

# At most 60 lines and 20 KB of the check's text; a comment holds 65,536.
details=""
if [ -n "$check_file" ] && [ -f "$check_file" ] && [ ! -L "$check_file" ]; then
  details="$(head -n 60 "$check_file" | head -c 20000)"
fi

{
  echo "<!-- reach-around-preview -->"
  if [ "$check_status" = "0" ] && [ "$build_status" = "0" ]; then
    echo "## The preview is ready"
    echo
    echo "Your change is valid and the site builds."
    echo
    echo "$details"
    echo
    if [ -n "$artifact_url" ]; then
      echo "**Look at it:** download the [\`preview\` artifact]($artifact_url), unzip it"
      echo "and open \`index.html\`."
    else
      echo "**Look at it:** open the run and download the \`preview\` artifact, unzip it"
      echo "and open \`index.html\`."
    fi
    echo
    echo "Nothing is deployed yet. Merging this pull request into \`main\` deploys"
    echo "it, after a reviewer approves."
  elif [ "$check_status" = "0" ]; then
    echo "## The settings are valid, but the site did not build"
    echo
    echo "$details"
    echo
    echo "Nothing is deployed. The build stopped for a reason the settings check"
    echo "cannot see (for example a picture file that is not in the repository)."
    echo "Open the run log to see which file or setting."
  elif [ "$check_status" = "2" ]; then
    echo "## This change has a problem"
    echo
    echo "$details"
    echo
    echo "Nothing was built and nothing is deployed. Edit \`site.yaml\` on this"
    echo "branch again; this check runs again after every save. The numbers count"
    echo "tabs, blocks and list entries from 1."
  else
    echo "## The check could not run"
    echo
    echo "This is a problem with the check, not with your change. Nothing was"
    echo "built and nothing is deployed. Open the run log. The usual causes are a"
    echo "file that must not be committed (such as a \`.env\` file) or an engine"
    echo "version in \`engine.lock\` that is too old to have the \`check\` command."
  fi
  if [ -n "$run_url" ]; then
    echo
    echo "[Run details]($run_url)"
  fi
} >"$out"
