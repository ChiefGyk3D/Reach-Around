# Deploying to the test subdomain

This is the one-time setup the maintainer does in Cloudflare and GitHub, and
how the manual workflow `.github/workflows/deploy-test.yml` uses it. The
workflow deploys the click-counting Worker and the static link page to a
**test** hostname. The production hostname is a separate cutover.

Every name below is a placeholder. Replace `example.invalid` style values with
your own; never commit real account ids, database ids or tokens.

| Placeholder | Meaning |
|---|---|
| `links-test.example.invalid` | the test hostname you choose |
| `reach-around-test` | the Pages project name |
| `reach-around-clicks` | the D1 database name (matches `worker/wrangler.toml`) |

## 1. Cloudflare steps

1. **Pages project.** Dashboard > Workers & Pages > Create > Pages > Direct
   Upload. Name it `reach-around-test` and create it with any placeholder
   file. Direct Upload means Cloudflare never builds from your repository; the
   workflow uploads the already-built site. Note the project name.
2. **D1 database.** Dashboard > Storage & Databases > D1 > Create database,
   named `reach-around-clicks`. Copy the database id (a UUID).
3. **Rate limiting namespace.** The Worker's `CLICK_LIMITER` binding needs a
   namespace id: any positive integer you pick that is unique within your
   account (for example `1001`). It is not a secret and nothing is created in
   the dashboard.
4. **Account id.** Dashboard > Workers & Pages overview, right-hand column.
   It identifies the account; it is not a secret, but keep it out of the
   repository.
5. **Test hostname.** Pick `links-test.example.invalid` (use a hostname in a
   zone you control, for example `links-test.<your-domain>`). Attach it to the
   Pages project: Pages project > Custom domains > Set up a custom domain.
   When the zone is on Cloudflare this creates the DNS record for you. If you
   add it by hand, create a proxied `CNAME` from the test hostname to
   `reach-around-test.pages.dev`.
6. **API token.** My Profile > API Tokens > Create Token > Create Custom
   Token. Grant only:

   | Scope | Permission | Why |
   |---|---|---|
   | Account | Cloudflare Pages: Edit | `wrangler pages deploy` |
   | Account | D1: Edit | `wrangler d1 migrations apply` |
   | Account | Workers Scripts: Edit | `wrangler deploy` |
   | User | User Details: Read | wrangler identifies the token owner |

   Under Account Resources, include only your one account. Add a client IP
   filter and a short expiry if you can. Do not use the Global API Key. If a
   wrangler step reports a missing permission, add that one permission and
   nothing broader.

   Only if you set `ZONE_NAME` (section 2), also grant these two, limited to
   the one zone under Zone Resources:

   | Scope | Permission | Why |
   |---|---|---|
   | Zone | Workers Routes: Edit | `wrangler deploy` creates the `/e/*` route |
   | Zone | Zone: Read | wrangler looks up the zone by name |

   Without `ZONE_NAME` neither is needed.

## 2. GitHub steps

1. Repository > Settings > Environments > New environment named `test`.
   - Add yourself under **Required reviewers** so every deploy needs a click.
   - Under **Deployment branches**, choose "Selected branches" and allow only
     `main`. The workflow also refuses any other ref on its own.
2. In the `test` environment add this **secret**:
   - `CLOUDFLARE_API_TOKEN`: the token from step 1.6.
