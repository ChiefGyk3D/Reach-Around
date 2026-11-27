# My Reach Around instance

This is the template for a **private instance repository**: the one place that
holds your own page (config, images, overrides) and deploys it. The engine
that builds the page is the public [Reach Around](https://github.com/ChiefGyk3D/Reach-Around)
project; this repository only pins which version of it to use.

The model, in one line: the engine is public and versioned, your instance is
private and consumes a pinned release. Read `docs/instance-repos.md` in the
engine repository for the reasoning.

## What is in here

| Path | What it is |
|---|---|
| `site.yaml` | Your page. Edit this. |
| `media/` | Your images (avatar, favicon, background). |
| `overrides/` | Optional. Template and `custom.css` overrides; create it when you need it. |
| `engine.lock` | The pinned engine: container image by digest, plus the matching source commit for the Worker. |
| `engine.release.json` | The image, repository, commit, tag and D1 schema of that one release. `scripts/read-lock.sh --release` fails when it disagrees with `engine.lock`; update both in the same pull request (Renovate moves only the lock, so a Renovate pull request fails this check until the manifest is updated). |
| `.github/workflows/preview.yml` | On a pull request: check the config, build a preview artifact, and comment on the pull request with the result in plain language and the list of changed blocks. Also lints the scripts and workflows (GYST `bash-ci`). Deploys nothing. Its gate job is named `CI green`. |
| `.github/workflows/deploy.yml` | On a push to `main` or a manual run: check and validate always; deploy staging only while `ALLOW_STAGING_DEPLOY` is exactly `true`; production only by a manual run with target `production` while `ALLOW_PRODUCTION_DEPLOY` is exactly `true`. Each deploy waits for its environment's reviewer. |
| `.github/workflows/refresh-counts.yml` | Once a day (and by hand): refresh follower counts with the pinned engine and open a pull request with the new `numbers.json` (and `numbers.fediverse.json`, when `numbers.fediverse` sources are set). Optional; see "Daily follower counts". |
| `.github/workflows/check-hand-kept-freshness.yml` | Once a week (and by hand): check `hand-kept.json` and keep one issue open while a number you keep by hand is past its limit. Optional; see "Hand-kept numbers". |
| `.github/workflows/history-guard.yml` | On a pull request: fail any change to `history/social.jsonl` that is not an append, except a labeled tombstone-only rewrite. Optional; see "History rewrites". Its check is named `History guard`. |
| `.github/history-approvers` | The GitHub logins allowed to authorize such a rewrite. Ships empty. |
| `.github/CODEOWNERS` | Example owners of the paths that decide a rewrite (the primary boundary with code-owner review). Replace `OWNER-LOGIN`; see "History rewrites". |
| `renovate.json` | Keeps `engine.lock` and the Actions pins current. |
| `scripts/` | `check-freshness.py` (the stale check of the hand-kept file, standard library only), `check-artifact.py` (the exact file set the publish job accepts from the artifact: regular files only, no links, no extra names), `validate-numbers.py` (the strict check on `numbers.json`, and with `--fediverse` on `numbers.fediverse.json`, that the counts refresh runs twice), `refresh-counts.sh`, `verify-history.sh` (the engine's `counts history-verify` on the history file, offline), `verify-history-rewrite.sh` (the engine's `counts history-compare`: append-only or a valid tombstone-only rewrite), `history-guard.py` (the pull-request guard that decides whether a rewrite is authorized, standard library only), `check-config.sh` (the plain-language config check), `validate.sh` (the build CI runs), `write-result.py` (bundles the preview job's results into the one data file the comment job reads), `render-comment.py` (run by the comment job from the base commit: validates that file and writes the pull request comment from fixed text), `check-repo.sh`, `read-lock.sh`, `deploy-local.sh`. |
| `docs/EDITING.md` | How to change the page without touching code. |

## Adopt it

1. Create a new **private** repository on GitHub. Do not fork the engine: a
   fork of a public repository cannot be made private, and you do not need the
   engine's code here.
2. Copy this folder's contents into it, including the hidden `.github` and
   `.gitignore`:

   ```
   git clone https://github.com/ChiefGyk3D/Reach-Around.git
   git clone git@github.com:YOUR-NAME/YOUR-INSTANCE.git
   cp -r Reach-Around/examples/instance-repo/. YOUR-INSTANCE/
   cd YOUR-INSTANCE
   ```

3. Edit `site.yaml`. Check it builds before you commit (needs Docker or Podman):

   ```
   scripts/validate.sh --out out
   ```

   The link manifest is written to a separate temporary file, never into the
   output folder (pass `--manifest FILE` to keep it; the file must be outside
   `--out`).

   **Needs engine v0.2.0 or later.** `--manifest` and `reach-around check` first shipped
   in v0.2.0, and `engine.lock` pins that release. If you pin an older image,
   `validate.sh` and the deploy workflow fail with a message saying so.

   Published files under `static/` and your images carry a content hash in
   their names (`site.3fa9c1d2ab4e5f607182.css`), and the built `_headers` caches them for
   a year while pages are revalidated, so a change shows at once. The engine
   documents this, and an optional cache purge, in `docs/deploy.md` under
   "Caching and stale files". This template does not run the purge step.

4. Do not push to `main` yet. Finish the one-time setup below first, in its
   order: create and protect both environments, then add the variables, and set
   the variable that switches staging on last. GitHub creates a missing
   environment with no protection the first time a job names it, so the
   environments must exist, with their reviewers and the `main`-only branch
   rule, before any run can start. Until the staging switch is set, a push runs
   only the `validate` job (which holds no environment and no secret). Then
   commit and push. Production never runs on a push.

## One-time setup

The Cloudflare side (Pages project with Direct Upload, D1 database, API token
scopes, production branch) is described step by step in the engine's
`docs/deploy.md`. Follow its sections 1 and "Token type", using your own
names. Every name here is a placeholder.

**Step 1: the environments, before anything else.** On GitHub:
**Settings > Environments > New environment**, twice: `staging` and
`production`. For each one:

- Turn on **Required reviewers** and name at least one reviewer who is a
  different human from the person who will start deployments. Then turn on
  **Prevent self-review** (the setting is under the required reviewers). Every
  deploy then waits for that reviewer's approval before the job runs.
- Under **Deployment branches and tags**, allow only `main`.
- Add no secret. The token comes only from the secret manager, over OIDC.

**What environment protection can and cannot guarantee.** It is the real gate
on the branch and on who approves: GitHub enforces the reviewer and the
deployment branch rule, and a workflow file cannot override them. **Prevent
self-review** only stops the person who started a deployment from approving that
same deployment. Environment protection and **Prevent self-review** do not
establish that the approver did not author the commit being deployed: one person
can write and push the code, a different login can start the deployment, and the
first login can approve it. A login is not a person, so a second login you
control is not independent control either. Independent approval needs a
second human. If you are the sole owner, there is no independent approval to
configure: treat the approval as a confirmation pause (a moment to look before
the job runs), never as separation of duties, and do not describe it as one. If
you must be able to say "the approver did not author the change", enforce it
outside the environment: protected-branch pull-request review on `main` (a
required approving review from someone other than the author) or an external
policy, in addition to the environment approval. Also check what your GitHub
plan offers for environments on a private repository, and whether an
administrator can bypass the rules there (look at each environment's settings;
this README does not assume either). The workflow's own `main` checks, and the
variables `ALLOW_STAGING_DEPLOY` and `ALLOW_PRODUCTION_DEPLOY`, are operational
switches that fail closed: they are not an approval control, and anyone who can
edit repository variables can flip them. The workflow cannot check that an
environment is protected, because that needs an administrator token that no job
here holds.

**Step 2: the repository variables.** Add these **repository variables**
(**Settings > Secrets and variables > Actions > Variables**) only after both
environments are protected. They are not secret, but keep real values out of
commits and issues. They are repository level, not environment level, because
the Worker call is made by a job that holds no environment and so cannot read an
environment's own variables. Set `ALLOW_STAGING_DEPLOY` last: it is what lets a
push start the first run.

| Variable | Used by | Example (placeholder) |
|---|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | both tiers | `00000000000000000000000000000000` |
| `DOPPLER_PROJECT` | both tiers | `my-links` |
| `STAGING_WORKER_NAME` | staging | `my-links-clicks-staging` |
| `STAGING_PAGES_PROJECT` | staging | `my-links-staging` |
| `STAGING_D1_DATABASE_ID` | staging | `00000000-0000-0000-0000-000000000000` |
| `STAGING_RATELIMIT_NAMESPACE_ID` | staging | `1001` |
| `STAGING_SITE_HOST` | staging | `staging.links.example.invalid` |
| `STAGING_ZONE_NAME` (optional) | staging | `example.invalid` |
| `DOPPLER_IDENTITY_ID_STAGING` | staging | an identity id (not a secret) |
| `ALLOW_STAGING_DEPLOY` | staging | absent by default; exactly `true` lets a push (or a manual run) deploy staging; set it last |
| `PRODUCTION_WORKER_NAME` | production | `my-links-clicks` |
| `PRODUCTION_PAGES_PROJECT` | production | `my-links` |
| `PRODUCTION_D1_DATABASE_ID` | production | `00000000-0000-0000-0000-000000000000` |
| `PRODUCTION_RATELIMIT_NAMESPACE_ID` | production | `1001` |
| `PRODUCTION_SITE_HOST` | production | `links.example.invalid` |
| `PRODUCTION_ZONE_NAME` (optional) | production | `example.invalid` |
| `DOPPLER_IDENTITY_ID_PRODUCTION` | production | a different identity id |
| `ALLOW_PRODUCTION_DEPLOY` | production | absent by default; exactly `true` turns production on |

  The `validate` job checks these values once, with `scripts/check-tiers.sh`,
  and the four deploy jobs read only the values it checked (its job outputs),
  never the variables again. Staging settings are required (the zone name is
  optional). Production settings are required together as soon as any one is set
  or `ALLOW_PRODUCTION_DEPLOY` is `true`. Staging and production settings must
  differ after normalization: names are lowercased, a host or zone loses one
  trailing dot and a default port, a name with non-ASCII characters must be given
  in its `xn--` form, and a namespace id is digits only and compared as a number
  (`01002` equals `1002`).

  The two tiers must use different Worker names, and neither may be the
  engine's own test Worker (`reach-around-clicks`); the `validate` job fails the
  run otherwise. They must also use different Pages projects (the names are
  compared after lowercasing, and `validate` fails when they are equal), and the
  staging Pages job refuses to publish into the production project. Do not rely
  on the branch name: `--branch staging` makes a preview deployment only when
  the project's production branch is something else, and a project has one
  production branch, so give staging its own Pages project and check the
  production branch of each project in Cloudflare (the engine's `docs/deploy.md`,
  "Production branch"). The placeholder ids (`1001`, the all-zero database id) are
  refused by the called workflow, so each tier needs its own real D1 database and
  rate limit namespace.

  Unlike the engine's own test workflow, there is no `LINK_IDS` variable: the
  workflow reads the link ids from the build manifest, so the Worker and the page
  cannot disagree.

**To deploy.** With `ALLOW_STAGING_DEPLOY` set to `true`, a push to `main`
deploys staging. To run it by hand, open **Actions > Deploy > Run workflow** on
`main` and pick the target. Production runs only from a manual run on `main` with
target `production` while `ALLOW_PRODUCTION_DEPLOY` is exactly `true`; set it
when you are ready and remove it again to turn production off. Remove
`ALLOW_STAGING_DEPLOY` to stop staging. Moving `engine.lock` moves the two reusable
`uses:` pins in `deploy.yml` with it (Renovate does both in one pull request, and
a test fails if they differ).

Also turn on **Settings > Code security > Secret scanning** and push
protection, and protect `main` (require the `CI green` check from the `Preview` workflow, no
force pushes). The `Preview` workflow calls a reusable workflow from the
public `ChiefGyk3D/git-your-ship-together` repository; if your organization
restricts which actions may run, allow that repository.

The preview comment needs no setup: the `comment` job uses the run's own
token with `pull-requests: write` (and `contents: read` to fetch one script
from the base commit) and nothing else. Pull requests from forks get no
preview and no comment: the `preview` and `comment` jobs are skipped for them
(a guard on `head.repo.full_name`), and their token cannot write anyway.

**The comment marker is a convention, not proof.** The comment starts with a
marker naming this repository and workflow, and the job updates only a comment
that carries it and was written by the Actions bot, and refuses to post if more
than one matches. The marker distinguishes this comment by convention: any other
workflow in the repository with `pull-requests: write` could post one that
looks the same. Treat who may change `.github/` as the real control (below).

**Who may change the workflow.** With the `pull_request` trigger GitHub runs
the workflow file from the pull request itself, so a collaborator with write
access can edit `.github/workflows/preview.yml` and the scripts in their own
pull request. The template stops a pull request from forging the comment (the
comment is rendered by a script from the base commit, from validated data) and
from choosing the engine image of its own preview (the image comes from the base
branch's `engine.lock`), but it cannot stop someone with write access from
changing the workflow itself. So: give write access only to people you trust,
and if the repository has collaborators protect `.github/` and `scripts/` with
a `CODEOWNERS` file and a branch rule that requires a code owner's review:

```
# .github/CODEOWNERS (replace the handle with a person or team you trust)
/.github/   @your-handle
/scripts/   @your-handle
/engine.lock @your-handle
```

**`engine.lock` and previews.** A preview uses the `engine.lock` on the base
branch, not the one in the pull request. A change to `engine.lock` takes effect
for previews only after it is merged. (The merge itself is still checked: the
pull request's own `Preview` run builds with the old engine.)

## Hand-kept numbers

Optional. Numbers for a platform with no collector live in `hand-kept.json` (see
the engine's `docs/kit.md`, "Hand-kept entries and imports"; the engine's
`kit measure add` and `kit measure import-youtube` write it). The workflow
`.github/workflows/check-hand-kept-freshness.yml` runs `scripts/check-freshness.py`
every Monday at 07:41 UTC and from the **Actions** tab. For each platform and key
it judges the newest entry against a 30 day limit. While any number is older it
keeps one open issue, "Hand-kept numbers are out of date", that lists platform
names, keys and ages (never a value); when all are fresh it closes the issue.

It uses no secret and no Cloudflare token, runs only on `main`, and its token
can write issues and nothing else. If you keep nothing by hand, delete the
workflow and the script. There is no Worker route for this.

## Daily follower counts

Optional. If `site.yaml` has a `numbers` block (the engine's `docs/config.md`
lists each source), `.github/workflows/refresh-counts.yml` runs
`reach-around counts refresh` every day at 07:17 UTC, and you can run it from
the **Actions** tab (**Run workflow**). It runs the engine image pinned in
`engine.lock` (by digest, shape-checked by `scripts/read-lock.sh`), through
`scripts/refresh-counts.sh`. Without a `numbers` block the run fails at the
config check with a plain-language message; delete the workflow if you do not
want counts.

**What happens to the result.** The workflow does not push to `main`. It
pushes the branch `counts/refresh` and opens one pull request, "Refresh
follower counts", that changes `numbers.json` (and `numbers.fediverse.json`, when `numbers.fediverse` sources are set, and `history/social.jsonl`, when `numbers.history_file` is set to that path). You merge it; that push runs
`Deploy` as usual, with its reviewer approval. Reasons: a push made with the
run's own token does not start other workflows (so a direct commit would never
deploy), and `main` should stay protected. Because of the same rule, the
`Preview` check does not start on that pull request by itself; close and reopen
it to run `Preview` if your branch rule requires `CI green`. If nothing
changed, no pull request is opened.

**Settings > Actions > General > Workflow permissions:** tick "Allow GitHub
Actions to create and approve pull requests", or the pull request step fails.

**Secrets.** Add them under **Settings > Secrets and variables > Actions**
(repository secrets; this workflow has no environment). Add only the sources
you use; a source whose secret is not set is skipped, not failed.

| Secret | Source | Where it comes from |
|---|---|---|
| `YOUTUBE_API_KEY` | `youtube` | A YouTube Data API v3 key (Google Cloud console), restricted to that API. |
| `YOUTUBE_USERNAME` | `youtube` | Optional. The channel handle (with or without `@`), read only when the source sets `handle_env: YOUTUBE_USERNAME` instead of `channel_id`. |
| `TWITCH_CLIENT_ID` | `twitch` | The client id of an app in the Twitch developer console. |
| `TWITCH_CLIENT_SECRET` | `twitch` | The client secret of the same app. |

**Where the keys come from.** This template reads them from GitHub Actions
secrets, the default that needs nothing else. The engine's own test deployment
(`.github/workflows/deploy-test.yml`, documented in the engine's
`docs/deploy.md`, "Daily follower counts (test deployment)") can also fetch them
from Doppler over GitHub OIDC, with no long-lived token, when you set the
repository variables `DOPPLER_COUNTS_IDENTITY_ID` (an identity made for the
counts job alone, not the deploy job's `DOPPLER_IDENTITY_ID`),
`DOPPLER_COUNTS_PROJECT` and `DOPPLER_COUNTS_CONFIG` (a dedicated project for
social-media credentials; no default). With those unset it falls back to GitHub secrets. In both cases the
keys reach one step only.

Sources that need no key (Bluesky, Mastodon-compatible servers, GitHub, Discord
invites, Matrix) need no secret. The names are fixed by the workflow, so in
`site.yaml` set `api_key_env: YOUTUBE_API_KEY`, `client_id_env: TWITCH_CLIENT_ID`
and `client_secret_env: TWITCH_CLIENT_SECRET`. The Twitch follower total needs a
broadcaster user token in `TWITCH_USER_ACCESS_TOKEN` (see `docs/twitch-source.md`
in the engine repo); without it that source is reported as skipped
(`needs_user_token`) and the number stays hand-kept in `numbers.json`.

**Safety.**

- The keys are set on one step only (the one that runs the engine), through
  `env`, and are passed into the container by name. They are not in workflow
  or job `env`, not in a `run:` script, not printed; the engine never prints or
  writes them.
- The engine is allowed to reach the network (it has to), with a read-only root
  file system, no capabilities, and only `site.yaml`, `media/` and `overrides/`
  mounted read-only.
- The suspect-value guard is always on. A result of zero over a good number, or
  a fall of more than 90 percent from 1000 or more, is kept out and that source
  is marked failed. The workflow never passes the option that accepts such a
  value. If the change is real, run the engine once by hand with it (see
  `docs/customizing.md` in the engine repository), or edit `numbers.json`.
- One failed source never stops the others: the good numbers are proposed
  first, and then the run is marked failed so you notice. Entries you wrote by
  hand for platforms without a source are kept.
- Two jobs keep the keys and the write access apart. The `refresh` job holds
  the keys and can only read (`contents: read`); the `publish` job can write
  (`contents: write`, `pull-requests: write`) and holds no keys. Neither uses
  `pull_request_target`, and both refuse to run from a branch other than
  `main`.
- `numbers.json` is data, not trusted. `scripts/validate-numbers.py` checks it
  before it is copied into the repository and again in the `publish` job after
  it crosses as an artifact: the exact shape the engine writes, known id
  names, whole non-negative counts, real UTC stamps, at most 64 KiB. It writes
  a fresh copy through a temporary file and a rename, never the original bytes.
- `numbers.fediverse.json` (the fediverse snapshots the engine writes beside
  `numbers.json`) is checked the same way, twice, with
  `scripts/validate-numbers.py --fediverse`: the engine's own size limit
  (512 KiB) and key rules, anonymous aggregates only (no account, handle,
  host, link or any person-level key), and the whole file is refused when any
  part fails (`followers` and `posts` are optional, as in the engine; the rest
  is required). An entry older than 30 days, or dated after today, is dropped,
  the kit's own freshness rule, so the last snapshot of a source you removed or
  that keeps failing ages out (the file is then written with no entry); the kit
  also ignores a platform with no configured source at once. Only that file
  crosses. The engine's unit note
  (`.numbers.json.unit`), its `.bad` copy, the pending note and staged copies
  stay in the scratch folder and are never carried or committed.
- The egress check does not apply to this template. `reach-around counts
  egress-check` compares the hosts the sources need (a Mastodon host is one)
  with an `allowed-endpoints` list; the refresh job here has no such list (it
  runs no egress-firewall step), so there is nothing to compare and the check is
  not a step. If you add an egress-firewall step to the refresh job, run
  `reach-around counts egress-check --config site.yaml --allowed FILE` against
  that step's list first, so a configured host is never blocked.
- The branch is replaced only if it is still the commit the run saw
  (`--force-with-lease`). If a pull request is already open on `counts/refresh`
  and it was not opened by the Actions bot, comes from a fork, or changes
  anything besides `numbers.json`, `numbers.fediverse.json` and `history/social.jsonl`, the run stops with a message and touches
  nothing. Close that pull request or rename its branch, then run again.

## History rewrites

The counts history is append-only. `.github/workflows/history-guard.yml` (the
check named `History guard`) runs on every pull request and fails any change to
`history/social.jsonl` that is not a pure append of well-formed lines. The one
exception is a **tombstone-only rewrite**: an owner redaction that replaces old
`api` rows with tombstones (see `docs/social-history.md` in the engine
repository, "Redact stored values (tombstones)").

A rewrite is accepted only when ALL of these hold, and any failure is a red
check that prints fixed words, never a value:

- The head branch is named `redact/<anything>`, in this repository (not a fork),
  and never `counts/refresh`.
- The pull request carries the label `history-tombstone`, and the last label
  event is a `labeled` event applied by a login listed in
  `.github/history-approvers`. The guard reads that file from the base commit,
  so a pull request cannot add its own author. The label and the applier are
  read live from the API, twice, and must agree. The label event must come
  after the head commit last changed, and be at least 24 hours old (a notice
  period, not a second approval). The time the head changed is the later of the
  first workflow run GitHub started for this exact head commit on this pull
  request and the latest forced push; a commit date is never used, because the
  author sets it. If that time cannot be determined the check fails. Pushing a
  new head after the label therefore needs the label removed and applied again,
  and a fresh 24 hours.
- No pull request from the Actions bot is open on `counts/refresh`.
- The change touches only `history/social.jsonl`, optionally `numbers.json`
  (removal of the entry that shows a redacted value, nothing else), and adds
  exactly one `history/redactions/<run-id>.manifest`.
- The engine's `counts history-compare` returns "valid rewrite" for the exact
  bytes: only `api` rows became tombstones (their corrections go with them);
  every other line keeps its content and order; no tombstone was removed or
  turned back into data; later lines are re-chained and canonical; at most 500
  records and 10 percent of the rows; every tombstone field and the manifest are
  derived from the redacted keys, one reason code and two dates, so a redacted
  value has no field to hide in. Make such a file with
  `reach-around counts history-redact ... --confirm --derived`.
- The head contains the current tip of `main` (update the branch if `main`
  moved). A stale `counts/refresh` pull request built before the rewrite no
  longer extends `main` and fails the check.

The label is an intent record and a 24 hour cooling-off signal, not a gate that
GitHub enforces: the workflow reads it, but GitHub does not stop a merge because
a label is missing or was removed after a green run. The compare is the control
for the bytes. A label alone changes nothing, and `hand_kept` and `imported`
rows are never accepted by this route.

**Set it up in this order.** First bump `engine.lock` to a release that has
`counts history-compare` and merge the scripts (`scripts/history-guard.py`,
`scripts/verify-history-rewrite.sh`) and `.github/history-approvers` in one pull
request; the guard reads its scripts from the base commit, so the workflow file
goes in a second pull request. Then set up the code-owner boundary below, require
the check `History guard` and "branch must be up to date before merging" on
`main`, and do not enable a merge queue (the guard does not run under
`merge_group`). Add your login to `.github/history-approvers` only after the
activation checklist below; the file ships empty, which means no rewrite is
accepted.

**The workflow is NOT a sufficient merge gate on its own.** With a
`pull_request` trigger GitHub runs the workflow file of the pull request's merge
ref, so a pull request can replace `history-guard.yml` and keep the required
check name; the guard scripts are pinned to the base commit, but that only
helps after the workflow has run its checkout. The first steps of the job
compare the running copy of the workflow with the base commit's copy and fail on
any difference: that catches accidental drift, and it does not stop a replaced
workflow that leaves the step out.

**The primary boundary is CODEOWNERS plus code-owner review.** The paths that
decide whether a rewrite can merge are `.github/` (the workflow and the
approvers file), `scripts/`, `engine.lock`, `engine.release.json` and
`history/redactions/` (the manifests). Protect them with all of the following,
so a change to the guard or its inputs cannot merge without a review the pull
request's author cannot give. The history file itself (`history/social.jsonl`)
and `numbers.json` are deliberately not owned: the guard decides whether a
rewrite is accepted, so the review must cover the guard, its approvers and the
manifests, and the daily refresh only appends, which is not a rewrite and needs
no human review:

- A `CODEOWNERS` file that covers those paths. A ready file is
  `.github/CODEOWNERS` in this template (replace `OWNER-LOGIN`). GitHub reads it
  from the base branch of the pull request, so a pull request cannot change who
  owns a path for itself. A code owner needs write access.
- The branch rule "Require review from Code Owners", "Dismiss stale pull request
  approvals when new commits are pushed" and "Require approval of the most
  recent reviewable push" (an approval from someone other than the last person
  to push), and the setting "Do not allow bypassing the above settings" so that
  administrators are included.
- The check `History guard`, required, with "branch must be up to date".

Why this is the boundary and the workflow is not: a replaced workflow is a
change under `.github/`, which needs the code-owner approval. The check, the
engine comparison and the label and age gate stay as designed, but they now
protect against mistakes and unreviewed paths, not against a hostile workflow
replacement.

**The ruleset required workflow is NOT AVAILABLE on a Team plan.** The ruleset
rule "Require workflows to pass before merging" (a workflow in a separate
trusted repository that a pull request cannot replace) would be a stronger
boundary, but it is organization or enterprise level and, per GitHub's sources,
only on GitHub Enterprise plans. Sources read 2026-11-26: the page on available
rules for rulesets,
<https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets>
(ruleset workflows "can be configured at the organization or enterprise
level"), and GitHub's changelog of 2023-08-02 (the ability "will only be
available on GitHub Enterprise plans"). Do not build on it unless you have an
Enterprise account; then it is an optional upgrade path, and you must confirm it
fires for your events first (user reports say it does not run on the `labeled`
and `unlabeled` events; UNVERIFIED).

**Who reviews when you are the only person.** GitHub does not let an author
approve their own pull request (the page on approving a pull request, read
2026-11-26: "Pull request authors cannot approve their own pull requests"), so
with one human owner a code-owner review has to come from something other than
the account that opens the pull request. Options, strongest first, each with
what it does and does not protect:

1. A second GitHub account that you control, with write access, named in
   `CODEOWNERS`, with its own credential kept apart from the one your tools and
   agents use (a separate hardware key or passkey, not stored on the machine
   that opens pull requests). Protects against: a mistake or a compromised token
   or automation under your first account merging a change to the guard, its
   inputs or the approvers file without a deliberate second step. Does not
   protect against: you approving without reading, or someone holding both
   credentials. It reduces to your own discipline at the moment you approve. A
   code owner must have write access (verified, CODEOWNERS page read
   2026-11-26). Whether GitHub's terms allow a second login that you operate
   yourself is UNVERIFIED: read them before you rely on it.
2. A GitHub App or bot as the reviewer. UNVERIFIED: the code owners page names
   people and teams with write access, and the branch rules page does not say
   whether an app's approval counts; do not rely on it until you have seen a
   test pull request merge only with that approval. Do not use the Actions
   token to approve: the repository setting for it exists, but whether its
   approval counts toward required reviews is UNVERIFIED.
3. A team. A team that contains only you does not help: you are still the
   author, and the author's approval is not allowed. A team helps only when it
   contains an account other than the one that opens the pull request, which is
   option 1 in another form.
4. Administrator bypass (leave "Do not allow bypassing" off and merge as the
   owner). Protects against nothing but an accidental merge: the owner can
   merge without the review (verified, approving page: owners and administrators
   can merge without an approving review). This is not a boundary. It reduces
   entirely to your discipline and to the audit log you read afterwards.

Recommendation: option 1, with the bypass setting on (administrators included).
With one person, the 24 hour cooling-off after the label and the detective
record in the job summary add time to think and a trail, not a second opinion.
If you choose option 4, say so plainly in the pull request and treat the guard
as mistake prevention only. A rewrite then needs you to turn the protection off
for the merge and back on, which is an act of discipline, not a control.

**Defense in depth (not a boundary).** These add friction and a record and
never replace the code-owner review:

- Restricting who can push `redact/*` branches. A pull request from any branch
  can replace the workflow, so this is not a trust boundary.
- The running-copy comparison at the start of the job.
- The label, the 24 hour age and the detective job summary.

**What the offline tests cover, and what only your live gates prove.** The engine
repository's test `tests/test_history_guard_boundary.py` reads the shipped
`.github/CODEOWNERS` with segment-aware matching after GitHub's CODEOWNERS page
(read 2026-11-27: `*` does not cross `/`, the last matching pattern wins, an
empty owner list un-owns a path; `!`, `[ ]` and backslash escapes are not
supported by GitHub and are refused by the test). It checks CODEOWNERS PATTERN
COVERAGE for the paths it names: the guard files it lists (the workflow, the
approvers file, files under `scripts/`, `engine.lock`, `engine.release.json`,
`history/redactions/` and the CODEOWNERS file itself, plus two synthetic nested
paths) match a rule with an owner, and the history file and `numbers.json` match
none. It does not show that GitHub enforces anything. Enforcement (code-owner
review required, administrators included, an author who cannot approve their own
pull request) is shown only by your own live gates: the "You OBSERVED it block"
item of the checklist below and the optional live gate G1. Until you have run
them and seen the block, the boundary is UNVERIFIED and activation stays blocked.

**Trusted inputs the workflow reads.** This is the inventory of the inputs that
the test's parsers find in `.github/workflows/history-guard.yml` and
`scripts/history-guard.py`. The tests are change detectors and heuristics, not a
security boundary. The CLAIMS table and the KNOWN GAPS list below say exactly
what each test backs and what it does not.

- Names: each context path, step environment name and shell variable the parser
  finds in the workflow, and each environment name the script reads through
  `env()`, `os.environ.get` or `os.getenv`, is listed here; no row lists a name
  those parsers do not find. Implicit process inputs are not found (see KNOWN GAPS).
- Change detection (a review trigger, not an independent check): the workflow
  file (byte for byte), each of its steps, and the files its steps name (the
  guard script, `scripts/read-lock.sh` and `scripts/verify-history-rewrite.sh`)
  are pinned by SHA-256. The other files checked out from the base commit are
  listed with the reason they are not run (the engine lock and release manifest
  are read as data, the workflow is interpreted by Actions). A change to any of
  them, a new job or a new step fails a test that names it, so a human must
  update the table on purpose. A pull request can update the table in the same
  change, so the file that holds it is owned in the engine repository's
  CODEOWNERS and its update needs the maintainer's review.
- Heuristic (best effort): a scan for common network commands and foreign hosts
  in the workflow and shell scripts, and a syntax-tree scan of the guard script.
  It catches common mistakes and cannot prove the absence of network use; a
  split string, an alias or an indirect command gets past any such scan.

The tests do not show that a listed input is used safely at run time (that is
the guard's own tests and the engine's compare), and they do not show what the
job can reach. The protection is CODEOWNERS plus branch protection on `main`,
which your own live gates exercise (the checklist below). Confining the job's
network access at run time is tracked as a separate follow-up. The wrapper
starts the engine image with the network off; the container runtime's own pull
of that image is outside these tests.

The workflow has no explicit reference to the `secrets`, `vars`, `inputs` or
`env` contexts, and no artifact upload, artifact download or cache step (the test
checks exactly these names; it says nothing about GitHub's own contexts beyond
the rows below).

| Input | Where it comes from | Trust |
| --- | --- | --- |
| `github.event.pull_request.base.sha` | event payload | commit id the job pins; the base is trusted code |
| `github.event.pull_request.head.sha` | event payload | the head is only ever data |
| `github.event.pull_request.number` | event payload | a number, used in API paths and the run group |
| `github.event.pull_request.head.repo.full_name` | event payload | compared with this repository: same repository only |
| `github.event.pull_request.head.ref` | event payload | branch name, through `env`, must match `redact/` |
| `github.event.pull_request.created_at` | event payload | a date, the day written into the compare |
| `github.repository` | context | this repository |
| `github.token` | context | read-only token (`contents`, `pull-requests`, `actions`) |
| `github.workflow_ref` | context | must name this workflow file |
| `steps.trust.outputs.dir` | an earlier step | the read-only folder of base copies |
| `REPO`, `PR_NUMBER`, `GH_TOKEN`, `HEAD_SHA`, `BASE_SHA`, `HEAD_REPO`, `HEAD_REF`, `PR_CREATED_AT`, `TRUSTED`, `WORKFLOW_REF` | environment of the steps | set only from the rows above |
| `RUNNER_TEMP`, `GITHUB_OUTPUT`, `GITHUB_STEP_SUMMARY` | runner environment | scratch folder and result files of the job |
| `scripts/read-lock.sh`, `scripts/verify-history-rewrite.sh`, `scripts/history-guard.py` | files of the BASE commit | trusted, sparse checkout, run by the job |
| `engine.lock`, `engine.release.json` | files of the BASE commit | trusted, sparse checkout, read as data by `scripts/read-lock.sh` |
| `.github/workflows/history-guard.yml` | file of the BASE commit | trusted, sparse checkout, compared with the running copy; interpreted by Actions |
| `.github/history-approvers` | file of the BASE commit | trusted, read as a git object |
| `history/social.jsonl`, `numbers.json`, one manifest under `history/redactions/` | files of the HEAD commit | untrusted data, never executed, checked by the engine |
| `issues/{number}/events`, `issues/{number}/labels`, `actions/runs`, `pulls`, `git/ref/heads/main` | GitHub API of this repository | read-only calls |

**CLAIMS and KNOWN GAPS.** Each claim the tests back, the tests that back it, and
the gap that remains. A test name here must exist: a test checks that.

| Claim | Tests | Known gap |
| --- | --- | --- |
| The listed guard paths match a CODEOWNERS rule with an owner; the history file and `numbers.json` match none | `test_the_example_owns_every_protected_path_and_not_the_history_file`, `test_every_shipped_file_under_the_guarded_paths_has_an_owner`, `test_the_sparse_checked_files_are_owned` | Only the listed and shipped paths; GitHub enforcement is not tested |
| The matcher follows the documented GitHub cases and refuses unsupported syntax | `test_the_matcher_conforms_to_the_documented_cases`, `test_unsupported_syntax_is_refused_not_skipped` | A bare name matching only itself is a strict reading, unverified against GitHub |
| The coverage check names a path whose rules were removed | `test_the_coverage_check_names_a_path_whose_rules_were_removed`, `test_the_coverage_check_names_a_history_file_that_became_owned` | Mutations of the shipped file only |
| The input names the parsers find in the workflow and script are listed, and no listed name is unread | `test_the_workflow_reads_no_input_that_the_inventory_leaves_out`, `test_the_script_reads_no_input_that_the_inventory_leaves_out`, `test_the_inventory_names_no_input_that_nothing_reads` | Implicit process inputs are not found |
| The workflow has no explicit `secrets`, `vars`, `inputs` or `env` reference and no artifact or cache step | `test_the_workflow_has_no_explicit_secrets_vars_inputs_artifact_or_cache` | Only those names |
| The job list and every step match the pinned table | `test_the_workflow_matches_the_pinned_step_table`, `test_a_new_step_and_a_second_job_are_caught` | A pull request can update the table; review trigger only |
| The workflow file is pinned byte for byte | `test_the_workflow_file_matches_its_pinned_digest`, `test_a_top_level_workflow_change_fails_the_file_digest_and_names_it` | Same: review trigger only |
| The three scripts the steps name are pinned by SHA-256 | `test_the_pinned_files_match_their_digests`, `test_a_changed_executed_file_fails_and_names_it` | Same: review trigger only |
| Files in the sparse list and paths named in steps are in the tables, matched exactly | `test_sparse_checked_and_step_named_files_are_pinned_or_explained`, `test_a_foreign_or_traversing_path_is_not_accepted_as_an_inventoried_file` | Only the syntactic forms the token pattern finds |
| The pinned scripts name only pinned callees and run no recognised dynamic form (outside a short reasoned allow list) | `test_the_pinned_scripts_name_only_pinned_callees_and_no_dynamic_form`, `test_a_nested_or_dynamic_callee_in_a_pinned_shell_script_fails_naming_the_line`, `test_a_dynamic_or_unlisted_callee_in_the_guard_script_fails_naming_the_line` | See the gaps below |
| Common network commands and foreign hosts are caught (heuristic) | `test_heuristic_the_script_scan_finds_no_common_network_mistake`, `test_heuristic_the_shell_scan_finds_no_common_network_mistake` | Cannot show the absence of network use |
| The trust step installs read-only base copies and stops on a bad release pair | `test_the_trust_step_installs_read_only_base_copies_not_the_pull_request`, `test_the_trust_step_stops_a_bad_release_pair_before_any_output` | Run with a jq stand-in, not GitHub's runner |
| Updating the pinned tables needs the maintainer's review | `test_the_table_and_this_file_are_owned_in_the_engine_codeowners` | Depends on branch protection of the engine repository |

KNOWN GAPS, one line each (the tests do not cover these):

- Files that those scripts call in turn are found only when named as a literal
  path ending in a script extension; the callee of a callee is not followed.
- Extensionless scripts are not discovered.
- Paths built at run time are not resolved; in a pinned script they fail the
  test when they take a recognised dynamic form, and other forms are not found.
- Implicit process inputs are not inventoried: `PATH` and command lookup, git
  configuration, container runtime configuration and other tool configuration
  files, and the environment the runner provides.
- Network use is not proven absent (heuristic scans only); runtime egress
  confinement is a separate follow-up.
- GitHub's own behavior (CODEOWNERS enforcement, which workflow revision runs for
  a pull request that edits it) is shown only by your live gates.

**Checklist before you list the first approver.** `.github/history-approvers`
ships empty so that nothing is accepted. Complete all of these first, in order:

- [ ] `.github/CODEOWNERS` is present on `main` and covers `.github/`,
      `scripts/`, `engine.lock`, `engine.release.json`,
      `history/redactions/` and `.github/history-approvers` (not the history
      file itself, so the daily refresh needs no review).
- [ ] Branch protection or a ruleset on `main` requires review from Code Owners
      (with stale approvals dismissed, approval of the most recent reviewable
      push, and administrators included, no bypass), and the check
      `History guard` with "branch must be up to date".
- [ ] You OBSERVED it block: a test pull request that edits
      `.github/workflows/history-guard.yml` cannot merge without an independent
      code-owner approval. Configuration alone does not count; do not add any
      approver login until you have seen the block.
- [ ] Optional, recommended: live gate G1 (which revision of the workflow GitHub
      executes for a pull request that edits it) was run on a scratch
      repository. Not required for this boundary.

**What is verified and what is not.** Verified from GitHub's documentation: a
`pull_request` workflow must already exist on the default branch and runs
against the pull request's merge ref. The guard reads its scripts and the
approvers file from a checkout pinned to the base commit and the pull request's
data from a checkout pinned to the head commit. UNVERIFIED until G1: which
revision of the workflow file GitHub executes for a pull request that edits it,
and (G5) that `actions: read` is enough and every write is refused.
Until the checklist is complete, assume anyone who can merge a change to the workflow, `scripts/`,
`engine.lock` or the approvers file can weaken the guard. A pull request that
edits the workflow itself fails the running-copy comparison by design: change
the workflow in its own reviewed pull request.

**`actions: read` is wide.** The job token needs `actions: read` only to list
the workflow runs of the head commit. That permission is capability-wide: it
also reads the logs and artifacts of the repository, and GitHub has no narrower
built-in permission for the run list alone. It is acceptable only together with
the trust boundary above, because a replaced workflow would receive it. The
token cannot write.

**What this does not do.** A sole owner cannot be protected from their own
account; the guard prevents mistakes, bounds a rewrite, and leaves a record in
the job summary (short commit ids, the label event number, label age and the
manifest name; no login, no full commit id, no value). Whether removing the label
after a green run turns the merge red depends on your branch protection. An
approval covers one head commit only: a different head needs the label removed
and applied again. A tombstone cleans the working
tree only: git history, deployed copies and Actions artifacts still hold the
old value. Removing it from the git history of this repository is a separate
operation (see the runbook "Runbook: removing redacted history from the private
instance repo's git history").

## The token

There is no Cloudflare token in GitHub. Each deploy job reads it from the secret
manager (Doppler) with a short-lived GitHub OIDC identity, one identity per tier.
It reaches only two kinds of step: the wrangler steps inside the engine's
reusable Worker workflow (migrations, Worker deploy) and the one Pages publish
step of each `pages-*` job. It is never in workflow or job `env`, never in an
install step, and the `validate` job has no environment and no `id-token`
permission at all. Only the four deploy jobs (`worker-staging`, `pages-staging`,
`worker-production`, `pages-production`) hold `id-token: write`.

In the secret manager, put the Cloudflare token (as `CLOUDFLARE_KEY_VALUE`) in
the `dev` config for staging and the `prd` config for production, and make one
service-account identity per tier, each pinned to this repository and its own
environment (the OIDC subject ends `:environment:staging` or
`:environment:production`) and able to read only its own config. The token is a
user token with only: Account Cloudflare Pages Edit, D1 Edit, Workers Scripts
Edit, User Details Read, and an expiry (add Workers Routes Edit and Zone Read on
the one zone only if you set a zone). Use the secret manager's `prd` config only
after you have decided the site is live.

For deploys from your own machine, `scripts/deploy-local.sh` reads the token
from Doppler per command (see the comments at the top of that script).

## How updates flow

1. A new engine release is published (a `v*` tag; the image appears on GHCR).
2. Renovate opens one pull request, "reach-around engine", that changes the
   three lines in `engine.lock` together: image tag and digest, and the source
   commit.
3. The `Preview` workflow checks and builds your site with the new engine. A
   config that the new version rejects shows up in the pull request comment,
   in plain language, not in production.
4. You read the engine's `CHANGELOG.md`, merge, and `Deploy` runs.

Renovate must be enabled for the repository (the Renovate GitHub App, or your
own runner). Dependabot is not used: it can bump the version in a `uses:` line
but cannot update an image digest held in a plain file such as `engine.lock`.
To update by hand, copy the digest from the release notes or
`podman inspect` and the commit from the release tag, and edit all three lines.

Engine releases are alpha: the config format can change between versions.
That is why the pin exists and why every update goes through a pull request.

## Never commit

- API tokens, `.env` files, Doppler or wrangler credentials
- `wrangler.deploy.toml` (it holds real ids), settings files for
  `deploy-local.sh`, `*.bak`
- photos with location or camera metadata
- anything from visitors (click exports, lead lists)

`scripts/check-repo.sh` fails the build if it finds files by those names. It
is a filename check, not a secret scanner.
