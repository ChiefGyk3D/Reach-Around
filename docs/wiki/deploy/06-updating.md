<!-- SPDX-License-Identifier: Apache-2.0 -->
# 6. Updating

Three kinds of change, each with its own path: your **content**, the **engine**
version and the **Worker**. Every change is a pull request in your private
instance repository.

## Change your page

Edit `site.yaml`, `media/` or `overrides/` and open a pull request. The `Preview`
workflow checks the config, builds the page and comments the result in plain
language ([page 3](03-instance-repo-and-pinning.md#preview-on-every-pull-request)).
Merge once `CI green` is green; a push to `main` starts the deploy, which waits for
the environment's reviewer. To check locally first (needs Docker or Podman):

```sh
scripts/validate.sh --out out
```

Hand-kept follower numbers live in `numbers.json`. To update one, read the number
from the platform, change that platform's `count` (a whole number) and set `as_of`
to the time you read it, written like `2026-01-15T12:00:00Z` (a real date, not in
the future). An entry older than 30 days triggers a warning but is still shown. A
fetched number always wins over a hand-kept one while it is fresh.

If scheduled count refreshes stop, GitHub disables scheduled workflows after 60
days without repository activity and nothing alerts you. Check the Actions tab for
a disabled schedule and re-enable it. A failed refresh does not stop the deploy:
the build uses the `numbers.json` committed beside the config.

## Update the engine

An engine update moves the three `engine.lock` lines from one release, together:
the image tag and digest, the repository and the commit of the release tag.

1. Read the engine's `CHANGELOG.md` for the release. The config format is alpha
   and can change, so a config the new version rejects is caught by the preview,
   not production.
2. Verify the image before you pin it:

   ```sh
   cosign verify "ghcr.io/chiefgyk3d/reach-around:<version>" --certificate-identity-regexp '^https://github.com/ChiefGyk3D/git-your-ship-together/\.github/workflows/container-release\.yml@' --certificate-oidc-issuer https://token.actions.githubusercontent.com
   ```

   ```sh
   cosign verify-attestation --type spdxjson "ghcr.io/chiefgyk3d/reach-around:<version>" --certificate-identity-regexp '^https://github.com/ChiefGyk3D/git-your-ship-together/\.github/workflows/container-release\.yml@' --certificate-oidc-issuer https://token.actions.githubusercontent.com
   ```

   ```sh
   gh attestation verify "oci://ghcr.io/chiefgyk3d/reach-around:<version>" --owner ChiefGyk3D
   ```

   Use the digest the engine's `Release` run prints. More in
   [docs/releasing.md](../../releasing.md#verify-an-image). Confirm the output
   yourself; a fork's image has a different signing identity.
3. Open the pull request, by hand or from Renovate. The template ships a
   `renovate.json` that groups the image and the source commit into one pull
   request and keeps every Action pinned by commit with a version comment. That
   needs the Renovate GitHub App or a self-hosted Renovate. **Unverified:** the
   regex managers were written from Renovate's documented configuration and have
   not been run against a live Renovate. Check the first pull request it opens.
   Dependabot cannot update a digest held in a plain value such as `engine.lock`.
4. Read the `Preview` comment, then merge. Any new dependency release is held for
   at least 7 days before you merge it, in case it is compromised.
5. Deploy through your normal path, to staging first when you have one.

Do **not** move `engine.lock` during a 48 hour soak before a cutover: it restarts
the evidence the cutover needs.

## Update the Worker

The Worker is deployed from the engine's `worker/` folder at the commit in
`engine.lock`, so an engine update updates it. Migrations are forward only.

- A deploy that migrates: call the Worker deploy quiet, verify, take a backup and a
  Time Travel bookmark, then migrate and deploy
  ([Going live](05-going-live.md#migrations-with-the-worker-quiet)).
- Read the migration notes for the release before applying them. A migration that
  deletes or rebuilds data must be preceded by an export:

  ```sh
  npx wrangler d1 export <d1-database> --remote --output dump.sql
  ```

- To stop counting without a rollback, use the kill switches. To go back, redeploy
  with the switch cleared:

  ```sh
  npx wrangler deploy --var CLICKS_KILL:1
  npx wrangler deploy --var CLICKS_KILL:0
  ```

The reusable Worker deploy, its inputs, its concurrency rules and its caveats
(none tested by a run yet) are in the long reference. Never run it from a
`pull_request_target` or any event that gives a pull request secrets or an
environment; call it from a push to `main` or a manual run on `main` only.

## Caching and stale files

A page you change shows at once, and a file is cached for a year only because its
name changes whenever its content does. Everything under `static/` and every image
or font your config names carries a content hash in its name and is served
`public, max-age=31536000, immutable`. Pages, `404.html`, `robots.txt` and files a
browser asks for by name are served `public, max-age=0, must-revalidate`.

- A **hashed** file cannot be stale: a different name is a different URL. If a page
  looks wrong, hard-reload once.
- A **stable** file is revalidated, so it is at most one request behind. If the
  zone's Browser Cache TTL holds an old copy, set it to Respect Existing Headers
  and purge that URL.
- A file you **removed** from the build can stay at the edge for hours. Purge it by
  URL. The engine's own test workflow has an optional purge step that does this
  with a token limited to Zone > Cache Purge > Purge on one zone; it is described
  in the long reference, and the instance template does not run it.

## Roll back an update

| What | How |
|---|---|
| The site | Pages project > Deployments > pick the previous deployment > Rollback to this deployment, or re-run the deploy from an earlier good commit on `main`. |
| The Worker | `wrangler rollback`, or Worker > Deployments > Rollback. Links keep working with the Worker dark, because the page uses `ping` plus a beacon, not a redirect. |
| D1 migrations | Forward only. Ship a new migration to undo a change; restore from the Time Travel bookmark and backup ([docs/backup-restore.md](../../backup-restore.md)). |
| The engine | Move `engine.lock` back to the previous release's three lines. A published engine tag is never moved; a bad release is fixed forward with the next version. |
| A leaked token | Revoke both tokens in Cloudflare, issue a new pair, update the store ([Secrets](04-secrets.md#rotation-and-compromise)). |
| Stop all deploys | Disable the workflow in the Actions tab, or remove the environment secret. |

For the weekly and monthly routine after you are live, see
[docs/operations.md](../../operations.md).
