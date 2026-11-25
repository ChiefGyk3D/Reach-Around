<!-- SPDX-License-Identifier: Apache-2.0 -->
# Edge settings as code (issue #613)

The Cloudflare zone that fronts a Reach Around site carries a few settings that
no file in the site's own repository can set: how AI crawlers are treated, the
managed `robots.txt`, one rate limit on the analytics routes, and Bot Fight
Mode. This page is the plan and the runbook. The code is in
[`deploy/edge/`](../deploy/edge/); the single source of the desired state is
[`deploy/edge/desired-state.json`](../deploy/edge/desired-state.json).

**These settings are zone-wide, and the staging site shares the zone with
production (owner decision, 2026-11-23).** Every setting here reaches both, and
other sites on the zone.

**Nothing here applies itself, and nothing may be applied yet.** The apply path
is NOT approved (see [the apply path](#the-apply-path-is-not-approved)).
This repository holds the desired state, the policy checks and the read-only
drift check only; the plan runs inside the project's infrastructure pipeline
(see [Where the plan runs](#where-the-plan-runs)). No workflow here runs an apply. The module is
apply-capable but apply-prohibited (see [Policy, not capability](#policy-not-capability)).

Cloudflare documentation read 2026-11-23 (page dates are Cloudflare's own
`dateModified`):

| Topic | Page | Modified |
|---|---|---|
| AI crawler policy | https://developers.cloudflare.com/bots/additional-configurations/block-ai-bots/ | 2026-11-02 |
| Managed robots.txt | https://developers.cloudflare.com/bots/additional-configurations/managed-robots-txt/ | 2026-11-02 |
| AI Crawl Control | https://developers.cloudflare.com/ai-crawl-control/ and `.../features/manage-ai-crawlers/` | 2026-08-14, 2026-07-28 |
| Rate limiting | https://developers.cloudflare.com/waf/rate-limiting-rules/ and `.../parameters/` | 2026-08-25, 2026-11-04 |
| Bot Fight Mode | https://developers.cloudflare.com/bots/get-started/bot-fight-mode/ | 2026-08-03 |
| Bot Management API | https://developers.cloudflare.com/api/resources/bot_management/methods/update/ | read 2026-11-23 |

## Desired state

### Bot management (`PUT /zones/{zone_id}/bot_management`)

Managed fields, each with a desired value:

| Key | Value | Meaning |
|---|---|---|
| `ai_training` | `disallow` | Training crawlers: `robots.txt` Disallow, plus an edge block while Bot Preference Sync is on. Mixed-purpose crawlers are excluded from the edge block. |
| `ai_search` | `disabled` | Search crawlers stay allowed. (The provider spells it `aisearch`.) |
| `ai_user` | `disabled` | Assistant and agent fetches (the Agent policy) stay allowed. This is where link-preview style fetches would land if Cloudflare classes them as agents; check after the first change. |
| `ai_bots_protection` | `disabled` | The blanket "block AI scrapers and crawlers" rule stays off, because it is not limited to training. |
| `bot_preference_sync_enabled` | `true` | Serves the managed `robots.txt` directives and enables the edge block. |
| `is_robots_txt_managed` | `true` | Cloudflare prepends its managed block to the site's own `robots.txt`. |
| `crawler_protection` | `disabled` | No link maze. |
| `content_bots_protection` | `disabled` | No extra content-bot rule. |
| `fight_mode` | `false` | Bot Fight Mode stays off. On a Free zone it cannot be skipped or exempted by a rule, so it may challenge browser beacons. Revisit only after real beacon traffic is measured on the test host. |

Unmanaged fields. The provider's `cloudflare_bot_management` resource (5.29.0)
can send 22 fields. The 13 below are listed in `desired-state.json` as
`{"unmanaged": true}`. That means only that this repository does not configure
them (`main.tf` never sets them) and does not drift-check them. **Whether an
apply would still send them, as an omitted optional attribute, a default or a
refreshed value, is unverified.** That is the reason the apply path stays
prohibited. The offline check fails if any text here, in `main.tf` or in
`desired-state.json` promises that such fields are safe from an apply.

`ai_agents`, `ai_bots_migration_opt_out`, `auto_update_model`,
`bm_cookie_enabled`, `cf_robots_variant`, `enable_js`,
`jsd_api_results_enabled`, `optimize_wordpress`, `sbfm_definitely_automated`,
`sbfm_likely_automated`, `sbfm_static_resource_protection`,
`sbfm_verified_bots`, `suppress_session_score`.

The list of 22 is checked against a reviewed manifest,
[`deploy/edge/provider-fields.json`](../deploy/edge/provider-fields.json),
generated from the provider's own schema (`tofu providers schema -json`, piped
through `scripts/gen-provider-fields.py`; the command is recorded in the file).
The offline check requires the manifest, the field list in `check-edge.py` and
`desired-state.json` to agree with each other independently, and the manifest's
provider version to equal the pin. A field cannot be dropped from two of the
three unnoticed. The manifest itself is only as current as its last
regeneration: regenerate and review the diff on every provider bump.

AI Crawl Control is on for every plan with no setting to change. Pay Per Crawl
is a closed beta and is not used. Per-crawler Block actions in that dashboard
make a WAF custom rule; this plan enforces through the policy above and adds no
WAF custom rule.

### The apply path is NOT approved

The REST call is a PUT of the whole bot management object. It is not known what
request body provider 5.29.0 sends when an optional field is left out of the
configuration, so an unmanaged field such as `enable_js` may be reset to a
default. **The apply path is NOT approved until one of these is done and
recorded here:** the provider's exact request body is captured, or an
experiment on an isolated zone proves that unrepresented fields are preserved.
Because the staging site shares the zone with production, there is no safe zone
to try it on today. Until then use the pipeline's plan and the drift check only.

### Rate rule on `/e/t` (phase `http_ratelimit`): a MANUAL step

The desired rule is kept in `desired-state.json` and compared by the live
check, but the module does not manage it. A rules resource owns the whole
phase, and on a shared zone an apply would delete every other site's rules.

**Warning for the shared zone:** add this rule to the existing rules of the
`http_ratelimit` phase, in the dashboard or through the Rulesets API. Never replace
the phase, and never upload a ruleset that leaves out the rules already there. Read the phase first (`GET /zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint`)
and keep a copy.

| Key | Value |
|---|---|
| `ref` | `reach_around_mint_cap` |
| `description` | `Reach Around: edge cap on the beacon token path /e/t` |
| `expression` | `http.request.uri.path eq "/e/t"` (the regex `matches` operator is Business and up) |
| `action` | `block` (a beacon cannot solve a challenge) |
| `characteristics` | `cf.colo.id`, `ip.src` |
| `period` | `10` (the only Free value) |
| `requests_per_period` | `4` |
| `mitigation_timeout` | `10` (the only Free value) |

The rule rate limits the beacon token path only: 4 requests per 10 seconds per
client IP and edge location. Free cannot match on method, so it covers every
method on that exact path. The other `/e/` paths are covered by the in-Worker
limits, not by this rule. Counters are per data center and per address, so many
visitors behind one shared address could reach the limit; measure before
raising or lowering it. Free allows one rule in this phase, so if another
site's rule is already there, this one cannot be added on a Free zone: read the
plan limit first.

The live check finds the rule by `ref` (or, failing that, by `expression`),
reports whether it is present and correct, and treats every other rule in the
phase as someone else's: never drift.

## Blast radius and rollback

| Change | Reaches | Rollback |
|---|---|---|
| Bot management settings | Every host on the zone, including production and other sites. The managed `robots.txt` block is prepended to every host's `robots.txt` within seconds. | Restore the saved snapshot (`--save`), or set `ai_training` to `disabled` and `bot_preference_sync_enabled` to `false`; the managed block disappears within seconds. |
| Rate rule (manual) | Every request to the exact path `/e/t` on every host of the zone (Free cannot match on host). A blocked client gets Cloudflare's block page instead of the Worker's empty 204 for 10 seconds. | Delete only that rule in the dashboard. |
| Bot Fight Mode | Unchanged: the code asserts it is off. | Not applicable. |

## Token and calls (once, per zone)

One user API token per environment, limited to that one zone (an account-owned
token cannot carry zone permissions, see [deploy.md](deploy.md#token-type)), with an
end date, kept in the secret manager:

| Token | Permissions | Used for |
|---|---|---|
| `edge-read` (one per zone) | Bot Management Read, Zone WAF Read | `scripts/check-edge.py --live`; the pipeline's plan holds its own credential |
| `edge-write` | Bot Management Write (dashboard: Bot Management Edit), Zone WAF Write (Zone WAF Edit) | Not issued: the apply path is NOT approved, and the rate rule is a manual step in the dashboard. |

Calls the read token makes: `GET /zones/{zone_id}/bot_management` and
`GET /zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint`. No Zone Read
is needed because the zone id is an input. `--site-host` also fetches
`https://HOST/robots.txt` without a token.

## Policy, not capability

`deploy/edge/` is a mutable OpenTofu module: `cloudflare_bot_management.zone` is
an ordinary resource, and OpenTofu has no setting that makes a resource
plan-only. A hand-typed apply with a write token would change the zone. What
keeps that from happening is policy plus the controls below, and this page does
not claim more:

- Only the read-only token is issued; the write token is not (see the table above).
- `scripts/check-edge.py --offline` (CI) fails if `deploy/edge/` holds any
  resource other than `cloudflare_bot_management.zone`, any data source, module,
  provisioner, lifecycle block or other locals, or if any attribute of the
  resource is not assigned exactly the matching `local.bot` key from
  `desired-state.json` (a literal, a swapped key or a stray reference fails).
- The same check fails if `deploy/edge/` holds any file that is not on its list
  (a saved plan under any name, a state file). `deploy/edge/.gitignore` ignores
  everything not on the list.
- The same check fails if any reviewed file is missing (`variables.tf` included)
  or if the zone_id variable is not declared exactly once.
- `--live` runs the same validation of the desired state, the provider manifest,
  the pin and the module before it makes any request, and exits 2 if it fails.

## Structural controls

The text scans in `tests/test_edge_settings.py` (no apply, destroy or import
command anywhere in tracked text, no zone or account id committed) are
**defense in depth, not proof**: a regex can be dodged. What really enforces "nothing is
applied from here" is structural:

- CI has no Cloudflare credentials, so no CI job can reach the zone.
- Write tokens exist only in the secret manager and are not issued for this work.
- No workflow is given a Cloudflare token of any kind.
- The module has no rules resource, so even a mistaken run cannot touch other
  sites' rate rules.
- No file in this repository passes the API token to a program, and the offline
  check enforces the literal name repository-wide: every file except
  `scripts/check-edge.py` whose bytes contain `CLOUDFLARE_EDGE_READ_TOKEN` fails,
  whatever its suffix, depth or encoding. Only `docs/edge.md` (one reviewed path,
  not a suffix, since a script can source a `.txt` file) may name it;
  `tests/test_edge_settings.py` may not hold it at all, as a literal anywhere
  (a list or tuple element included, since a container can be subscripted,
  starred, iterated or passed to a call), as an identifier, f-string part or
  piece of a concatenation; its fixtures use `check.TOKEN_ENV`. Directories are skipped by name at the repository root only
  (`.git`, virtual environments, caches). A symlink to a directory is reported, a
  symlink to a file is followed to its target and refused if it is outside the
  repository, missing or not a regular file, and a FIFO, socket or device is
  reported and never opened. Files are read in full, opened without following
  links, and the opened file must match its path before and after the read
  (device, inode, size, link count, modification time and change time), so a file
  replaced, relinked or written in place during its own scan is reported. The
  guarantee is bounded: it rests on the filesystem's change time, which user space
  cannot set back but which can be coarse, and a file already scanned can still be
  changed afterwards, so run the check on a checkout no other step is changing.
  The name is searched as UTF-8 text and as UTF-16 and UTF-32 (both byte orders);
  other encodings are not decoded. A directory that cannot be listed is reported,
  never treated as clean, and the scan has generous bounds (64 MiB per file, 1 GiB
  in all): crossing one is a reported problem, not a clean result. There is no
  local launcher: no wrapper
  script, no CLI configuration file and no configurable path to a tool.
  `scripts/check-edge.py --offline` fails if `deploy/edge/` holds a launcher or
  CLI configuration, or any text that names a token variable, starts a subprocess
  or begins with a shebang. The one tool that sends the read token,
  `check-edge.py --live`, reads it from the environment itself and makes one HTTPS
  GET per call from Python: no subprocess and no executable lookup.
- The checker refuses to run unless the interpreter is isolated
  (`/usr/bin/python3 -I scripts/check-edge.py`). The file has no shebang and no
  executable bit, and the offline check fails if that changes, so no `env` or
  `PATH` lookup happens before the guard: an attacker-controlled `python3` on
  `PATH` is never handed the token. Name the interpreter by absolute path (the
  one the deployment image provides) whenever the token is in the environment. A
  normal interpreter honors `PYTHONPATH`, `PYTHONHOME` and site files before any
  line of the script, so a planted module could read the token first. The refusal
  comes before any other module is imported, and it runs on import as well as on
  execution: a non-isolated interpreter that holds the read token in its
  environment is refused at import (exit 2) before the next import line, so a
  planted `json.py` is never loaded. Every function that reads the token
  (`read_token`) checks isolation itself. A non-isolated import with no token in
  the environment is allowed, for the pure helpers and the tests. Python cannot
  protect what ran before the guard (a `sitecustomize`, a `.pth` file or a module
  already imported by the caller) or a caller that sets the token only after
  importing; those are the caller's trust. CI runs the offline step as
  `/usr/bin/python3 -I` (no `PATH` lookup) and holds no read token.
- The honest limit of that scan: it finds the literal name only (bytecode files
  are scanned like any other file; only the checker's own cache entry is exempt).
  A name built at run time, such as two string pieces joined, is not found and is
  out of scope; a test pins that limit. A token handed on through the
  environment of another process is not found either, so the scan is a tripwire
  for review, not a proof. The real control is that no other process is ever
  started with the token in its environment.
- `.github/CODEOWNERS` names `deploy/edge/`, the checker and its tests, but it
  only names a reviewer. It becomes a control only when the repository has a
  branch protection rule or ruleset on the default branch with "Require review
  from Code Owners" turned on (and, preferably, "Dismiss stale pull request
  approvals when new commits are pushed"). That is a repository setting, not
  something this change makes; until it is on, a change to the checker is not
  guaranteed a code owner review.
- The checker also pins the minimum OpenTofu version, the provider manifest's
  identity and computed-only fields, and the shape of the lock file entry.

## Where the plan runs

The plan runs only inside the project's infrastructure pipeline (Spacelift),
with that pipeline's own vetted OpenTofu binary and its own credentials. It is
not run from a developer machine, and this repository ships no script for it:
a script that handed the token to a local executable would be a way to leak it.
Plan output is read in the pipeline.

## Run order (each step needs the owner's explicit word)

1. Read the dashboard: the zone's plan, and which `http_ratelimit` rules and
   what `robots.txt` the zone already has.
2. `/usr/bin/python3 -I scripts/check-edge.py --live --zone-id ID --save snapshot.json`
   with the read token in `CLOUDFLARE_EDGE_READ_TOKEN`, to record what is
   there. Keep the file private.
3. Read the pipeline's plan. Changes planned are not an error.
4. Stop. Changing the zone waits for the apply path to be approved (above).
5. After any change: `check-edge.py --live --zone-id ID --site-host HOST` exits
   0; a link preview of the site still renders; monitoring still sees the site;
   a real page view still counts.

## Drift

`python3 -I scripts/check-edge.py --offline` runs in CI. It fails when the
desired state breaks an owner decision, when a provider field is missing from
`desired-state.json`, when `main.tf` sets an unmanaged field or defines a rules
resource, or when `main.tf`, the provider pin and the lock file stop matching.
`--live` compares the real zone and exits 1 on any difference, 2 when it could
not read (never read as green). Before any authenticated call it refuses to run,
with exit 2, if a proxy or trust-store override variable is set (`HTTPS_PROXY`,
`HTTP_PROXY`, `ALL_PROXY` in either case, `SSL_CERT_FILE`, `SSL_CERT_DIR`,
`REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`), and its API connection ignores
environment proxies entirely, so a proxy with its own CA cannot read the token. The `--site-host` fetch is bounded as a whole: at most 3 addresses are tried and one 30 second limit covers connecting, the handshake and reading, so it fails with a clear message instead of waiting. `--site-host` takes a plain public hostname
only: no IP, port, path or credentials. It resolves the name, refuses if any
address is loopback, private, link-local, multicast, reserved or unspecified,
connects to that address with the hostname as the certificate name, and follows
no redirect.

## Open owner decisions

None of these is settled by this change except where noted. The defaults below
are what the code assumes today.

| # | Decision | Current default |
|---|---|---|
| 1 | Zone layout: is the test host on its own zone? | Decided 2026-11-23: no, the staging site shares the zone with production. So nothing is applied and the zone keeps every existing rule. |
| 2 | Zone plan | Free assumed; the limits above depend on it. The real plan is read from the dashboard first. |
| 3 | State location | In the pipeline (Spacelift), not on a developer machine. |
| 4 | Code location: this repository (`deploy/edge/`) or a private infrastructure repository | This repository. |
| 5 | Rate threshold `requests_per_period` | 4; accept it or measure first. |
| 6 | Is the zone shared with other sites? Then their `robots.txt` and `/e/` paths change too. | Assumed shared. |
