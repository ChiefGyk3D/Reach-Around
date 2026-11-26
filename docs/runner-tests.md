<!-- SPDX-License-Identifier: Apache-2.0 -->
# Running the full test suite outside GitHub Actions

Issue #901. This page lists what a machine needs to run the whole suite
(`pytest tests --run-visual`) and records the failures a full run on a plain container
runner gave on 2026-11-26, with the cause and the fix for each group. GitHub Actions is
the judge for pull requests; this page is for anyone who runs the suite somewhere else
(a self-hosted container runner, a laptop, a clean VM).

## What the suite needs

| Need | Used by | Notes |
|---|---|---|
| Python 3.13 and the `requirements-dev.txt` packages | everything | Playwright's Python package is in the dev requirements |
| `jq` on `PATH` | `scripts/read-lock.sh` and `deploy-local.sh` in `examples/instance-repo/`, the `sites` check in the deploy workflow, `.github/scripts/purge-cache.sh` | GitHub-hosted Ubuntu runners have it. A script that finds no `jq` fails closed on purpose (`jq is required`), so the tests that run those scripts fail loudly instead of passing unchecked |
| `curl`, `git`, `openssl`, `bash` | purge, deploy and instance-repo script tests | A real `git` binary, not a stub |
| A git checkout with its history metadata (a `.git` directory) | `tests/test_edge_settings.py` (lists tracked files with `git ls-files`), `tests/test_instance_repo_template.py` (reads the engine workflow at the commit pinned in `engine.lock`) | A source tree copied without `.git` cannot run these |
| The pinned engine commit reachable from that checkout | `tests/test_instance_repo_template.py` | Fetched once while the network is on. Under `CI=1` a missing commit fails the test on purpose (the check must not pass unchecked); without `CI` it skips with a message |
| A Chromium binary and `--run-visual` | everything marked `visual` | Set `RA_CHROMIUM` to the binary, or run `playwright install chromium`. CI does not pass `--run-visual`, so these tests are only exercised when someone runs them |
| `worker/node_modules` (`cd worker && npm ci --ignore-scripts`) and Node 22.12 or newer on `PATH` | none: `scripts/check-d1-migration-rollback.py` (#907) is a hand-run script, NOT part of pytest or CI | Runs the pinned Wrangler with `--local` against a disposable D1 database in a temp directory (no account, token or network) and checks that a failing migration file is rolled back whole. It is not a test on purpose: the test guard forbids any test from starting Wrangler, and that guard is left unchanged. `tests/test_d1_rollback_script.py` only reads the script (it exists, does nothing at import, refuses under a CI marker, never names the remote flag, drops the same triggers as migration 0022) and needs neither dependency. See "Migration rollback check" in [operations.md](operations.md) |
| `tofu` (OpenTofu) | none of the tests above need it today | Listed because the edge module tests read the files, not the binary |

## Failures found on 2026-11-26

A full uncapped run on a container runner gave 98 failures and about 15,000 passes
(one more failure in `tests/site/test_profile_card_layout.py` is a flaky test tracked
separately in issue #900 and is not part of this table). The runner had no `jq` and its
job directory held a copy of the working tree without `.git`; it also set `CI=1`.

| Files (failures) | Cause | Kind | Fix |
|---|---|---|---|
| `tests/test_deploy_ordering.py` (52), `tests/test_deploy_worker_workflow.py` (5), `tests/test_instance_repo_template.py` (6 of 12) | `jq` is not installed, so the lock reader and the `sites` check refuse to run (`engine.release.json does not match engine.lock: jq is required`, `SITES is not a JSON object`) | Environment | Add `jq` to the runner image |
| `tests/test_purge_script.py` (5) | The purge script parses the Cloudflare answer with `jq -e` and fails closed without it; the stub `curl` in the test answered correctly, so every call was reported as failed (`curl exit 0; HTTP none`) | Environment | Add `jq` to the runner image |
| `tests/test_edge_settings.py` (3), `tests/test_instance_repo_template.py` (6 of 12) | The job directory is not a git repository (`git -C /job/src ls-files` exits 128), and the engine commit from `engine.lock` is not available (`CI=1` makes that a failure) | Environment | In the runner's dependency phase (network on), run `git init`, `git add -A` and `git fetch --depth=1 <repository> <engine commit>` in the source copy |
| `tests/site/test_responsive.py` (7), `tests/site/test_device_qa_visual.py` (7) | The tests predate #337: they counted distinct left edges as columns, or expected an odd last card to span the row. The page now keeps one column width and centers that card. `test_orphan_centering.py` already pins the new behavior, so the old checks contradicted it | Test out of date | Count the most elements in one row; assert a lone card is centered at one column width and never spans |
| `tests/site/test_layout.py` (2), `tests/site/test_profile_card_layout.py` (1) | The tests build the page from `examples/instance/site.yaml` and merged its brand theme (explicit colors, square buttons) into the checks for the default palettes, so the dark page was `#0F0F0F` instead of `#0E0E0E`, buttons were square, and the light variant kept dark surfaces | Test not hermetic | The tests replace the example theme with the default one instead of merging it |
| `tests/site/test_platforms_visual.py` (1) | The example's follower counts are dated 2026-01-01; against the test's fixed day they are older than the stale limit, so the visible number is correctly not drawn | Test not hermetic | The test re-dates the counts to the day before its fixed day |
| `tests/site/test_crypto_visual.py` (1), `tests/site/test_email_visual.py` (1) | The tab icon is fetched after the load event, and the test began counting requests right after `goto`, so it counted the icon as a request made by the copy button | Test race | Wait for the network to go idle before the counter starts; the assertion (no request on copy) is unchanged |
| `tests/site/test_smart_stack_compact_visual.py` (1) | Passed when its file was run alone on the runner and failed in the full run | Not reproduced | Not changed here. If it comes back, open an issue with the full run's output |

## Image and runner fixes

These cannot be made from the repository. Each is one line for whoever maintains the
runner image or its job script.

1. Install `jq`: for a Debian or Ubuntu based image, `apt-get install -y --no-install-recommends jq`.
2. In the job script's dependency phase (network on), in the source copy: `git init -q . && git add -A`, then
   `git remote add origin <this repository's URL>` and
   `git fetch --depth=1 origin "$(sed -n 's/^ENGINE_REF=\([0-9a-f]\{40\}\) .*/\1/p' examples/instance-repo/engine.lock)"`.
   The alternative for the engine commit is to leave `CI` unset on the runner, which makes
   that check skip with its message instead of failing; the three
   `tests/test_edge_settings.py` tests still need the `git init` and `git add`.

## Checking a runner

Run the suite groups above in one go, after the fixes:

```
pytest --run-visual tests/test_deploy_ordering.py tests/test_deploy_worker_workflow.py \
  tests/test_instance_repo_template.py tests/test_purge_script.py tests/test_edge_settings.py \
  tests/site/test_responsive.py tests/site/test_device_qa_visual.py tests/site/test_layout.py \
  tests/site/test_profile_card_layout.py tests/site/test_platforms_visual.py \
  tests/site/test_crypto_visual.py tests/site/test_email_visual.py -q
```

A failure that says `jq is required`, `not a git repository` or `engine commit ... is not
in this checkout` is a runner problem, not a code problem.
