#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# One local gate for the repo. Run it before you push.
#
#   scripts/gate.sh                full gate: pytest, ruff, mypy, schema, worker
#   scripts/gate.sh --fast         only what the changes against origin/main touch
#   scripts/gate.sh --visual       also run the browser layout tests (--run-visual)
#   scripts/gate.sh --plan         print the steps that would run, run none
#
# Every step prints its exit code; the last line is one summary and the script
# exits non-zero if any step failed. Output is never filtered, so an exit code
# is always the tool's own.
#
# Environment:
#   GATE_BASE   ref the fast mode diffs against (default origin/main)
#   GATE_VENV   a virtualenv directory to take python, ruff and mypy from
#               (default: venv-220 under ~/.local/share/reach-around-dev when it
#               exists, else whatever is on PATH)
#   GATE_ROOT   repository root (default: the checkout this script lives in)

set -u

internal=""
if [ "${1:-}" = "--internal" ]; then
    # Re-entry point so a step can be a shell function run under `step`.
    internal="${2:-}"
    shift 2
fi

fast=0
visual=0
plan=0
for arg in "$@"; do
    case "$arg" in
        --fast) fast=1 ;;
        --visual) visual=1 ;;
        --plan) plan=1 ;;
        -h | --help)
            sed -n '4,22p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "gate: unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

root="${GATE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$root" || exit 2

venv="${GATE_VENV:-}"
if [ -z "$venv" ] && [ -x "$HOME/.local/share/reach-around-dev/venv-220/bin/python" ]; then
    venv="$HOME/.local/share/reach-around-dev/venv-220"
fi
tool() {
    if [ -n "$venv" ] && [ -x "$venv/bin/$1" ]; then
        printf '%s\n' "$venv/bin/$1"
    else
        printf '%s\n' "$1"
    fi
}
py="$(tool python)"
ruff="$(tool ruff)"
mypy="$(tool mypy)"
export PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1

failed=()
ran=0

# step NAME CMD...: run CMD, print its exit code, remember a failure.
step() {
    local name="$1" rc
    shift
    if [ "$plan" -eq 1 ]; then
        echo "plan: $name: $*"
        return 0
    fi
    echo "== $name: $*"
    "$@"
    rc=$?
    ran=$((ran + 1))
    echo "-- $name: exit=$rc"
    if [ "$rc" -ne 0 ]; then
        failed+=("$name")
    fi
}

# schema_check: the committed schema must equal what the config models print.
schema_check() {
    local tmp rc
    tmp="$(mktemp)" || return 2
    "$py" -c 'import sys; from reach_around.cli import main; sys.exit(main(sys.argv[1:]))' \
        schema --out "$tmp" || {
        rm -f "$tmp"
        return 2
    }
    cmp "$tmp" docs/site.schema.json
    rc=$?
    rm -f "$tmp"
    if [ "$rc" -ne 0 ]; then
        echo "docs/site.schema.json is stale: reach-around schema --out docs/site.schema.json" >&2
    fi
    return "$rc"
}

# worker_install: npm ci only when node_modules is missing.
worker_install() {
    (cd worker && { [ -d node_modules ] || npm ci; })
}
worker_tsc() { (cd worker && npx tsc --noEmit); }
worker_vitest() { (cd worker && npx vitest run); }

case "$internal" in
    schema)
        schema_check
        exit $?
        ;;
    worker_install)
        worker_install
        exit $?
        ;;
    worker_tsc)
        worker_tsc
        exit $?
        ;;
    worker_vitest)
        worker_vitest
        exit $?
        ;;
    "") ;;
    *)
        echo "gate: unknown internal step: $internal" >&2
        exit 2
        ;;
esac

pytest_args=(-m pytest -q -p no:cacheprovider)
if [ "$visual" -eq 1 ]; then
    pytest_args+=(--run-visual)
fi

worker_steps() {
    if [ ! -d worker ]; then
        echo "-- worker: skipped (no worker/ directory)"
        return 0
    fi
    if [ ! -d worker/node_modules ] || [ "$plan" -eq 1 ]; then
        step "worker install" bash "$0" --internal worker_install
    fi
    step "worker tsc" bash "$0" --internal worker_tsc
    step "worker vitest" bash "$0" --internal worker_vitest
}

