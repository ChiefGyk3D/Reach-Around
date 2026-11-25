<!-- SPDX-License-Identifier: Apache-2.0 -->
# 3. Instance repo and pinning the engine

Reach Around is split in two on purpose.

- The **engine** is the public repository: versioned, released as a signed
  container image. It validates a config, builds a static page and runs the
  click-counting Worker.
- Your **instance** is a separate, **private** repository: your `site.yaml`,
  images, overrides and the workflow that deploys them. It contains no engine
  code; it only says which engine release to use.

Do not fork the engine to run your page. A fork of a public repository stays
public, and you do not need the engine's code in your repository.

## Create your instance

The template is [`examples/instance-repo/`](../../../examples/instance-repo/) in the
engine repository. Create a new private repository, then copy the template in,
including the hidden `.github` and `.gitignore`:

```sh
git clone https://github.com/ChiefGyk3D/Reach-Around.git
git clone git@github.com:<your-account>/<your-instance>.git
cp -r Reach-Around/examples/instance-repo/. <your-instance>/
cd <your-instance>
```

Edit `site.yaml`, then check it builds (needs Docker or Podman):

```sh
scripts/validate.sh --out out
```

The template needs engine v0.2.0 or later: `--manifest` and `reach-around check`
first shipped there, and `engine.lock` in the template pins it.

On GitHub, also turn on secret scanning and push protection, and protect `main`
(require the `CI green` check from the `Preview` workflow, no force pushes).

## What is in the template

| Path | What it is |
|---|---|
| `site.yaml` | Your page. Edit this. |
| `media/` | Your images. |
| `overrides/` | Optional template and CSS overrides. |
| `engine.lock` | The pinned engine (below). |
| `.github/workflows/preview.yml` | On a pull request: check the config, build a preview artifact, comment the result. Deploys nothing. Its gate job is named `CI green`. |
| `.github/workflows/deploy.yml` | On a push to `main`: check, validate, deploy after a reviewer approves. |
| `.github/workflows/refresh-counts.yml` | Optional daily follower-count refresh, as a pull request. |
| `renovate.json` | Keeps `engine.lock` and the Action pins current. |

## Pinning the engine

`engine.lock` is three lines that move together:

```
ENGINE_IMAGE=ghcr.io/chiefgyk3d/reach-around:<version>@sha256:<digest>
ENGINE_REPO=ChiefGyk3D/Reach-Around
ENGINE_REF=<commit of the release tag> # v<version>
```

Pin by **digest**, not by tag: a tag can be moved, a digest cannot. The image
holds the site builder only, not the Worker, so the lock also pins the **commit**
of the same release tag and the deploy checks out only `worker/` from it. Both
must come from the same release. The scripts parse the file (they never source
it) and check each value's shape before it reaches a command.

Pinning the container image by digest was chosen over a git submodule (ties your
history to the engine's, and an update is easy to merge unread) and over
`pip install` of a tagged release (runs dependency build scripts on the runner).

To verify an image before you pin it, use the commands in
[docs/releasing.md](../../releasing.md#verify-an-image).

## Preview on every pull request

The `Preview` workflow runs `reach-around check`, then the build, in the image
named by the **base** branch's `engine.lock`, with no network and only
`site.yaml`, `media/`, `overrides/` and `numbers.json` mounted read-only. It
posts a plain-language comment: valid (what changed), not valid (each problem as
one sentence naming the tab, block and field), or build failed. No value from
`site.yaml` is ever printed.

Because GitHub runs the workflow file from the pull request, a collaborator with
write access can change the workflow in their own pull request. The template
stops a pull request from forging the comment or choosing the engine image, but
not a trusted writer from rewriting the workflow. Give write access only to
people you trust, and protect `.github/`, `scripts/` and `engine.lock` with a
`CODEOWNERS` file and required review. Pull requests from forks get no preview.

A change to `engine.lock` takes effect for previews only after it merges.

**Unverified.** The preview workflows pass actionlint, zizmor and the engine's
tests, but the comment lookup and update (`gh api` on the issue comments), the
sparse checkout of one script and the run-page link have not run on a real pull
request. Check the first one. The first pull request that adds these files has
no base copy of the renderer, so its comment job fails; add the template to
`main` directly.

## Forks

| | Instance repository | Fork |
|---|---|---|
| Contains | config, media, overrides, deploy workflow | the engine's code |
| Visibility | private is normal | a fork of a public repository stays public |
| Updates | a pull request that moves `engine.lock` | merging upstream, with conflicts |
| Use it for | running your page | changing the engine itself |

If you need a change the engine does not offer, prefer an override in the
instance. If you do fork, point `ENGINE_REPO` at your fork, build and publish
your own image and pin that digest. The image's signature identity is the release
workflow, so a fork's image has a different identity and the signature checks
need that identity changed. The reusable Worker deploy refuses any `engine_repo`
other than the engine itself; a fork keeps and calls its own copy.

If the engine repository or image is private, the runner cannot pull it with the
default `GITHUB_TOKEN`. Use a fine-grained read-only token or a GitHub App scoped
to those two things, stored as an environment secret and only in the steps that
pull and check out.

## Assets

An asset set is a directory tree whose paths match the local paths in
`site.yaml` (a config value of `media/avatar.png` needs that path under the
selected root). The public engine repository holds only synthetic example assets.
Your real publishable assets live in your private instance. One real set for
private development and production is the default; separate trees are optional.

`reach-around build` uses the config's directory by default. To choose another
set:

```sh
reach-around build --config <your-instance>/site.yaml --assets-dir <your-instance>/assets/production --out out
```

The engine does not merge sets or fall back from one to another, so a missing
file stops the build instead of silently mixing development and production.

## What must never be committed

- API tokens, `.env` files, Doppler tokens and credentials of any kind.
- The generated `wrangler.deploy.toml` and the settings file used by
  `scripts/deploy-local.sh`: they hold real account, database and host values.
- Photos that carry location, camera or author metadata (the engine copies image
  bytes unchanged).
- Any visitor data: click exports, lead lists, logs.
- A personal identifier your page must not publish.

A private repository limits who can read a file, not who can read a clone, a log
or a fork. It is not a safe place for a token.

## The Worker deploy

The Worker and D1 half of a deploy exists once as a reusable workflow,
`.github/workflows/deploy-worker.yml` in the engine, which an instance can call
pinned by commit. **The shipped template does not call it yet**; it still does the
older inline Worker deploy. Moving the template over is tracked in the engine's
issue #694. A repository copied from the current template does not get the
OIDC-only reusable deploy until it adds the call itself. The call, its inputs and
its caveats are in the long reference under "Deploying the Worker from an
instance repo" in [docs/deploy.md](../../deploy.md).

**Unverified.** The reusable workflow has not been run end to end. Its first run
should be on a staging environment, from `main`, with a reviewer present.

## Next

[Secrets](04-secrets.md).
