# Contributing

## The local gate

`scripts/gate.sh` is the one command to run before you push. It runs the same
checks CI does, on your machine.

```sh
scripts/gate.sh             # full: pytest, ruff check, ruff format --check, mypy src,
                            #       the schema check, and the Worker (tsc, vitest)
scripts/gate.sh --fast      # only what your changes against origin/main touch
scripts/gate.sh --visual    # full, plus the browser layout tests (--run-visual)
scripts/gate.sh --plan      # print the steps for the chosen mode and run none
```

Each step prints its own exit code. The last line is one summary
(`gate: PASS n step(s) passed` or `gate: FAIL ...`), and the script exits
non-zero if any step failed. Check the exit code of the script itself; do not
pipe it into `grep` or `tail` and test that.

```sh
scripts/gate.sh > gate.log 2>&1; echo "gate exit=$?"
```

What `--fast` runs:

| Changed files | Steps |
| --- | --- |
| `src/reach_around/*.py` | ruff, mypy, tests whose name contains the module name (the whole suite if none), the schema check |
| `tests/test_*.py` | ruff, mypy, those tests |
| `worker/*` (not `.md`) | `npm ci` if `node_modules` is missing, `tsc --noEmit`, `vitest run` |
| `docs/site.schema.json` | the schema check |
| Markdown, `docs/` prose, workflows | nothing heavy |
| anything else (assets, fixtures, `pyproject.toml`, test helpers) | the whole Python suite and the schema check |

`--fast` diffs against `origin/main` (set `GATE_BASE` to use another ref), so run
`git fetch origin` first. It is a speed-up, not a replacement: the full run is
what to trust before you ask for a merge.

Tools come from the virtualenv in `GATE_VENV` when set, else `venv-220` under
`~/.local/share/reach-around-dev` when it exists, else from `PATH`. Install the
dev requirements with `pip install --require-hashes -r requirements-dev.txt`.

The script itself is held to `shellcheck` and `shfmt -d -i 4 -ci`.

## CI

CI calls the shared reusable workflows from
`ChiefGyk3D/git-your-ship-together` (`python-ci.yml`), plus the repo-specific
Podman job and the Worker job. The gate script is a local convenience; it does
not replace those, and CI does not call it, so the merge gate (`CI green`) keeps
its exact coverage (the Python version matrix, the container builds, the
workflow lint, and the token-drift check run there).