if [ "$fast" -eq 0 ]; then
    step "pytest" "$py" "${pytest_args[@]}"
    step "ruff check" "$ruff" check .
    step "ruff format" "$ruff" format --check .
    step "mypy" "$mypy"
    step "schema" bash "$0" --internal schema
    worker_steps
else
    base="${GATE_BASE:-origin/main}"
    if ! git rev-parse --verify --quiet "$base^{commit}" >/dev/null; then
        echo "gate: base ref not found: $base (git fetch origin, or set GATE_BASE)" >&2
        exit 2
    fi
    # Committed, staged and unstaged changes against the base, plus new files.
    mapfile -t changed < <(
        {
            git diff --name-only --diff-filter=d "$base" --
            git ls-files --others --exclude-standard
        } | sort -u
    )
    py_files=()
    test_files=()
    mypy_files=()
    need_full_pytest=0
    need_schema=0
    need_worker=0
    heavy=0
    for f in "${changed[@]}"; do
        [ -n "$f" ] || continue
        case "$f" in
            worker/*.md) ;;
            worker/*)
                need_worker=1
                heavy=1
                ;;
            *.md | LICENSE | docs/assets/* | docs/education/* | docs/research/* | docs/superpowers/* | docs/security/*) ;;
            docs/site.schema.json)
                need_schema=1
                heavy=1
                ;;
            .github/workflows/*) ;;
            tests/test_*.py)
                py_files+=("$f")
                mypy_files+=("$f")
                test_files+=("$f")
                heavy=1
                ;;
            src/reach_around/*.py)
                py_files+=("$f")
                mypy_files+=("$f")
                need_schema=1
                heavy=1
                mod="$(basename "$f" .py)"
                related=()
                for t in tests/test_*"$mod"*.py; do
                    [ -e "$t" ] && related+=("$t")
                done
                if [ "${#related[@]}" -eq 0 ]; then
                    need_full_pytest=1
                else
                    test_files+=("${related[@]}")
                fi
                ;;
            scripts/*.py | .github/scripts/*.py)
                py_files+=("$f")
                heavy=1
                ;;
            *)
                # Anything else (packaged assets, fixtures, pyproject, helpers
                # under tests/) can change any test: run them all.
                case "$f" in *.py) py_files+=("$f") ;; esac
                case "$f" in tests/*.py | src/*.py) mypy_files+=("$f") ;; esac
                need_full_pytest=1
                need_schema=1
                heavy=1
                ;;
        esac
    done
    echo "gate: fast mode against $base: ${#changed[@]} changed file(s)"
    if [ "$heavy" -eq 0 ]; then
        echo "-- nothing heavy to run (docs, workflow or comment-level changes only)"
    fi
    if [ "${#py_files[@]}" -gt 0 ]; then
        step "ruff check" "$ruff" check "${py_files[@]}"
        step "ruff format" "$ruff" format --check "${py_files[@]}"
    fi
    if [ "${#mypy_files[@]}" -gt 0 ]; then
        step "mypy" "$mypy" "${mypy_files[@]}"
    fi
    if [ "$need_full_pytest" -eq 1 ]; then
        step "pytest" "$py" "${pytest_args[@]}"
    elif [ "${#test_files[@]}" -gt 0 ]; then
        mapfile -t uniq_tests < <(printf '%s\n' "${test_files[@]}" | sort -u)
        step "pytest" "$py" "${pytest_args[@]}" "${uniq_tests[@]}"
    fi
    if [ "$need_schema" -eq 1 ]; then
        step "schema" bash "$0" --internal schema
    fi
    if [ "$need_worker" -eq 1 ]; then
        worker_steps
    fi
fi

if [ "$plan" -eq 1 ]; then
    exit 0
fi
if [ "${#failed[@]}" -gt 0 ]; then
    echo "gate: FAIL ${#failed[@]} of $ran step(s) failed: ${failed[*]}"
    exit 1
fi
echo "gate: PASS $ran step(s) passed"
exit 0
