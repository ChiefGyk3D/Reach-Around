<!-- SPDX-License-Identifier: Apache-2.0 -->
# Contributing and the agent workflow

This page says how work happens in this repository, including the part that
surprises people: AI agents wrote much of the code. A human owner sets the
direction, reviews, and decides every merge. Nothing here is a claim about a
hosted service; it is about how the project is built.

Status: pre-release software. Where this page cannot point to a file in the
repository for a claim, it is marked `[OWNER INPUT NEEDED]` and the owner
fills it in before publication.

Contents: [Who writes the code](#who-writes-the-code) ·
[Every task has an issue and a card](#every-task-has-an-issue-and-a-card) ·
[Branches and pull requests](#branches-and-pull-requests) ·
[Agent briefs](#agent-briefs) · [Why a lead coordinates cheaper agents](#why-a-lead-coordinates-cheaper-agents) ·
[The gate named CI green](#the-gate-named-ci-green) ·
[Changelog fragments](#changelog-fragments) · [Commit and PR trailers](#commit-and-pr-trailers) ·
[Security review](#security-review) · [For a human contributor](#for-a-human-contributor) ·
[How to run the checks locally](#how-to-run-the-checks-locally)

## Who writes the code

Most of the code, tests and documentation in this repository were written by
AI agents (Claude models, and in some reviews a second model from another
vendor). The commit history shows it: agent-assisted commits carry a
co-author trailer naming the model (see [trailers](#commit-and-pr-trailers)).

A human owner does the rest of the job: chooses what gets built, writes or
approves the plan, reviews the result, and is the only one who decides a merge.
Agents propose; they do not approve their own work. The owner's account is
the author on the merged commits, which is why the trailer is how you tell
who actually drafted a change.

`[OWNER INPUT NEEDED]` An approximate share of agent-written versus
human-written code, if you want a number on this page. The repository does not
record one.

## Every task has an issue and a card

No work starts without an issue and a card on the project board. The board is
defined as code in `board.yaml` at the repository root (a user-owned GitHub
Project, kept current by the maintainer, with fields for area, epic and
effort). Each issue belongs to an epic, a grouping issue whose work lives in
sub-issues. This page itself is issue #814 under epic #795.

`[OWNER INPUT NEEDED]` The rule that the issue and card must exist before work
starts, and that the card moves in the same step as a dispatch, a PR or a
merge, is the owner's working rule. It is not written down in a repository
file yet, so confirm the wording before publishing it as a promise.

## Branches and pull requests

- Branches are short-lived and named for their purpose, for example
  `docs/814-wiki-contributing`. One branch carries one issue.
- Every pull request links its issue (`Closes #N` or `Refs #N`) and starts as
  a draft. It leaves draft only when the author believes it is ready for
  review.
- Pull requests are merged one at a time, after the gate is green and after a
  check that the branch still merges cleanly.

`[OWNER INPUT NEEDED]` Whether drafts-first and one-at-a-time merging are a
rule contributors must follow or the maintainer's own habit.

## Agent briefs

An agent starts from a written brief, not a chat. A brief names:

- the issue it serves (an `issue: owner/repo#N` line),
- the exact task and the files in scope,
- which checks to run (targeted ones, since CI is the judge),
- what to leave alone, and
- a cleanup duty: the agent removes the worktrees, virtualenvs and temp files
  it created and reports what it removed and what it kept.

The brief is the agent's whole context, so an unclear brief is a bug in the
brief, not in the agent. Agents work in their own git worktree, never in the
owner's main checkout.

`[OWNER INPUT NEEDED]` The brief template is not in this repository. Say
whether you want it published here or kept private.

## Why a lead coordinates cheaper agents

One session acts as the lead, in the role of project manager. It does not
implement and it does not wait. It splits work into independent tasks, writes
the briefs, dispatches implementer and reviewer agents, and checks the
results against the issue. Waiting on CI or a long run is handed to a
background step so the lead stays available.

The reason is cost and focus. The lead keeps the plan and the context. Cheaper
models do the bulk of the typing and the checking, in parallel, each with a
narrow brief. The most expensive model is held back for the judgment calls
that need it. This is a budget decision as much as a quality one, and it
works only because the gate below is automatic and strict: a cheaper agent's
mistake has to fail a check, not slip past a tired reader.

`[OWNER INPUT NEEDED]` Which model tiers are used for which roles, if you want
that stated publicly. The repository records only the co-author trailer.

## The gate named CI green

There is one merge gate, and it is called `CI green`. The repository's CI
(`.github/workflows/ci.yml`) calls the shared reusable workflow `python-ci.yml`
from `ChiefGyk3D/git-your-ship-together`, pinned by commit, and that workflow
reports its result as `CI green` (shown as `ci / CI green`). The Worker job in
`.github/workflows/worker-ci.yml` reports the same name. Repository-specific
jobs, such as the Podman smoke test, stay local to this repository.

What the gate covers, from the CI file: the test suite across Python 3.11,
3.12 and 3.13, `ruff check` and `ruff format --check`, the token-drift check,
the public-assets check, the D1 migration check, the changelog checks, `mypy`,
a container build, and for the Worker, `tsc` and `vitest`.

A pull request merges only when the named check is green on that pull
request's own run, not on an older one.

## Changelog fragments

Every pull request adds exactly one fragment file in `changelog.d/`, named
`<issue-number>-<short-slug>.md`, so two pull requests never edit the same
lines of `CHANGELOG.md`. The fragment is one entry that starts with `- `,
names its issue and epic, and is written in plain language. See
`changelog.d/README.md` for the full format.

Pull requests that change no shipped behavior, such as a docs-only change,
still follow the rule: either add a fragment, or put a line in the pull request
body that reads `no-changelog: <reason>` (or use the `no-changelog` label). CI
checks this (`changelog-assemble.py check-pr`). Do not edit the
`[Unreleased]` section by hand; the release step folds the fragments in.

## Commit and PR trailers

When an agent drafted a commit, the commit message ends with a co-author
trailer naming the model:

```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
```

Merged history shows the same trailer in the lowercase form GitHub rewrites
it to. Pull request descriptions written by an agent end with a line saying
they were generated with Claude Code. The trailers are there on purpose: the
history should say honestly who drafted what.

## Security review

Changes that touch a trust boundary, the click and lead paths, the gate, or
anything that handles untrusted input get an extra review beyond the ordinary
one. In general terms:

- An independent reviewer, separate from the author, checks the change against
  its issue and runs the gate.
- An adversarial review tries to break the change: hostile input, failure
  paths, and tests that pass for the wrong reason.
- A test counts as proof only if it fails when the fix is reverted. The
  reviewer reverts the fix, confirms the test goes red, then restores it.
- The adversarial review is bounded to a fixed number of rounds so it ends.
- A finding rated Critical or High is fixed promptly. `[OWNER INPUT NEEDED]` This is the owner's working rule and is not written in a repository file.

Details of an unfixed weakness stay in the private tracker until the fix has
merged. Public issues say only that hardening is tracked. To report a
vulnerability, see `docs/security/threat-model.md`. `[OWNER INPUT NEEDED]` The public
channel for reporting a vulnerability; the repository has no security policy
file yet.

`[OWNER INPUT NEEDED]` The number of adversarial rounds and the model or
program used for the adversarial tier, if you want them stated. The
repository does not record them.

## For a human contributor

You do not need to use agents, and the project does not require you to. A
human contribution differs in a few ways:

- You open an issue first, or comment on an existing one, so the work has a
  home before it starts. The maintainer adds the board card and epic.
- You write your own brief, which is just the issue and your pull request
  description. There is no agent cleanup duty; clean up your own branch.
- You carry no co-author trailer unless an agent helped you, in which case add
  one that tells the truth.
- You run the local gate before you push (below) and watch `CI green` on your
  pull request.
- The same review applies to you as to an agent: the gate must be green, a
  changelog fragment or `no-changelog` line is present, and the owner decides
  the merge.

`[OWNER INPUT NEEDED]` Whether outside pull requests are being accepted yet
while the project is pre-release, and how you want to be contacted.

## How to run the checks locally

The one command to run before you push is `scripts/gate.sh`. It runs the same
checks CI does, on your machine (from `CONTRIBUTING.md`):

```sh
scripts/gate.sh             # full: pytest, ruff check, ruff format --check, mypy src,
                            #       the schema check, and the Worker (tsc, vitest)
scripts/gate.sh --fast      # only what your changes against origin/main touch
scripts/gate.sh --visual    # full, plus the browser layout tests (--run-visual)
scripts/gate.sh --plan      # print the steps for the chosen mode and run none
```

Run `git fetch origin` first so `--fast` can diff against `origin/main`. Read
the script's own exit code; do not pipe it into another command and test that:

```sh
scripts/gate.sh > gate.log 2>&1; echo "gate exit=$?"
```

Install the development requirements with:

```sh
pip install --require-hashes -r requirements-dev.txt
```

The local gate is a convenience, not a replacement for CI. CI also runs the
Python version matrix, the container builds, the workflow lint and the
token-drift check, and `CI green` is what gates the merge. Run the changelog
check yourself with:

```sh
python3 -I scripts/changelog-assemble.py verify
```

For the full gate contents and the `--fast` table, read `CONTRIBUTING.md`.
