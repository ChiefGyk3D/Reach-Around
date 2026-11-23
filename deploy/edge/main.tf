# SPDX-License-Identifier: Apache-2.0
#
# Zone-wide edge settings for Reach Around (issue #613). Plan only by default:
# nothing here runs an apply, and no workflow does either. The desired state is
# desired-state.json, the single source that scripts/check-edge.py and the tests
# also read, so the three cannot disagree.
#
# These settings are ZONE-WIDE. They also cover every other site on the zone.

locals {
  desired = jsondecode(file("${path.module}/desired-state.json"))
  bot     = local.desired.bot_management
  rl      = local.desired.rate_limit
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

# One rate limiting rule on /e/. The Free plan allows exactly one rule in this
# phase, so this resource owns the whole http_ratelimit entry point ruleset: an
# existing ruleset must be imported first (docs/edge.md, "First run").
# REST: /zones/{zone_id}/rulesets/phases/http_ratelimit/entrypoint (Zone WAF Write).
resource "cloudflare_ruleset" "beacon_rate_limit" {
  zone_id     = var.zone_id
  name        = "Reach Around edge rate limit"
  description = "Managed by deploy/edge (issue #613)"
  kind        = "zone"
  phase       = "http_ratelimit"

  rules = [{
    ref         = local.rl.ref
    description = local.rl.description
    expression  = local.rl.expression
    action      = local.rl.action
    ratelimit = {
      characteristics     = local.rl.characteristics
      period              = local.rl.period
      requests_per_period = local.rl.requests_per_period
      mitigation_timeout  = local.rl.mitigation_timeout
    }
  }]
}
