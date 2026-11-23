<!-- SPDX-License-Identifier: Apache-2.0 -->
# Edge settings as code (issue #613)

The Cloudflare zone that fronts a Reach Around site carries a few settings that
no file in the site's own repository can set: how AI crawlers are treated, the
managed `robots.txt`, one rate limit on the analytics routes, and Bot Fight
Mode. This page is the plan and the runbook. The code is in
[`deploy/edge/`](../deploy/edge/); the single source of the desired state is
[`deploy/edge/desired-state.json`](../deploy/edge/desired-state.json).

**These settings are zone-wide.** They also cover every other site on the zone.
**Nothing here applies itself.** `deploy/edge/plan.sh` only plans; an apply is a
command the owner types after reading the plan, and no workflow runs one.

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

| Key | Value | Meaning |
|---|---|---|
| `ai_training` | `disallow` | Training crawlers: `robots.txt` Disallow, plus an edge block while Bot Preference Sync is on. Mixed-purpose crawlers are excluded from the edge block. |
| `ai_search` | `disabled` | Search crawlers stay allowed. |
| `ai_user` | `disabled` | Assistant and agent fetches (the Agent policy) stay allowed. This is where link-preview style fetches would land if Cloudflare classes them as agents; check after the first apply. |
| `ai_bots_protection` | `disabled` | The blanket "block AI scrapers and crawlers" rule stays off, because it is not limited to training. |
| `bot_preference_sync_enabled` | `true` | Serves the managed `robots.txt` directives and enables the edge block. |
| `is_robots_txt_managed` | `true` | Cloudflare prepends its managed block to the site's own `robots.txt`. |
| `crawler_protection` | `disabled` | No link maze. |
| `content_bots_protection` | `disabled` | No extra content-bot rule. |
| `fight_mode` | `false` | Bot Fight Mode stays off. On a Free zone it cannot be skipped or exempted by a rule, so it may challenge browser beacons. Revisit only after real beacon traffic is measured on the test host. |

AI Crawl Control is on for every plan with no setting to change. Pay Per Crawl
is a closed beta and is not used. Per-crawler Block actions in that dashboard
make a WAF custom rule; this plan enforces through the policy above and adds no
WAF custom rule.

### Rate limit on `/e/` (phase `http_ratelimit`)

| Key | Value |
|---|---|
| `ref` | `reach_around_beacon_flood_cap` |
| `description` | `Reach Around: edge flood cap on the /e/ analytics routes` |
| `expression` | `starts_with(http.request.uri.path, "/e/")` (the regex `matches` operator is Business and up) |
| `action` | `block` (a beacon cannot solve a challenge) |
| `characteristics` | `cf.colo.id`, `ip.src` |
| `period` | `10` (the only Free value) |
| `requests_per_period` | `30` |
| `mitigation_timeout` | `10` (the only Free value) |

The Worker already limits each client (20 clicks, 5 views, 6 tokens per 60
seconds), so one client cannot legitimately send more than a few requests in 10
seconds. 30 is a flood cap, not a fair-use cap. Counters are per data center and
per address, so many visitors behind one shared address could reach it; measure
on the test host before production and change `requests_per_period` there if
needed. Free allows one rule in this phase, so this rule owns the whole phase.

## Blast radius and rollback

| Change | Reaches | Rollback |
|---|---|---|
| Bot management settings | Every host on the zone, including other sites. The managed `robots.txt` block is prepended to every host's `robots.txt` within seconds. | Re-apply the saved snapshot (`--save`), or set `ai_training` to `disabled` and `bot_preference_sync_enabled` to `false`; the managed block disappears within seconds. |
| Rate rule | Every request to a path starting `/e/` on every host of the zone (Free cannot match on host). A blocked client gets Cloudflare's block page instead of the Worker's empty 204 for 10 seconds. | Delete the rule in the dashboard, or `tofu destroy -target=cloudflare_ruleset.beacon_rate_limit`. |
| Bot Fight Mode | Unchanged: the code asserts it is off. | Not applicable. |

## Token and calls (approve once, per zone)

One user API token per environment, limited to that one zone (an account-owned
token cannot carry zone permissions, see [deploy.md](deploy.md#token-type)), with an
end date:

| Token | Permissions | Used for |
|---|---|---|
| `edge-write` (test zone, then production zone) | Bot Management Write (dashboard: Bot Management Edit), Zone WAF Write (Zone WAF Edit) | `tofu apply` |
| `edge-read` (one per zone) | Bot Management Read, Zone WAF Read | `scripts/check-edge.py --live` and `tofu plan` |

Calls the write token makes: `GET` and `PUT /zones/{zone_id}/bot_management`;
`GET`, `PUT` or `POST` on `/zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint`
and its rules. No Zone Read is needed because the zone id is an input. The
tokens are stored in the secret manager, never in a GitHub secret.

## First run (owner's explicit word for each step)

1. Read the dashboard: the zone's plan, and whether an `http_ratelimit` rule or
   a managed `robots.txt` already exists.
2. `python3 -I scripts/check-edge.py --live --zone-id ID --save snapshot.json`
   with the read token, to record what is there. Keep the file private.
3. If a rate rule exists, import it first:
   `tofu import cloudflare_ruleset.beacon_rate_limit zones/ID/RULESET_ID`
   (OpenTofu otherwise assumes it owns the whole phase).
4. `TF_VAR_zone_id=ID CLOUDFLARE_API_TOKEN=... deploy/edge/plan.sh` and read it.
   Exit 2 means changes are planned; that is not an error.
5. The owner applies by hand, on the test zone first.
6. Check: `check-edge.py --live ... --robots-url https://HOST/robots.txt`
   exits 0; a link preview of the site still renders; monitoring still sees the
   site; a real page view still counts.

## Drift

`python3 -I scripts/check-edge.py --offline` runs in CI. It fails when the
desired state breaks an owner decision, or `main.tf`, the provider pin and the
lock file stop matching it. `--live` compares the real zone and exits 1 on any
difference, 2 when it could not read (never read as green). Run it by hand or
from a scheduled job that gets the read token from the secret manager.

## Open owner decisions

None of these is settled by this change. The defaults below are what the code
assumes today; each stays open until the owner decides.

| # | Decision | Current default |
|---|---|---|
| 1 | Zone layout: is the test host on its own zone? These settings are zone-wide, so "test first" is only real on a separate zone. Which zone ids (never committed). | Not decided. One zone id per run, passed as `TF_VAR_zone_id`. |
| 2 | Zone plan | Free assumed; the limits above depend on it. The real plan is read from the dashboard first. |
| 3 | State location: local (git-ignored) or a Spacelift stack | Plan-only local runs, state git-ignored. |
| 4 | Code location: this repository (`deploy/edge/`) or a private infrastructure repository | This repository. |
| 5 | Rate threshold `requests_per_period` | 30; accept it or measure on the test host first. |
| 6 | Is the zone shared with other sites? Then their `robots.txt` and `/e/` paths change too. | Assumed shared. |
