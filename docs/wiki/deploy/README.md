<!-- SPDX-License-Identifier: Apache-2.0 -->
# Deploy

How to take Reach Around from a clean checkout to a live link page, keep it
current, and move to it from a link-in-bio service. Six short pages, in the
order you will need them.

| Page | What it answers |
|---|---|
| [1. Prerequisites and accounts](01-prerequisites.md) | What you need before you start, and what it costs to run |
| [2. First deploy (test)](02-first-deploy-test.md) | A test hostname, the Cloudflare pieces and the GitHub workflow |
| [3. Instance repo and pinning the engine](03-instance-repo-and-pinning.md) | Your private repository, `engine.lock`, forks, assets |
| [4. Secrets](04-secrets.md) | Where tokens live (Doppler, dev before prd) and what never goes in a repository |
| [5. Going live](05-going-live.md) | The DNS switch, rollback and the shutdown of the old service |
| [6. Updating](06-updating.md) | Engine releases, content changes, Worker changes, rollbacks |

## Note on the long reference

The older single file, [docs/deploy.md](../../deploy.md), remains the long
reference until this split is reviewed. It still holds the full variable tables,
the Worker and D1 budget details, the Live and page-view settings, caching and
the troubleshooting table. Where a page here says "see the long reference", that
is the file it means. Nothing in `deploy.md` has been removed or rewritten.

## Conventions on these pages

- Every value in angle brackets, such as `<site-host>` or `<doppler-project>`,
  is a placeholder for your own value. Real hostnames, account ids, database ids
  and tokens never go in a repository, an issue or a chat.
- **Verified** means read from a named source on the date given. **Inferred** or
  **unverified** means reasoned or read from code but not yet run, so check it on
  your first deploy. Every such mark from the long reference is carried over.
- The Paid plan on Cloudflare is the baseline. The earlier Free-first profiles
  are superseded (see [Prerequisites](01-prerequisites.md#plan-and-cost)).
