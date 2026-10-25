<!-- SPDX-License-Identifier: Apache-2.0 -->
# Instance repositories

Reach Around is split in two on purpose.

- The **engine** is this repository: public, versioned, released as a signed
  container image. It knows how to validate a config, build a static page and
  run the click-counting Worker.
- Your **instance** is a separate, private repository: your `site.yaml`,
  images, overrides, and the workflow that deploys them. It contains no engine
  code. It only says which engine release to use.

The template for an instance is [`examples/instance-repo/`](../examples/instance-repo/).
Copy its contents into a new private repository (its README has the commands).
This page explains the model. It answers issue #78 and builds on issue #103
(phase 2: edit from anywhere, deploy automatically).

## How the instance consumes the engine

Three options were weighed.

| Option | Verdict |
|---|---|
| **Pinned container image** (chosen) | The image is built, scanned, signed and attested by the engine's release workflow. Pinning its digest means the bytes you build with are the bytes that were verified. No Python toolchain on the runner, and the build runs with no network, no capabilities and a read-only root. |
| Git submodule | Ties the instance's history to the engine's, and a submodule update is easy to merge without reading. The image already carries what a submodule would. |
| `pip install` of a tagged release | Runs dependency build scripts on the runner and needs a hash-locked requirements file kept in sync by hand. The release workflow publishes the image, not a package. |

One gap: the image holds the site builder only, not the Worker. The Worker is
deployed from the engine's `worker/` folder, so the instance also pins the
**commit** of the same release tag and checks out only `worker/` from it.
`engine.lock` in the template holds both pins in three lines:

```
ENGINE_IMAGE=ghcr.io/chiefgyk3d/reach-around:0.1.0@sha256:<digest>
ENGINE_REPO=ChiefGyk3D/Reach-Around
ENGINE_REF=<commit of the v0.1.0 tag> # v0.1.0
```

The scripts parse this file (they never source it) and check each value's
shape before it reaches a command or `$GITHUB_ENV`.

## Pinning and updating

Pin by digest, not by tag. A tag can be moved; a digest cannot. Both the image
and the source commit must come from the same release.

**What updates the pins.** Dependabot can bump the version in a `uses:` line of
a workflow, but it cannot update a digest stored in a plain value such as an
`env:` entry or a file like `engine.lock`. Renovate can, with a regex custom
manager, and it can update the image tag and digest and the tag and commit
together as one grouped pull request. The template ships a `renovate.json` that
does this, plus `helpers:pinGitHubActionDigests` so every Action stays pinned
by commit with a version comment. That means the instance needs the Renovate
GitHub App (or a self-hosted Renovate). The regex managers were written from
Renovate's documented configuration and have not been run against a live
Renovate; check the first pull request it opens.

**What a human does.** Every update is a pull request. The `Preview` workflow
builds your site with the new engine and reports problems in plain language, so
a config the new version rejects (the format is alpha and can change) is caught
before it reaches production. Read the engine's `CHANGELOG.md`, then merge.

