<!-- SPDX-License-Identifier: Apache-2.0 -->
# 1. Prerequisites and accounts

What to have in hand before the [first deploy](02-first-deploy-test.md).

## Accounts

| Account | Used for | Notes |
|---|---|---|
| GitHub | Your private instance repository, Actions, environments | A personal or organization account. The engine is public; your instance is private. |
| Cloudflare | Pages (the static page), a Worker (click and page-view counting), D1 (the database) | The zone for your domain should be on Cloudflare if you want the click route (`/e/*`) created for you. |
| Doppler (or another secret manager) | Holds the deploy token and Worker secrets | See [Secrets](04-secrets.md). GitHub environment secrets work as a fallback. |
| A domain you control | The test host now, the production host later | A subdomain only needs a CNAME. An apex host needs its zone on Cloudflare. |

## Tools on your machine

- `git`, and the GitHub CLI (`gh`) if you script repository setup.
- Docker or Podman, to build the page locally from the pinned engine image.
- Node.js and `npx wrangler`, only if you deploy from your own machine instead
  of from Actions.
- `doppler`, if you use Doppler for the deploy token.
- `dig` and `curl`, for the checks on the [going live](05-going-live.md) page.

## Plan and cost

The Cloudflare **Paid** plan is the baseline, decided 2026-11-19. The Worker's
default write quota, the click ceiling and the funnel caps are sized for it.

**Superseded:** the earlier Free-first profiles (a Free-plan quota, a lower
`free` funnel cap profile and a one-site rollout on Free, decided 2026-11-08)
are no longer the default. The long reference keeps them only for a deployment
that deliberately sets the Free plan's quota. For what Paid costs and what
breaks on Free, see [Costs](../Costs.md).

The cap-and-quota arithmetic is checked by the deploy workflow and refuses a
combination that does not fit. It is in the long reference, under "Kit limits
and the daily write budget" in [docs/deploy.md](../../deploy.md).

## What you decide first

1. A **test hostname** such as `links-test.<your-domain>` and, later, a
   production hostname such as `links.<your-domain>`.
2. The **Pages project name** (for example `<pages-project>`) and the **D1
   database name** (the engine's `worker/wrangler.toml` uses
   `reach-around-clicks`).
3. Whether the **click route** is wanted. Without a zone route for `/e/*` on the
   site host the page works but clicks are not counted.
4. Where the **secrets** live ([page 4](04-secrets.md)). Decide this before the
   first deploy so no token is ever pasted anywhere else.

## Checklist

- [ ] A private GitHub repository exists for your instance.
- [ ] The Cloudflare account is on the Paid plan (or you have chosen to run on
      Free knowing the click ceiling does not fit there).
- [ ] The zone for your domain is on Cloudflare, or you will add DNS by hand.
- [ ] Doppler (or your secret manager) has a project for this product with
      `dev` and `prd` environments.
- [ ] You know who the required reviewer on each GitHub environment will be.
