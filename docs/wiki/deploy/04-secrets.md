<!-- SPDX-License-Identifier: Apache-2.0 -->
# 4. Secrets

Rules first, then the layout, then the commands.

1. **A secret never goes in a repository**, an issue, a pull request, a log or a
   chat. A private repository is not an exception: it limits who reads the file,
   not who reads a clone or a log.
2. **Doppler dev before prd.** A new project gets its secrets in the `dev`
   environment first. Use `prd` only after you have decided the project is live in
   production, as its own step with its own access.
3. **Least privilege, one identity per purpose and tier.** A `dev` identity must
   not be able to read `prd`.
4. **Never put a token on a command line.** A value typed as an argument lands in
   your shell history and the process list. Let the tool read it from a prompt or
   from its own store.

## Layout

One Doppler project per product, with environments `dev` and `prd` always (and
`stg` only if you want a staging tier). Configs live inside those environments.
Service accounts and CI identities get access per environment and config. Design
where the secrets of a fully developed product end up from the first commit.

| Secret | Where it lives | Notes |
|---|---|---|
| Cloudflare deploy token | `<doppler-project>`, config `dev` (later `prd`) | Used by the three wrangler steps only. Prefer a rotated pair of tokens over a hand-made one. |
| Worker secrets (the hashing key, others the config needs) | Set on the Worker with `npx wrangler secret put` | Never in `wrangler.toml`, a variable or a file. |
| Social-count keys (optional) | A separate Doppler project that holds social-media credentials only | Read-only API keys for public counts. Never the shared CI project. |

## Where the deploy token comes from

Two paths, in order of preference.

**Doppler over GitHub OIDC (preferred).** The workflow asks Doppler for one name
only, `CLOUDFLARE_KEY_VALUE`, over a short-lived OIDC identity. The value is
masked and returned as one step output, and nothing is written to the job
environment; only the steps that map it into their own `env:` (the wrangler steps)
can read it. No long-lived token sits in GitHub. The reusable Worker deploy works
this way and reads no GitHub secret.

**A GitHub environment secret (fallback, and what the template does today).** The
shipped template reads a secret named `CLOUDFLARE_API_TOKEN` on the environment.
The environment has a required reviewer and a `main`-only deployment branch rule,
so the secret is released to the deploy job only after a person approves. The
Doppler option is in the template as disabled comments until the engine's issue
#98 lands. A `DOPPLER_TOKEN` service token in GitHub is emergency-only: it is a
long-lived credential, so do not leave it set while OIDC works.

## The Doppler identity

Create a Doppler service account with Viewer on one project and one config only
(for example `<doppler-project>` config `dev`), and an OIDC identity on it with:

- issuer `https://token.actions.githubusercontent.com`
- audience `https://github.com/<owner>`
- one subject, in the form your repository actually emits

Check which subject form is in use:

```sh
gh api repos/<owner>/<repo>/actions/oidc/customization/sub
```

The immutable form includes the numeric owner and repository ids, so a renamed or
recreated repository cannot reuse it:

```text
repo:<owner>@<owner-id>/<repo>@<repo-id>:environment:<exact-environment>
```

If the command shows the default form instead, register
`repo:<owner>/<repo>:environment:<exact-environment>` alone. Register only the
form your repository emits; an extra form is an extra way in. Put the identity
UUID (not a secret) in the repository variable `DOPPLER_IDENTITY_ID`.

The subject names an environment, not a workflow, so any workflow in the
repository that requests `id-token: write` and targets that environment on an
allowed branch gets the same subject. The real limit is who can write to `main`.
Restrict the environment to `main`, require a reviewer and use `CODEOWNERS` on
`.github/workflows/`.

**Verified.** Doppler supports custom claim rules (docs.doppler.com, Service
Account Identities, read 2026-11-07). **Inferred, not tested:** that adding
`job_workflow_ref` as a custom claim is accepted and matches. **Unverified for a
called workflow:** which claims the token carries when the job runs in a reusable
workflow (the subject uses the environment either way). Check the claims of a
real token before relying on any pin beyond `sub`, and confirm a run still
authenticates.

## Commands

Run a command with only the one secret it needs, so an unrelated secret in the
config never reaches wrangler:

```sh
doppler run --project <doppler-project> --config dev --only-secrets CLOUDFLARE_KEY_VALUE -- <command>
```

Store a token by hand under the name the deploy reads. Doppler reads the value
from its prompt; the trailing redirect keeps the command from echoing it:

```sh
doppler secrets set CLOUDFLARE_KEY_VALUE --project <doppler-project> --config dev > /dev/null
```

Set a Worker secret (the Worker's hashing key is required on every Worker that
counts clicks, 32 or more random bytes, base64):

```sh
npx wrangler secret put VIEW_HASH_SECRET
```

If the Doppler CLI asks about a newer version and a non-interactive run fails,
add `--no-check-version`:

```sh
doppler --no-check-version run --project <doppler-project> --config dev --only-secrets CLOUDFLARE_KEY_VALUE -- <command>
```

## Check a token without printing it

This reads the token from Doppler and gives it to `curl` on standard input, so it
appears neither on screen nor in the process list. `"status": "active"` means the
token is alive; it does not prove which permissions it has (a missing permission
shows up as an error on the matching wrangler step).

```sh
doppler run --project <doppler-project> --config dev --only-secrets CLOUDFLARE_KEY_VALUE -- sh -c 'printf "header = \"Authorization: Bearer %s\"\n" "$CLOUDFLARE_KEY_VALUE" | curl -sS -K - https://api.cloudflare.com/client/v4/user/tokens/verify | jq "{success, status: .result.status, expires_on: .result.expires_on}"'
```

An account-owned token uses a different endpoint,
`https://api.cloudflare.com/client/v4/accounts/<account-id>/tokens/verify`.

## Rotation and compromise

Keep two tokens valid at once so one can be rotated without downtime. If a token
leaks or you are unsure, revoke both in Cloudflare at once, issue a new pair and
update the store. To rotate automatically, add a Doppler Cloudflare Tokens rotated
secret in `dev` (it provides `CLOUDFLARE_KEY_NAME` and `CLOUDFLARE_KEY_VALUE`) and
create the two tokens it rotates with identical permissions. The deploy uses
`CLOUDFLARE_KEY_VALUE` as the API token directly, so do not also add a
`CLOUDFLARE_API_TOKEN` secret to `dev`.

## Masking

The Doppler action masks the token before handing it over, and the workflow
passes it only through the `env:` of the wrangler steps. **Unverified:** that the
masking holds for every way a step could print it. A step that receives the token
can read it, which is why only wrangler steps get it.

## Next

[Going live](05-going-live.md).