3. In the same environment add these **variables** (not secret, but still
   keep real values out of commits):

   | Variable | Value | What it does |
   |---|---|---|
   | `CLOUDFLARE_ACCOUNT_ID` | account id | selects the account for wrangler |
   | `PAGES_PROJECT` | `reach-around-test` | Pages project to publish to |
   | `D1_DATABASE_ID` | the D1 UUID | written into a throwaway deploy config |
   | `RATELIMIT_NAMESPACE_ID` | `1001` | rate limiter namespace |
   | `SITE_HOST` | `links-test.example.invalid` | the only origin the Worker accepts clicks from |
   | `LINK_IDS` | `["example-one","example-two"]` | link ids the Worker accepts; copy them from the build manifest (`site-out.manifest.json`, beside the site folder, never inside it) |

   Optional variable, set it to count clicks:

   | Variable | Value | What it does |
   |---|---|---|
   | `ZONE_NAME` | `example.invalid` | the Cloudflare zone that holds `SITE_HOST`; adds a `[[routes]]` entry for `SITE_HOST/e/*` to the throwaway deploy config |

   **Without `ZONE_NAME` the page works but clicks are not counted.** The
   click pings go to `/e/c/<id>` on the site's own host, and only a zone route
   sends that path to the Worker. When set, `ZONE_NAME` must be a plain
   hostname (`a-z`, `0-9`, `.`, `-`); when unset, the deploy config is
   unchanged.

   The workflow validates each value's shape before using it and fails with a
   clear message if a required one is missing.

## Secrets and Doppler

Project secrets (this Cloudflare token, later the Mailgun and collector
tokens) live in the Doppler project `reach-around`, with configs `dev`, `stg`
and `prd`. For a local deploy, read the token from the `stg` config:

```
doppler run --project reach-around --config stg --only-secrets CLOUDFLARE_API_TOKEN -- <command>
```

The CI workflow still reads `CLOUDFLARE_API_TOKEN` from the GitHub `test`
environment secret until issue #98 moves it to Doppler OIDC. Never paste a
token into chat, issues or commits.

## 3. Run it

Actions > Deploy to test > Run workflow, branch `main`. The workflow has two
jobs:

1. **`build`** has read-only repository access, no secrets and no
   environment. It installs the package, runs
   `reach-around build --config examples/instance/site.yaml --out site-out`,
   and uploads `site-out` as an artifact. The build also writes the link
   manifest to `site-out.manifest.json`, beside the site folder and never
   inside it, so Pages cannot publish it. It goes up as a second artifact,
   `site-manifest`.
2. **`deploy`** waits for `build`, then waits for a reviewer to approve the
   `test` environment. It downloads both artifacts (the configuration check warns when `LINK_IDS`
   and `site-out.manifest.json` disagree), installs the Worker
   dependencies with `npm ci --ignore-scripts`, applies D1 migrations,
   deploys the Worker, and publishes the site with `wrangler pages deploy`.

Why two jobs: `pip install` and `npm ci` run code from third-party
dependencies. Neither runs anywhere the Cloudflare token exists. The token is
set only on the configuration check and the three wrangler steps (migrations,
Worker deploy, Pages deploy); it is not in workflow-level or job-level `env`,
is never echoed, and is not passed to later steps. The `test` environment is
where you add a required reviewer (section 2), so a person approves each
deploy before the secret is released to the `deploy` job.

The committed `worker/wrangler.toml` keeps its fake ids; the workflow writes a
temporary `wrangler.deploy.toml` on the runner only.

Note: the build command arrives in plan Task 6 (and `pyproject.toml` in Task
1). Until both are on `main`, the build step fails by design.

## 4. Rolling back

- **Site.** Pages project > Deployments > pick the previous deployment >
  Rollback to this deployment. Or re-run the workflow from an earlier good
  commit on `main`.
- **Worker.** Worker > Deployments > Rollback, or `wrangler rollback` with the
  token. Pause clicks entirely by deleting the Worker; links keep working
  because the page uses `ping` plus a beacon, not a redirect.
- **D1 migrations.** Migrations are forward only. Ship a new migration to
  undo a change; as a backstop, `wrangler d1 export reach-around-clicks
  --remote` before schema changes.
- **DNS.** Remove the custom domain from the Pages project (or delete the
  `CNAME`). The test hostname stops resolving; nothing else is affected.
- **Token.** If the token leaks or you are unsure, roll or delete it in
  Cloudflare, then update the secret.
- **Stop all deploys.** Disable the workflow in the Actions tab, or remove
  the `test` environment secret.
