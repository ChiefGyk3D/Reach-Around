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
   workflow uploads the already-built site. Note the project name. Check that
   you really got a Pages project and not a Worker (see "Pages or Worker?"
   below), and that its production branch is `main` (see "Production
   branch").
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
6. **API token.** My Profile > API Tokens > Create Token. Use a **user**
   token, not an account-owned one (see "Token type" below for why). Start
   from the "Edit Cloudflare Workers" template or Create Custom Token. Grant
   only:

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

   Without `ZONE_NAME` neither is needed. If you would rather not grant the
   zone permissions at all, add the route by hand instead (see "Adding the
   route by hand").

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

To store the token, let Doppler read the value from its prompt instead of
putting it on the command line (a value typed as an argument lands in your
shell history and the process list):

```
doppler secrets set CLOUDFLARE_API_TOKEN --project reach-around --config stg
```

Do the same for `dev` and `prd` as needed. If the Doppler CLI nags about a
newer version and a non-interactive run fails because of it, add
`--no-check-version` to the command (for example
`doppler --no-check-version run ...`). `--only-secrets` hands the child
command only the secrets you name, so an unrelated secret in the config never
reaches wrangler.

## Pages or Worker?

Workers & Pages > Create can produce either a **Pages** project or a
**Worker**, and some dashboard flows (a Git import, or a "Create application"
path that never offered Direct Upload) quietly make a Worker. The site must
live in a Pages project, because that is what `wrangler pages deploy` writes
to and what a custom domain has to point at.

How to tell, from the dashboard:

- The URL path contains `/pages/` for a Pages project and `/workers/` for a
  Worker.
- A Worker's overview page shows a **Worker URL** (`...workers.dev`). A Pages
  project shows deployments, a production branch and Custom domains.

Why it matters: the custom domain must be attached to the Pages project. A
domain attached to a placeholder Worker keeps serving that Worker, whatever
you upload to Pages, and `wrangler pages deploy` can even report success.

To fix it:

1. On the Worker, open Settings > Domains & Routes and remove the custom
   domain.
2. Delete the placeholder Worker. This is the Worker created by mistake, not
   the `reach-around-clicks` click counter that `wrangler deploy` creates
   later.
3. Create the Pages project with Direct Upload (section 1, step 1).
4. On the Pages project, Custom domains > Set up a custom domain, and add the
   hostname there.

## Production branch

`wrangler pages deploy ... --branch main` only counts as a production deploy
when the Pages project's production branch is also `main`. Otherwise
Cloudflare files every deploy as a **preview** (served on a branch alias such
as `main.reach-around-test.pages.dev`), and the custom domain keeps showing
the last production deployment, or nothing.

Check and change it: Pages project > Settings > Builds & deployments (the
label can differ between dashboard versions) > Production branch. It must read
`main`. Then re-run the deploy. In the project's Deployments list the new
deployment should be tagged Production.

## Token type

The Workers Routes permission (needed only when you set `ZONE_NAME`) is a
zone permission. In the current dashboard an **account-owned** token (Manage
Account > Account API Tokens) cannot carry zone permissions, so use a
**user** token: My Profile > API Tokens > Create Token.

1. Start from the "Edit Cloudflare Workers" template. It includes Workers
   Scripts and Workers Routes.
2. Add Account > Cloudflare Pages: Edit and Account > D1: Edit.
3. Under Account Resources include only your one account, and under Zone
   Resources only the one zone.
4. Set a TTL (an end date) so a forgotten token expires on its own. Add a
   client IP filter if your address is stable.

Check the token without printing it. These commands read it from Doppler (or
from an environment variable you already exported) and pass it to `curl`
through standard input, so it appears neither on screen nor in the process
list:

```
doppler run --project reach-around --config stg --only-secrets CLOUDFLARE_API_TOKEN -- \
  sh -c 'printf "header = \"Authorization: Bearer %s\"\n" "$CLOUDFLARE_API_TOKEN" \
    | curl -sS -K - https://api.cloudflare.com/client/v4/user/tokens/verify \
    | jq "{success, status: .result.status, expires_on: .result.expires_on}"'
```

`"status": "active"` means the token is valid. An account-owned token uses a
different endpoint, `https://api.cloudflare.com/client/v4/accounts/<account
id>/tokens/verify`. Outside Doppler, drop the first line and run the `sh -c`
part with the token already in `CLOUDFLARE_API_TOKEN` in your environment
(set it with `read -rs CLOUDFLARE_API_TOKEN && export CLOUDFLARE_API_TOKEN`
so it is not echoed). The verify call proves the token is alive, not which
permissions it has; a missing permission shows up as an error on the matching
wrangler step.

## A private deploy script

The workflow above suits a test hostname. For a real instance, many people
prefer to deploy from their own machine with a script kept outside the
repository, because the real ids, hostname and site config stay private.
[`docs/examples/deploy-private.sh`](examples/deploy-private.sh) is a
sanitized, generic version (placeholders only). It does this:

1. **Builds outside the repository**, into a private directory, with
   `reach-around build`. No token exists in the process during the build.
2. **Derives the link ids from the build manifest** (`jq -c '.links | keys'`),
   so the Worker accepts exactly the links the page publishes and the two
   cannot drift.
3. **Generates the wrangler config in the private directory** from
   `worker/wrangler.toml`, replacing the placeholder ids and making `main`
   and `migrations_dir` absolute because the file no longer sits in
   `worker/`.
4. **Checks the shape of every value** (UUID, number, hostname, project name,
   32-character account id, link-id array) before it lands in a file.
5. **Offers `--dry-run`**, which builds, writes the config and prints the
   plan, then stops before any wrangler command. It also asks you to type
   `deploy` to confirm unless you pass `--yes`.
6. **Gives the token only to the three wrangler commands** (migrations,
   Worker deploy, Pages deploy), each wrapped in `doppler run --only-secrets`.
   The script unsets any token inherited from your shell.

Try it with `--dry-run` first:

```
cp docs/examples/deploy-private.sh ~/private/deploy.sh
chmod 700 ~/private/deploy.sh
~/private/deploy.sh --dry-run ~/private/deploy.settings
```

How it differs from the CI workflow:

| | `deploy-test.yml` | private script |
|---|---|---|
| Where it runs | GitHub runner | your machine |
| Values | environment variables in GitHub | a private settings file |
| Token | `test` environment secret | Doppler, injected per command |
| Approval | environment reviewer | typed confirmation |
| Link ids | copied by hand into `LINK_IDS` | read from the build manifest |
| Config file | `wrangler.deploy.toml` in `worker/` on the runner | in the private directory |
| Provenance | Actions run log | none beyond your shell history |

## Adding the route by hand

Clicks are counted only if `/e/*` on the site host reaches the Worker. If you
do not want the token to carry Workers Routes and Zone permissions, leave
`ZONE_NAME` unset and create the route yourself: dashboard > Workers & Pages
> the `reach-around-clicks` Worker > Settings > Domains & Routes > Add >
Route. Enter the route `links-test.example.invalid/e/*` and pick the zone.

The deploy leaves a hand-made route alone: without `ZONE_NAME` the generated
config has no `[[routes]]` entry, so `wrangler deploy` does not manage routes
and does not remove yours. (If you later set `ZONE_NAME`, wrangler will try to
create the same route; remove the hand-made one first if it complains.)

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| The custom domain serves old or different content | The domain is attached to a Worker, not the Pages project; or the deploy went to a preview because the production branch is not `main` | See "Pages or Worker?" and "Production branch". Check the newest deployment is tagged Production. |
| Route rejected: "No access to the specified resource" | The token lacks Workers Routes: Edit or Zone: Read on that zone, or it is an account-owned token, which cannot carry zone permissions | Use a user token with the zone permissions (see "Token type"), or leave `ZONE_NAME` unset and add the route by hand. |
| `d1 migrations apply` says there is nothing to apply | Every migration is already recorded in the database | Normal on a repeat deploy. To check, run `wrangler d1 migrations list DB --remote --config wrangler.deploy.toml`. |
| Pages upload says files are already uploaded | Pages stores files by content hash and skips the ones it has | Normal and harmless. A deployment is still created. If the site looks stale, check the production branch. |
| `doppler` fails on an update prompt in a script | The CLI asks about a newer version and there is no terminal to answer | Add `--no-check-version`, or update the CLI. |
| A GitHub runner stalls (queued, or stuck on one step) | A hosted-runner hiccup. The deploy concurrency group does not cancel in-progress runs, so a stuck run also blocks the next | Cancel it (Actions > the run > Cancel workflow, or `gh run cancel <run id>`), then `gh run rerun <run id>` or start Run workflow again. |

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
