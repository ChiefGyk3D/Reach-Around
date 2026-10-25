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
| `.github/workflows/preview.yml` | On a pull request: check the config, build a preview artifact, and comment on the pull request with the result in plain language and the list of changed blocks. Also lints the scripts and workflows (GYST `bash-ci`). Deploys nothing. Its gate job is named `CI green`. |
| `.github/workflows/deploy.yml` | On a push to `main`: check, validate, then deploy to Cloudflare after a reviewer approves. |
| `.github/workflows/refresh-counts.yml` | Once a day (and by hand): refresh follower counts with the pinned engine and open a pull request with the new `numbers.json`. Optional; see "Daily follower counts". |
| `renovate.json` | Keeps `engine.lock` and the Actions pins current. |
| `scripts/` | `check-config.sh` (the plain-language config check), `validate.sh` (the build CI runs), `write-result.py` (bundles the preview job's results into the one data file the comment job reads), `render-comment.py` (run by the comment job from the base commit: validates that file and writes the pull request comment from fixed text), `check-repo.sh`, `read-lock.sh`, `deploy-local.sh`. |
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

4. Commit and push to `main`. The deploy job will stop at the `production`
   environment until you finish the setup below, which is expected.

## One-time setup

The Cloudflare side (Pages project with Direct Upload, D1 database, API token
scopes, production branch) is described step by step in the engine's
`docs/deploy.md`. Follow its sections 1 and "Token type", using your own
names. Every name here is a placeholder.

On GitHub: **Settings > Environments > New environment** named `production`.

- Add yourself (or a second person) under **Required reviewers**. Every deploy
  then waits for a click before the token is released to the job.
- Under **Deployment branches**, allow only `main`.
- Add this **secret**: `CLOUDFLARE_API_TOKEN`. Create the token as a user
  token with only: Account Cloudflare Pages Edit, D1 Edit, Workers Scripts
  Edit, User Details Read, and an expiry. Add Workers Routes Edit and Zone
  Read on the one zone only if you set `ZONE_NAME`.
- Add these **variables** (not secret, but keep real values out of commits and
  issues):

  | Variable | Example (placeholder) |
  |---|---|
  | `CLOUDFLARE_ACCOUNT_ID` | `00000000000000000000000000000000` |
  | `PAGES_PROJECT` | `my-links` |
  | `D1_DATABASE_ID` | `00000000-0000-0000-0000-000000000000` |
  | `RATELIMIT_NAMESPACE_ID` | `1001` |
  | `SITE_HOST` | `links.example.invalid` |
  | `ZONE_NAME` (optional) | `example.invalid` |

  Unlike the engine's own test workflow, there is no `LINK_IDS` variable: the
  workflow reads the link ids from the build, so the Worker and the page cannot
  disagree.

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
follower counts", that changes `numbers.json`. You merge it; that push runs
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
| `TWITCH_CLIENT_ID` | `twitch` | The client id of an app in the Twitch developer console. |
| `TWITCH_CLIENT_SECRET` | `twitch` | The client secret of the same app. |

Sources that need no key (Bluesky, Mastodon-compatible servers, GitHub, Discord
invites, Matrix) need no secret. The names are fixed by the workflow, so in
`site.yaml` set `api_key_env: YOUTUBE_API_KEY`, `client_id_env: TWITCH_CLIENT_ID`
and `client_secret_env: TWITCH_CLIENT_SECRET`. The Twitch follower total needs a
user token that app credentials cannot get, so that source is normally reported
as skipped (`needs_user_token`); keep that number by hand in `numbers.json`.

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
- The workflow has `contents: write` and `pull-requests: write` and nothing
  else, uses no `pull_request_target`, and refuses to run from a branch other
  than `main`.

## The token

The token reaches only the three wrangler steps in `deploy.yml` (migrations,
Worker deploy, Pages deploy). It is never in workflow or job `env`, never in
the install steps, and the build job has no environment at all.

**Option: Doppler by OIDC (disabled).** Instead of a GitHub secret, the deploy
job can read the token from Doppler with a short-lived OIDC identity, so no
long-lived Cloudflare token is stored in GitHub. The engine's issue #98 tracks
the supported workflow for this. Until it lands, the commented block in
`deploy.yml` marks where it goes, and you should use the environment secret.
Do not enable it by guessing: it needs a Doppler service-account identity
scoped to this repository and the `production` environment.

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
