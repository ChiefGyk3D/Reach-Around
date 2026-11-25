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
`deploy/edge/plan.sh` only plans. No workflow runs an apply.

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
to try it on today. Until then use `plan.sh` and the drift check only.

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
| `edge-read` (one per zone) | Bot Management Read, Zone WAF Read | `scripts/check-edge.py --live` and the plan |
| `edge-write` | Bot Management Write (dashboard: Bot Management Edit), Zone WAF Write (Zone WAF Edit) | Not issued: the apply path is NOT approved, and the rate rule is a manual step in the dashboard. |

Calls the read token makes: `GET /zones/{zone_id}/bot_management` and
`GET /zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint`. No Zone Read
is needed because the zone id is an input. `--site-host` also fetches
`https://HOST/robots.txt` without a token.

## Structural controls

The text scans in `tests/test_edge_settings.py` (no apply, destroy or import
command anywhere in tracked text, no zone or account id committed) are
**defense in depth, not proof**: a regex can be dodged. What really enforces "nothing is
applied from here" is structural:

- CI has no Cloudflare credentials, so no CI job can reach the zone.
- Write tokens exist only in the secret manager and are not issued for this work.
- No workflow is given a Cloudflare token of any kind.
- `plan.sh` starts the tool under `env -i` with an allow-list: a fixed `PATH`, a
  fresh scratch `HOME`, `TMPDIR` and data directory (removed again), `TF_VAR_zone_id`,
  `CLOUDFLARE_API_TOKEN` (read from a file descriptor, never on a command line) and
  `TF_CLI_CONFIG_FILE` pointing at the reviewed `deploy/edge/tofu.rc`, which allows
  only a direct install of the locked provider (no `dev_overrides`, mirrors or
  plugin cache). Nothing else reaches it: no proxy or CA variable, no other
  `TF_VAR_*`, no `TF_PLUGIN_CACHE_DIR`. The tool is found at a fixed absolute path
  (`/usr/local/bin`, `/usr/bin` or `~/.local/bin`, or `TOFU_BIN`), never through
  `PATH`, and refused unless it and its parent directories belong to root or you
  and others cannot write to them. The script's own helper commands run with the
  same cleaned environment. Exit 65 means no trusted binary.
- The module has no rules resource, so even a mistaken run cannot touch other
  sites' rate rules.

## Run order (each step needs the owner's explicit word)

1. Read the dashboard: the zone's plan, and which `http_ratelimit` rules and
   what `robots.txt` the zone already has.
2. `python3 -I scripts/check-edge.py --live --zone-id ID --save snapshot.json`
   with the read token, to record what is there. Keep the file private.
3. `TF_VAR_zone_id=ID CLOUDFLARE_API_TOKEN=... deploy/edge/plan.sh` and read it.
   Exit 2 means changes are planned; that is not an error. Exit 64 is a usage
   error.
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
environment proxies entirely, so a proxy with its own CA cannot read the token. `--site-host` takes a plain public hostname
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
| 3 | State location: local (git-ignored) or a Spacelift stack | Plan-only local runs, state git-ignored. |
| 4 | Code location: this repository (`deploy/edge/`) or a private infrastructure repository | This repository. |
| 5 | Rate threshold `requests_per_period` | 4; accept it or measure first. |
| 6 | Is the zone shared with other sites? Then their `robots.txt` and `/e/` paths change too. | Assumed shared. |
