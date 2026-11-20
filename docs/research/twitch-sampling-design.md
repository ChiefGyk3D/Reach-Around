<!-- SPDX-License-Identifier: Apache-2.0 -->
# Design note: sampling live viewer numbers for Twitch and Kick

Refs #551 and #392, epic #246. Docs only: no sampler is written and nothing here
runs. Written 2026-11-20 from the repository's notes
([twitch.md](twitch.md), [kick.md](kick.md),
[platform-terms-retention.md](platform-terms-retention.md), the media kit v2
spec section 6.4). Anything the notes do not state is marked UNVERIFIED.

## The problem

`avg_ccv`, `peak_ccv` and `hours_watched` (spec section 6.3, same fields and
order for Twitch and Kick) have no API history on either platform. Twitch Get
Streams returns the viewer count of a stream broadcasting now and nothing else
(twitch.md, verified 2026-10-23). Kick's Channels response carries
`stream.viewer_count`, instantaneous only (kick.md, 2026-10-23). To get an average
or a peak over a period, something must either sample while live or import the
creator's own dashboard figures.

## The constraint that decides most of it

The owner decided on 2026-11-19 that Twitch and Kick are treated equally: no API
data is stored beyond a 24-hour cache, history is hand-kept
([platform-terms-retention.md](platform-terms-retention.md), Owner decisions 2 and
4). Both Developer Agreements allow copies only with written authorization, with
rights held, or as a 24-hour cache with no further sharing. A sampler stores a
series of API readings, so under that decision option A is blocked unless the
owner reverses it or obtains authorization. Whether a derived aggregate (one
average per week) is still "Twitch content" is a legal reading nobody has made
(UNRESOLVED; #625 is the human read). It must not be assumed to be allowed.

## Options

| | A. Cron sampler (stores API readings) | B. Dashboard export import | C. Hand-kept entry |
| --- | --- | --- | --- |
| What | A scheduled job polls Get Streams (Twitch) and Channels (Kick) while the creator is live; one integer and a timestamp per poll are folded into a daily or weekly average and peak | The owner downloads the platform's own analytics export (if one exists, UNVERIFIED for both) and imports a CSV | The owner reads the dashboard and types the number through the existing hand-kept flow (`counts history-add`, or the kit's hand-kept measure rows) |
| Where it runs | A Cloudflare Worker cron trigger, or the homelab collector. twitch.md says a sampler "belongs in the homelab collector". A Worker has no state between runs without D1 or KV, and the repository rule is that worker/ is not touched by this work | The owner's machine | The owner's machine |
| Cost | Twitch: free API; a poll every 1 to 2 minutes while live is at most 720 calls a day at 2 minutes; the bucket size is not recorded (UNVERIFIED), so the headroom is unknown, though 1 point a call is stated. Worker cron and storage limits and prices were not read and are UNVERIFIED. Plus engineering: live detection, gaps when the job is down, a token that must stay valid | None beyond a few minutes a month | None beyond a few minutes a month |
| Quota | Both platforms' rate limits are partly unrecorded (Kick: no numeric limit on the pages read) | None | None |
| Privacy | Aggregate only is possible and required: keep the viewer count and the time, drop everything else the answer carries. Kick's Channels answer carries `stream.key`, a secret that must be dropped before anything else sees it (kick.md). Twitch Get Streams carries the streamer's identity and thumbnail, which are not needed. No chat, no viewer identifiers, ever | No viewer identifiers if the export is aggregate. The export's columns are unknown (UNVERIFIED) | The owner's own reading; no API data |
| Terms | Stores API readings: blocked by the 2026-11-19 decision for both platforms | The export is the owner's own data from the owner's dashboard, not an API call, so the API storage rule does not obviously apply (INFERRED, not read in any terms) | Outside the platform data rules: the owner's own reading (terms note, item 4) |
| Twitch and Kick equal | Needs two collectors with the same cadence, the same fields and the same degrade rule; Kick has no history at all, so a sampler would be its only automatic route | Equal only if both platforms offer an export. Whether either does is unknown (spec open questions) | Equal by construction: the same fields, the same flow, the same trust label |
| Trust label | `api` (sampled) | `imported` | `hand_kept` |
| Accuracy | An average of samples, not the platform's own definition; the platform's help page for the definition did not render (UNVERIFIED) | The platform's own figure and definition | The platform's own figure and definition |

## Import shape for option B (proposal, nothing built)

The existing importer is `platform,metric,value,as_of` (importer.py, whole numbers
only, a UTC day, 200 rows, 64 KiB, one bad row refuses the file). Live measures
need a period, so a proposal for a sibling file, one row per platform, measure and
period, with every field required:

```
platform,measure,value,period_start,period_end
twitch,avg_ccv,142,2026-10-21,2026-11-19
kick,avg_ccv,87,2026-10-21,2026-11-19
twitch,hours_watched,8120,2026-10-21,2026-11-19
```

`measure` from the fixed list (`avg_ccv`, `peak_ccv`, `hours_watched`,
`unique_chatters`, `hours_streamed_per_week`), `value` whole digits, days UTC and
not in the future, the period at most 31 days, written as `imported` and printed
with its period. This shape is a proposal for the owner to accept; the real export
columns, if any, must be read from the dashboards first.

## Recommendation

Use C now and build B only if a dashboard export is confirmed for both platforms.
Do not build A unless the owner reverses the 2026-11-19 decision for both
platforms together, or Twitch and Kick give written authorization, because it is
the only option that stores API readings. This keeps Twitch and Kick identical and
needs no persistent job. C needs the kit's hand-kept measure rows (spec section
6.4), which are not part of this change.

If the owner does choose A, the minimum safe shape is: the homelab collector, not a
Worker; one integer and a timestamp per poll; samples folded into a daily
average, peak and sample count and the raw samples deleted at once (so what is kept
is a derived aggregate, still subject to the legal reading above); a closed
provider tag in the history file like the YouTube and Instagram collectors; the
same cadence and fields for both platforms; the Kick `stream.key` dropped first.

## Questions for the owner

1. Keep the 2026-11-19 decision (no stored Twitch or Kick API data), so the
   live measures stay hand-kept and the private Twitch collector in #392 is not
   built? Recommended: yes.
2. If yes, should a dashboard-export import (option B) be investigated once the
   owner has checked whether either dashboard offers an export?
3. If no, which retention applies to stored Twitch and Kick data (the 24-hour
   rule cannot be met by a history file), and does it apply to both platforms?
