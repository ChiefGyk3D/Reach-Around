# SPDX-License-Identifier: Apache-2.0
#
# Zone-wide bot management settings for Reach Around (issue #613). PLAN ONLY BY
# POLICY.
#
# This module is apply-capable but apply-prohibited: the resource below is an
# ordinary resource, so a hand-typed apply with a write token would change the
# zone. What prevents that is policy and structure, not the module: only a
# read-only token is issued, no workflow holds a credential, and
# scripts/check-edge.py --offline fails CI if anything but this one resource, bound
# exactly to desired-state.json, appears here (docs/edge.md, "Policy, not
# capability").
#
# The apply path is NOT approved. Two things must be proven first:
#   1. what request body provider 5.29.0 really sends for this resource. The
#      REST call is a PUT of the whole bot_management object, so a field this
#      file does not set (enable_js, ai_agents, sbfm_* ...) might be reset. Either
#      capture the exact request, or run an isolated-zone experiment showing the
#      unrepresented fields survive. Until then nothing here is applied.
#   2. that the zone is safe to change: the staging site shares the zone with
#      production, so every setting here reaches both.
# Nothing here runs an apply, and no workflow does either.
#
# desired-state.json is the single source that scripts/check-edge.py and the
# tests also read. Every field the provider can send is listed there (and in the
# reviewed provider-fields.json manifest), either with a desired value or as
# {"unmanaged": true}. An unmanaged field is not configured in this file and is
# not drift-checked. Whether an apply would still send it is unverified, which is
# why the apply path stays prohibited.
#
# The rate rule on /e/ is deliberately NOT a resource here. A cloudflare_ruleset
# resource owns the whole http_ratelimit phase, and on a shared zone applying it
# would delete other sites' rules. The rule stays in desired-state.json and is
# added by hand (docs/edge.md, "Rate rule: a MANUAL step").

locals {
  desired = jsondecode(file(join("/", [path.module, "desired-state.json"])))
  bot     = local.desired.bot_management
}

# AI crawler policy + managed robots.txt + Bot Fight Mode (kept off).
# REST: PUT /zones/{zone_id}/bot_management (permission Bot Management Write).
resource "cloudflare_bot_management" "zone" {
  zone_id = var.zone_id

  ai_training                 = local.bot.ai_training
  aisearch                    = local.bot.ai_search # the provider spells it without the underscore
  ai_user                     = local.bot.ai_user
  ai_bots_protection          = local.bot.ai_bots_protection
  bot_preference_sync_enabled = local.bot.bot_preference_sync_enabled
  is_robots_txt_managed       = local.bot.is_robots_txt_managed
  crawler_protection          = local.bot.crawler_protection
  content_bots_protection     = local.bot.content_bots_protection
  fight_mode                  = local.bot.fight_mode
}
