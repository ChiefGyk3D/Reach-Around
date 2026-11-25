<!-- SPDX-License-Identifier: Apache-2.0 -->
# 2. First deploy (test)

Deploy to a **test hostname** first. The production hostname is a separate step
([Going live](05-going-live.md)). This page covers the Cloudflare pieces, the
GitHub environment and the first run. The long reference for every optional
variable is [docs/deploy.md](../../deploy.md).

| Placeholder | Meaning |
|---|---|
| `<site-host>` | the test hostname, for example `links-test.<your-domain>` |
| `<pages-project>` | the Pages project name |
| `<d1-database>` | the D1 database name (the engine's `worker/wrangler.toml` uses `reach-around-clicks`) |
| `<zone-name>` | the Cloudflare zone that holds `<site-host>` |

The Cloudflare dashboard labels below are as the engine's deploy notes recorded
them. Labels can differ between dashboard versions; if one does not match, look
for the same object by name.

## 1. Cloudflare

1. **Pages project, by Direct Upload.** Workers & Pages > Create > Pages >
   Direct Upload. Name it `<pages-project>` and create it with any placeholder
   file. Cloudflare never builds from your repository; the workflow uploads the
   already built site. Check it is a **Pages** project and not a Worker (see
   [Pages or Worker](#pages-or-worker)), and that its production branch is
   `main` ([Production branch](#production-branch)).
2. **D1 database.** Storage & Databases > D1 > Create database, named
   `<d1-database>`. Copy the database id (a UUID).
3. **Rate-limit namespace id.** The Worker's click limiter binding needs a
   positive integer that is unique in your account. It is not a secret and
   nothing is created in the dashboard. Do not use the placeholder numbers that
   appear in `worker/wrangler.toml` (the deploy refuses them).
4. **Account id.** Workers & Pages overview, right-hand column. Not a secret, but
   keep it out of the repository.
5. **Custom domain.** On the Pages project: Custom domains > Set up a custom
   domain, and add `<site-host>`. When the zone is on Cloudflare this creates the
   DNS record. Doing it by hand: a proxied `CNAME` from `<site-host>` to
   `<pages-project>.pages.dev`, but only after the domain has been associated
   with the Pages project (a manual CNAME without that association returns a
   522; verified on Cloudflare's Pages Custom domains page, read 2026-11-19).
6. **API token.** My Profile > API Tokens > Create Token, a **user** token, not an
   account-owned one (an account-owned token cannot carry zone permissions).
   Grant only:

   | Scope | Permission | Why |
   |---|---|---|
   | Account | Cloudflare Pages: Edit | `wrangler pages deploy` |
   | Account | D1: Edit | `wrangler d1 migrations apply` |
   | Account | Workers Scripts: Edit | `wrangler deploy` |
   | User | User Details: Read | wrangler identifies the token owner |

   Include only your one account, and set an expiry (and a client IP filter if
   your address is stable). Do not use the Global API Key. If a wrangler step
   reports a missing permission, add that one permission and nothing broader.

   Only if you set `ZONE_NAME` below, also grant, limited to the one zone:
   Zone > Workers Routes: Edit (creates the `/e/*` route) and Zone > Zone: Read.
   If you would rather not grant them, leave `ZONE_NAME` unset and
   [add the route by hand](#adding-the-route-by-hand).

   Store the token in your secret manager, never in the repository
   ([Secrets](04-secrets.md)). The token is account-wide for Pages, D1 and
   Workers Scripts, so treat it accordingly; per-operation tokens or a dedicated
   Cloudflare account are the stronger isolation (an open owner decision in the
   engine's tracker).

## 2. GitHub

In your instance repository (see [page 3](03-instance-repo-and-pinning.md)):

1. Settings > Environments > New environment. The shipped template deploys to an
   environment named `production`; use a separate environment (for example
   `staging`) for a test hostname if you add one.
   - Add a **required reviewer**. An environment with none releases its secrets
     without a click.
   - Under **Deployment branches**, allow only `main`.
   - **Prevent self-review** locks out a sole maintainer (you could never approve
     your own deploy). Leave it off while you are the only reviewer; turn it on
     once a second reviewer exists.
2. Add the token as described on the [Secrets](04-secrets.md) page.
3. Add these **variables** (not secret, but keep real values out of commits and
   issues):

   | Variable | Value |
   |---|---|
   | `CLOUDFLARE_ACCOUNT_ID` | `<account-id>` |
   | `PAGES_PROJECT` | `<pages-project>` |
   | `D1_DATABASE_ID` | `<d1-uuid>` |
   | `RATELIMIT_NAMESPACE_ID` | `<unique-positive-integer>` |
   | `SITE_HOST` | `<site-host>`, the only origin the Worker accepts clicks from |
   | `ZONE_NAME` (optional) | `<zone-name>`; adds the `/e/*` route for `<site-host>` |

   **Without `ZONE_NAME` the page works but clicks are not counted**, because
   only a zone route sends `/e/c/<id>` to the Worker. The link ids come from the
   build itself; you do not set them.

The other settings (Live status, page views, budgets, kit limits, kill
switches) are optional and are listed in the long reference.

## 3. Run it

Push to `main` in your instance repository, or run the `Deploy` workflow by
hand, then approve the environment. The workflow has two jobs:

1. **build** has read-only repository access and no secrets. It runs
   `reach-around check` and the build in the pinned engine image with no network,
   and uploads the site and the link manifest as separate artifacts. The manifest
   is never inside the published site.
2. **deploy** waits for the reviewer, fetches the token, applies D1 migrations,
   deploys the Worker, then publishes the site with `wrangler pages deploy`.

Why two jobs: dependency installs run third-party code, so none of them runs
while the Cloudflare token exists.

## 4. Check it

Run these against your own host. They need no credential.

```sh
bash scripts/smoke.sh https://<site-host>
```

(`scripts/smoke.sh` is in the engine repository; run it from a checkout of the
engine.) It checks the page answers 200 with a Content-Security-Policy and
`nosniff`, that the write-only routes answer an empty 204 for an unknown id, that
no response sets a cookie and that no request header is echoed back. Exit 0 is a
pass.

Check the two cache classes. These are not covered by the engine's tests, which
check the `_headers` text only, so this is the first real check (unverified until
you run it):

```sh
curl -sI https://<site-host>/ | grep -i cache-control
curl -sI https://<site-host>/static/<hashed-css-name-from-the-page> | grep -i cache-control
```

Expect `public, max-age=0, must-revalidate` for the page and
`public, max-age=31536000, immutable` for the hashed file. If the zone's Browser
Cache TTL is a fixed value, Cloudflare can raise `max-age` on stable files; set it
to **Respect Existing Headers**.

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| The custom domain serves old or different content | The domain is attached to a Worker, not the Pages project, or the deploy went to a preview | See [Pages or Worker](#pages-or-worker) and [Production branch](#production-branch). |
| Route rejected: "No access to the specified resource" | The token lacks Workers Routes: Edit or Zone: Read, or it is account-owned | Use a user token with the zone permissions, or leave `ZONE_NAME` unset and add the route by hand. |
| `d1 migrations apply` says there is nothing to apply | Every migration is already recorded | Normal on a repeat deploy. |
| Pages upload says files are already uploaded | Pages stores files by content hash | Normal. If the site looks stale, check the production branch. |
| `doppler` fails on an update prompt in a script | The CLI asks about a newer version with no terminal | Add `--no-check-version`, or update the CLI. |
| A file looks old after a deploy | A removed path still cached at the edge | Purge that URL (Caching > Configuration > Purge Cache > Custom Purge). Hashed files cannot be stale. |

### Pages or Worker

Workers & Pages > Create can make a **Pages** project or a **Worker**, and some
flows quietly make a Worker. The site must live in a Pages project. Tell them
apart: the dashboard URL contains `/pages/` for Pages and `/workers/` for a
Worker, and a Worker's overview shows a `workers.dev` URL while Pages shows
deployments, a production branch and Custom domains. A domain attached to a
placeholder Worker keeps serving that Worker whatever you upload to Pages.

To fix it: remove the custom domain from the Worker (Settings > Domains &
Routes), delete the placeholder Worker (not the click-counter Worker that
`wrangler deploy` creates later), create the Pages project with Direct Upload,
then add the custom domain on the Pages project.

### Production branch

`wrangler pages deploy ... --branch main` counts as a production deploy only when
the Pages project's production branch is also `main`. Otherwise every deploy is
filed as a preview on a branch alias. Check Pages project > Settings > Builds &
deployments > Production branch (the label can differ), then re-deploy. The new
deployment should be tagged Production.

### Adding the route by hand

If the token does not carry zone permissions, leave `ZONE_NAME` unset and create
the route yourself: Workers & Pages > the click-counting Worker > Settings >
Domains & Routes > Add > Route, with `<site-host>/e/*` and your zone. A deploy
without `ZONE_NAME` leaves a hand-made route alone. If you later set `ZONE_NAME`,
remove the hand-made route first if wrangler complains.

## Redirecting an old path

If the link page replaces an older page at `/socials` on the same host, the build
can write the redirect for you. In `site.yaml`:

```yaml
redirects:
  socials:
    status: 302
```

Build, and `out/_redirects` holds two lines (`/socials / 302` and
`/socials/ / 302`). Keep `302` while testing, then change to `301` at the
switch. Verified against Cloudflare's Pages redirects page, read 2026-11-08:
one rule per line, 301, 302, 303, 307 and 308 are redirects, the first matching
rule wins.

**Not verified.** The docs do not say whether the visitor's own query string is
carried to a destination that has none, and say nothing about loops. Confirm on
your first deploy that Pages applies both lines (with and without the trailing
slash), that the query string is carried, and that a redirect to `/` does not
loop or get rewritten by a zone rule:

```sh
curl -sI "https://<site-host>/socials?x=1" | grep -i -e '^HTTP' -e '^location'
```

## Next

[Instance repo and pinning the engine](03-instance-repo-and-pinning.md).
