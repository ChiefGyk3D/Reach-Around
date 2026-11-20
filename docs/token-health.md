<!-- SPDX-License-Identifier: Apache-2.0 -->
# Token health report

The daily counts refresh tells you about a platform token BEFORE it expires and
after a platform refused it. One report, fixed words, never a token value.
Issue #390 (epic #246).

## What each collector already says

| Source | Signal today | Where it comes from |
| --- | --- | --- |
| Instagram, Threads | `token_expires` (a non-secret day in the config, about 60 days after issue or last refresh, per [instagram-threads-sources.md](instagram-threads-sources.md)); source error `token_expired` past that day; `token_rejected` on an HTTP 400 or 401; a `token-health: source=... kind=refresh_needed names=... days_left=N` line inside `refresh_within_days` | `meta_user.py`, `MetaTokenAlert` shape `HealthAlert` |
| Twitch (user token) | source error `token_expired` (a 401 and no refresh possible) or `token_rejected`; `refresh_failed`; line `token-health: ... kind=refresh_needed`. The token's lifetime is not stored, so there is no date | `twitch_user.py`, [twitch-source.md](twitch-source.md) |
| TikTok | `token_expired`, `token_rejected`, `scope_missing`, `refresh_failed`; line `token-health: ... kind=...` | `tiktok_user.py`, [tiktok-source.md](tiktok-source.md) |
| YouTube (API key) | no token words. A refused key ends the source with the fetch word `http_400` or `http_401` | `providers.youtube` (UNVERIFIED: which HTTP status Google uses for each key problem; `http_403` is also what a quota stop looks like, so it is not counted) |

The refresh reports each source in the exit code (3 when a source failed), the
`<platform>: error (<word>)` line and the status file (`numbers.status.json`).
The `token-health:` lines above go through the collector's sink to stderr, and
nothing collected them into one place before this report.

## The report

One record per token-holding source: `source`, `state`, `days_left`.

| State | Meaning |
| --- | --- |
| `ok` | nothing known is wrong (an unknown expiry is `ok` with `days_left=unknown`) |
| `expiring` | `token_expires` is at most `refresh_within_days` away (Instagram, Threads), today included |
| `expired` | the expiry day passed, or the source ended with `token_expired` |
| `rejected` | the platform refused the token (`token_rejected`, `refresh_failed`, `scope_missing`, or a refused YouTube key) |

A word from the last run wins over the date. Only Instagram and Threads have a
date, so only they can be `expiring`; Twitch, TikTok and YouTube show up when
they have already failed. It reads no secret and no environment variable.

```
token-health: source=instagram state=expiring days_left=3
```

(The older collector lines use `kind=` instead of `state=`; both start with
`token-health:` and neither carries a value.)

## Where it shows up and what it does to the exit code

- `reach-around counts refresh` prints one line per source after the counts:
  `ok` on stdout, anything else on stderr. It changes no exit code: an
  `expiring` token never turns a good run red. An `expired` or `rejected` token
  already failed its source, so the existing exit 3 covers it.
- `reach-around counts tokens --config site.yaml [--status FILE] [--format F]`
  prints the same report from the config and the last status file: no network,
  no secret. `--format` is `text`, `json`, `markdown` (a table for a step
  summary) or `github` (Actions annotations: `::warning` while expiring,
  `::error` otherwise; nothing for `ok`). Exit codes follow `counts refresh` and
  `counts stale`: 0 every token ok, 4 a token is expiring (the code
  `counts stale` uses for "something to do"), 3 a token is expired or rejected,
  2 usage or config problem. The status file is the last refresh's; with no
  status file only the dates count.
- The instance template's `scripts/refresh-counts.sh` runs `counts tokens` after
  each refresh (offline, no keys, read-only mounts), prints the annotations
  into the step log and appends the table to `$GITHUB_STEP_SUMMARY`. The report
  is best effort: whatever it returns is ignored, and an older engine image
  without the command skips it. A run is red only for the reasons it already
  was.

## Read this before building an alert on it

- **A missing or damaged status file is not healthy.** With no status file
  `counts tokens` reports only the dates and can exit 0. A step that posts an
  alert (follow-up #662) must check that the status file exists and is fresh
  before it reads exit 0 as "all tokens fine".
- **YouTube `http_400` is labeled `rejected`, but it may be a malformed
  request, not a bad key.** UNVERIFIED: Google's wording for each key problem
  was not checked against its documentation. Treat a YouTube `rejected` as
  "look at the key", and confirm in the console.

## Wiring Pushover, Discord or Matrix (yours, later)

The engine sends nothing. The owner's alert channels belong to the instance
repository's workflow. Because the script only prints, a later step in your own
workflow can run `counts tokens --format json` (or read the
`::warning`/`::error` lines) and post one fixed line such as
`instagram token expiring, 3 days left` to a channel with its own secret. Keep
the message to the report's words: no token, no URL. The follow-up to build that
step is tracked as its own issue (#390 follow-up, Pushover and chat channels).
The Worker alerts in [alerts.md](alerts.md) are a different feature and do not
carry these.

## What this does not do

- It does not store a health record per source over time or count repeated
  failures; the status file keeps only the last run. A history of failures is
  the follow-up.
- Twitch, TikTok and YouTube have no expiry date to warn from, so there is no
  early warning for them, only the report after a rejection.
- It does not renew a token. Renewal steps are in the per-source pages.