To update by hand, change all three lines of `engine.lock` from one release.
To verify an image before pinning it, use the commands in
[releasing.md](releasing.md#verify-an-image).

## Forks

A fork is a copy of the engine that you change. An instance is not.

| | Instance repository | Fork |
|---|---|---|
| Contains | config, media, overrides, deploy workflow | the engine's code |
| Visibility | private is fine and normal | a fork of a public repository stays public |
| Updates | a pull request that moves `engine.lock` | merging upstream, with conflicts |
| Use it for | running your page | changing the engine itself |

If you need a change the engine does not offer, prefer an
[override](customizing.md#layer-4-template-and-css-overrides) in the instance.
If you do fork, point `ENGINE_REPO` in `engine.lock` at your fork, build and
publish your own image, and pin that image's digest. The image's signature
identity is the release workflow, so a fork's image has a different identity
and the checks in `releasing.md` need that identity changed to match.

If the engine repository or its image is private, the runner cannot pull them
with the default `GITHUB_TOKEN`. Use a fine-grained read-only token or a
GitHub App scoped to those two things, stored as an environment secret, and
only in the steps that pull and check out.

## What must never be committed

- API tokens, `.env` files, Doppler tokens and credentials of any kind. The
  template's `.gitignore` and `scripts/check-repo.sh` cover common file names.
- The generated `wrangler.deploy.toml` and the settings file used by
  `deploy-local.sh`: they hold real account, database and host values.
- Photos that carry location, camera or author metadata. The engine copies
  image bytes unchanged (threat model T-20).
- Any visitor data: click exports, lead lists, logs.
- A real callsign, address or similar personal identifier, if your page must
  not publish it.

A private repository is not a safe place for a token. It limits who can read
the file, not who can read a clone, a log or a fork of the repository.

## Secrets: environment secret now, Doppler later

Today the deploy token is a secret on the GitHub environment `production`. The
environment has a required reviewer and a `main`-only deployment branch rule, so
the secret is released to the deploy job only after a person approves. The token
reaches the three wrangler steps and nothing else, which is also what the
engine's own `deploy-test.yml` does and what threat model T-02 asks for (the
build job has no secret, the deploy job runs fixed commands).

The intended end state is Doppler. Project secrets live in a Doppler project,
and CI reads them with a short-lived OIDC identity instead of a stored token,
which removes the long-lived token from GitHub and lets the owner revoke and
rotate in one place (T-19). Issue #98 tracks the workflow. Until it lands the
template keeps the option documented in comments and **disabled**; it needs
`id-token: write` on the deploy job and a Doppler identity scoped to the
repository and environment, neither of which is set up here. For deploys from
your own machine, `scripts/deploy-local.sh` already reads the token from Doppler
per command, as [`docs/examples/deploy-private.sh`](examples/deploy-private.sh)
does.

## What the template's workflows do

| | `preview.yml` (pull request) | `deploy.yml` (push to `main`, manual) |
|---|---|---|
| Trigger | `pull_request` only, never `pull_request_target` | `push` to `main`, `workflow_dispatch` |
| Permissions | `contents: read`; only the `comment` job also has `pull-requests: write` | `contents: read` |
| Secrets, environment | none | `production`, reviewer required |
| Runs | `reach-around check`, then the build, with the pinned image and no network; GYST `bash-ci` over the scripts and workflows | `reach-around check`, then the build, with the pinned image and no network |
| Output | a `preview` artifact (the built site) and a pull request comment | site artifact, then Pages and Worker deploy |
| Token | only the run's own token, only in the `comment` job | wrangler steps only |
| Gate | `CI green` (needs every other job) | the `production` approval |
| Concurrency | one preview per pull request; a new push cancels the old | one deploy at a time, never cancelled |

### What the pull request sees

On every push to a pull request the `preview` job runs the engine's
`reach-around check` against `site.yaml` and against the base branch's copy.
If the config is valid it builds the site and uploads it as the artifact
`preview`; either way it writes a comment text. The `comment` job, which has
the only write permission and checks out no code, posts that text (or updates
its own earlier comment). The comment says:

- valid: the tabs and blocks that changed, and a link to the `preview`
  artifact (download, unzip, open `index.html`);
- not valid: each problem as one sentence naming the tab, the block and the
  field, with a fix, and no build;
- valid but the build failed: that the settings are fine and the run log
  says which file is missing;
- the check could not run: usually an engine in `engine.lock` that is too old.

The check never prints a value from `site.yaml`, only the tab title, the block
id and the setting name, so a token or an address pasted in the wrong place
is not echoed into a comment. The comment job reads one text file, cut to
a fixed size, from the preview job and runs none of the change's code. A pull
request from a fork gets the check and the artifact but no comment.

A Pages preview deployment (a URL per pull request) is not used: it would need
the Cloudflare token in a job that runs the change's code, which the template
keeps apart on purpose. The artifact is the preview.

**Not yet exercised on GitHub.** The workflows pass actionlint, zizmor and the
tests in this repository; the comment step (`gh pr comment --edit-last
--create-if-none`) and the artifact link have not run on a real pull request.
Check the first one.

The link manifest is never inside the published site directory. The build
writes it to a separate directory (`validate.sh --manifest FILE`, mounted at
`/manifest`), the workflow uploads it as its own artifact (`site-manifest`), and
the deploy job downloads it into an empty directory, checks that it holds
exactly one regular file, and reads the link ids in a step with no secrets.

**Engine version.** `--manifest` and `check` arrived after the published 0.1.0
image, so the template needs the next engine release. `engine.lock` stays on
0.1.0 until that release exists, and `validate.sh` and `check-config.sh` say
so plainly if the pinned image does not know the option or the command.

Every Action is pinned by commit with a version comment, using the same pins
as this repository's workflows. A test in this repository
(`tests/test_instance_repo_template.py`) builds the template's `site.yaml` and
asserts these points, so the template cannot drift from the hardening described
in [deploy.md](deploy.md).

## Related

- [deploy.md](deploy.md): the Cloudflare side, token scopes, Pages versus Worker.
- [customizing.md](customizing.md): the config, themes and overrides you keep in the instance.
- [releasing.md](releasing.md): how the image you pin is built, signed and verified.
