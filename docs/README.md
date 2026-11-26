<!-- SPDX-License-Identifier: Apache-2.0 -->
# Documentation index

Every page in `docs/`, grouped by purpose, with a one-line purpose and a state word. Issue #801, epic #795.

State words (from the documentation plan, read 2026-11-24):

- **GOOD**: accurate, keep, edit lightly.
- **STALE**: names an old decision or a superseded plan. It carries a banner at the top that says what superseded it.
- **PARTLY STALE**: some sections predate the 2026-11-24 storage and privacy decisions.
- **INTERNAL**: a working paper or an owner-only page, not written as public guidance.
- **MISSING**: no page exists yet; listed at the end.

A state is a judgment on 2026-11-24, not a test result. The decisions that supersede the STALE pages will be in the ADR index in `docs/wiki/adr/` once PR #835 merges.

## Entry points

| File | Purpose | State |
|---|---|---|
| [../README.md](../README.md) | What the project is, status, run it in a container, links | GOOD (status text refreshed in #801) |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | The gate script and CI | GOOD, thin |
| [runner-tests.md](runner-tests.md) | What a machine needs to run the full test suite, and the 2026-11-26 runner failures with causes | GOOD |
| [../CHANGELOG.md](../CHANGELOG.md) | What changed per release; fragments live in `changelog.d/` | GOOD |

## Deploy and operate

| File | Purpose | State |
|---|---|---|
| [deploy.md](deploy.md) | Test-subdomain deploy, limits, Doppler, troubleshooting | STALE (banner added) |
| [cutover-runbook.md](cutover-runbook.md) | Moving a page from Beacons to Reach Around, with rollback | GOOD, INTERNAL framing |
| [operations.md](operations.md) | Weekly and monthly routine, rollback of analytics | GOOD |
| [failure-modes.md](failure-modes.md) | Kill switches, quota, restore, how gaps are shown | GOOD |
| [backup-restore.md](backup-restore.md) | Backing up the analytics database and history, restore drill | GOOD |
| [collection-gaps-runbook.md](collection-gaps-runbook.md) | Response and recovery when collection has gaps | GOOD |
| [token-health.md](token-health.md) | Token health report | GOOD |
| [token-sink.md](token-sink.md) | Store rotated collector tokens in the secret manager | GOOD |
| [alerts.md](alerts.md) | Owner alerts from the Worker | GOOD |
| [instance-repos.md](instance-repos.md) | Keep your page in a private repo that pins the engine release | GOOD |
| [containers.md](containers.md) | Run the container image | GOOD |
| [releasing.md](releasing.md) | Cut and verify a release | GOOD |
| [preview.md](preview.md) | Local preview server | GOOD |
| [grafana.md](grafana.md) | Optional Grafana dashboards and export | GOOD |
| [examples/deploy-private.sh](examples/deploy-private.sh) | Sanitized private deploy script | GOOD |

## Concepts and reference

| File | Purpose | State |
|---|---|---|
| [config.md](config.md) | Site config reference (large) | GOOD |
| [site.schema.json](site.schema.json) | Generated config schema | GOOD (generated) |
| [customizing.md](customizing.md) | Change the page: themes, overrides | GOOD |
| [design.md](design.md) | Design and theme tokens | GOOD |
| [design-sheet/](design-sheet/components.html) | Rendered component sheet | GOOD |
| [editor.md](editor.md) | The local editor | GOOD |
| [kit.md](kit.md) | Media kit, the gate form, the public versus gated halves | GOOD |
| [report.md](report.md) | The `reach-around report` command | PARTLY STALE |
| [analytics-design.md](analytics-design.md) | Page-view collector design | PARTLY STALE, INTERNAL |
| [analytics-funnel-tables-design.md](analytics-funnel-tables-design.md) | Landing and hop funnel tables design | PARTLY STALE, INTERNAL |
| [analytics-acceptance.md](analytics-acceptance.md) | Reconciliation against Beacons numbers | PARTLY STALE, INTERNAL |
| [performance.md](performance.md) | Lighthouse scores and how they were measured | GOOD |
| [parity.md](parity.md) | Link parity check | GOOD (overlap with research/beacons-parity.md unconfirmed) |
| [beacons-history-import.md](beacons-history-import.md) | Import Beacons link-page history | GOOD |
| [social-history.md](social-history.md) | Social-stats history store | GOOD, INTERNAL |
| [live.md](live.md) | Live tab | GOOD |
| [education/](education/) | Seven teaching pages: click endpoint, config hardening, CSP and overrides, escaping, privacy by construction, snapshot store, threat model walkthrough | GOOD (re-check against code before publishing) |

## Security and privacy

| File | Purpose | State |
|---|---|---|
| [privacy.md](privacy.md) | Lead retention and deletion requests | STALE (banner added) |
| [audience-analysis-privacy.md](audience-analysis-privacy.md) | What Reach Around does not collect | GOOD |
| [security/threat-model.md](security/threat-model.md) | Source threat model | GOOD, a risk register |
| [threat-model/](threat-model/README.md) | Threat Dragon model and the ranked open risks | GOOD, a risk register |
| [turnstile-accessibility-check.md](turnstile-accessibility-check.md) | Invisible bot check accessibility checklist | GOOD (predates the frame-domain decision) |
| [turnstile-storage-check.md](turnstile-storage-check.md) | Turnstile storage check | GOOD (predates the frame-domain decision) |

## Research (internal working papers)

Platform notes written from public documentation, with unverified items marked. Keep those marks. Index: [research/README.md](research/README.md). Cross-platform pages: [research/platform-metrics-matrix.md](research/platform-metrics-matrix.md), [research/platform-terms-retention.md](research/platform-terms-retention.md), [research/beacons-parity.md](research/beacons-parity.md) (an internal checklist, not a publishable comparison).

| File | Purpose | State |
|---|---|---|
| [research/](research/README.md) | One note per platform, plus design notes (bot classifier, history tombstones, Twitch sampling, IP datasets, read token, support embeds) | GOOD as research, INTERNAL |
| [twitch-source.md](twitch-source.md), [twitch-analytics.md](twitch-analytics.md) | Twitch followers source; Twitch analytics (owner only) | GOOD, INTERNAL |
| [youtube-analytics.md](youtube-analytics.md) | YouTube Analytics (owner only) | GOOD, INTERNAL |
| [instagram-insights.md](instagram-insights.md), [instagram-threads-sources.md](instagram-threads-sources.md) | Instagram insights (owner only); Instagram and Threads follower sources | GOOD, INTERNAL |
| [tiktok-source.md](tiktok-source.md), [engagement-sources.md](engagement-sources.md) | TikTok followers; TikTok and Threads engagement | GOOD, INTERNAL |

## History

| File | Purpose | State |
|---|---|---|
| [superpowers/specs/2026-10-23-reach-around-design.md](superpowers/specs/2026-10-23-reach-around-design.md) | Original design spec | STALE (banner added) |
| [superpowers/plans/2026-10-23-roadmap.md](superpowers/plans/2026-10-23-roadmap.md) | October roadmap, revision 2 | STALE (banner added) |
| [superpowers/plans/](superpowers/plans/) | Step-level plans 1 to 4, cutover slice, destination-kind tabs | STALE as plans, kept as history |
| [superpowers/specs/2026-10-25-local-editor-design.md](superpowers/specs/2026-10-25-local-editor-design.md), [decisions](superpowers/specs/2026-10-25-local-editor-decisions.md) | Local editor design and decisions | GOOD, history |
| [superpowers/specs/2026-11-20-media-kit-v2-design.md](superpowers/specs/2026-11-20-media-kit-v2-design.md), [sponsor quote design](superpowers/specs/2026-11-20-sponsor-quote-outreach-pricing-design.md) | Media kit v2 and sponsor quote designs | CURRENT |
| [wiki/](wiki/) | Source pages for the public wiki (costs page and worksheet so far) | GOOD, in progress |

## MISSING

Why Reach Around exists; a quick start for a stranger; a glossary; an architecture overview; a storage architecture page; the decisions (ADR) set (PR #835); a Free versus Paid deployment guide; a DNS and domain guide; a secrets layout guide; a GDPR and CCPA stance and data inventory page; a comparison page; an agent-workflow page; a FAQ; a public roadmap; a troubleshooting index; a contributor guide beyond the gate script.
