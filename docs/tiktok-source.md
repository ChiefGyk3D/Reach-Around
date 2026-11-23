<!-- SPDX-License-Identifier: Apache-2.0 -->
# TikTok followers source

The follower total comes from the TikTok Display API, `user.info.stats` scope,
with a user token. Until the owner has done the steps below, TikTok stays a
hand-kept number and everything works as before. Nothing here calls TikTok from
the repository's tests; the collector was built and tested on hand-written
response shapes only.

Marks: VERIFIED = read on the vendor page on the date given. INFERRED = not
confirmed on a page that could be read. The first read was 2026-11-09; the pages
were read again on 2026-11-23 (public pages only, no account, no API call). A
row with no newer date was not re-read.

## What the vendor docs say

| Fact | Mark | Source |
| --- | --- | --- |
| `GET https://open.tiktokapis.com/v2/user/info/`, required query `fields`, header `Authorization: Bearer <user token>` | VERIFIED 2026-11-23 (page "Last updated November 4, 2026") | https://developers.tiktok.com/doc/tiktok-api-v2-get-user-info |
| `follower_count` (int64), `following_count`, `likes_count` and `video_count` need the scope `user.info.stats`; `open_id` and `display_name` need `user.info.basic`. A scope migration is announced: `user.info.basic` alone no longer returns the stats fields, so an app and user that relied on it must add `user.info.stats` and authorize again | VERIFIED 2026-11-23 | same, and https://developers.tiktok.com/bulletin/user-info-scope-migration |
| The answer holds `data.user` and an `error` object whose `code` is `ok` on success | VERIFIED | same |
| Refresh: `POST https://open.tiktokapis.com/v2/oauth/token/`, form body `client_key`, `client_secret`, `grant_type=refresh_token`, `refresh_token`; answer has `access_token`, `expires_in`, `refresh_token`, `refresh_expires_in`, `open_id`, `scope`, `token_type` | VERIFIED | https://developers.tiktok.com/doc/oauth-refresh-a-user-access-token |
| Access token lives 2 hours, refresh token 180 days. Every refresh returns a different refresh token; reusing a replaced one makes later refreshes fail and the user must authorize again. The refresh page's example answer shows `expires_in` 86400 and `refresh_expires_in` 31536000, which do not match the 2 hour and 180 day text on the same pages; the collector uses the stated limits only as sanity caps (2 days, 400 days), never as a schedule | VERIFIED 2026-11-23 (both pages "Last updated November 5, 2026") | https://developers.tiktok.com/doc/oauth-user-access-token-management |
| Errors: `access_token_invalid` HTTP 401, `scope_not_authorized` HTTP 401, `scope_permission_missed` HTTP 400, `rate_limit_exceeded` HTTP 429 | VERIFIED 2026-11-23 (page "Last updated August 4, 2026") | https://developers.tiktok.com/doc/tiktok-api-v2-error-handling |
| 600 requests a minute for `/v2/user/info/` | VERIFIED (research note of 2026-11-08) | https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit |
| "Before you integrate with our developer products, you must submit your app for review": demo video (up to 5, 50 MB each), an explanation per product and scope, URL ownership verification | VERIFIED | https://developers.tiktok.com/doc/getting-started-create-an-app |
| A Sandbox is a restricted environment that needs no review: up to 5 sandboxes per app, up to 10 target TikTok accounts you own and can log in to; target users authorize Login Kit separately; Content Posting for public videos and Data Portability are not offered; the page does not list which scopes work in a Sandbox | VERIFIED | https://developers.tiktok.com/doc/add-a-sandbox |
| Moving to production requires app review | VERIFIED | same |
| Whether `user.info.stats` returns real data in a Sandbox | NOT FOUND, re-read 2026-11-23: the Sandbox page ("Last updated August 4, 2026") says nothing about scopes or fields, and the user info page says nothing about a Sandbox. Not guessed; the owner step below tests it | https://developers.tiktok.com/doc/add-a-sandbox |
| Whether `user.info.stats` needs approval beyond the app review, or is granted in production to a reviewed app. The scopes overview ("Last updated August 19, 2026", read 2026-11-23) names only `user.info.basic` as an example; the scope migration bulletin says to add the new scope under Scopes on the app page, save and submit for review | NOT FOUND whether anything beyond that review is needed (INFERRED risk); that the scope change goes through review is VERIFIED 2026-11-23 | https://developers.tiktok.com/doc/scopes-overview |
| Review duration | NOT FOUND (no time stated) | |
| Cost | VERIFIED free (research note 2026-11-08) | |
| The 401 classes are told apart by the `error.code` word only: the fetch layer, when asked, returns just that word (lowercase letters, digits, underscore, 40 at most) from a 400 or 401 body of at most 8 KiB, never body text. Only `access_token_invalid` leads to a refresh; `scope_not_authorized` is reported as `scope_missing` (owner action, no rotation); an absent or unknown code is `token_rejected` (owner action, no rotation) | VERIFIED (the codes), tested | https://developers.tiktok.com/doc/tiktok-api-v2-error-handling |
| Whether TikTok sends the `error` object on a 401 in the same shape as the success answer | INFERRED (the page lists codes, not the 401 body); if it does not, the result is `token_rejected` and nothing rotates | |

Answer to "is the count available without app approval": the docs show a
Sandbox needs no review, but do not say that `user.info.stats` works there, and
production needs review. So the count is **not shown to be available without
approval**. That is why the collector stays inert until the owner provides a
token (the "flag" is the access token variable: empty means skipped), and the
hand-kept routine below remains the supported path in the meantime.

## Sandbox answers without stats

The vendor pages do not say whether a Sandbox returns `user.info.stats` fields
(NOT FOUND, 2026-11-23). If the Sandbox answers `200` with a `data.user` object
that lacks `follower_count`, the source fails alone with the fixed word
`missing_field`: no count is stored, the number stays as it was, no refresh is
attempted, and Twitch, Kick and every other source are untouched. If the Sandbox
instead answers `401` with `scope_not_authorized`, the word is `scope_missing`
(owner action: authorize `user.info.stats`; no rotation). Both are tested with
hand-written answers
(`test_sandbox_answer_without_follower_count_is_exactly_missing_field`,
`test_scope_missing_401_reports_owner_action_without_rotation`). If the Sandbox
gives neither a count nor a scope error, keep TikTok hand-kept.

## Owner approval needed for the write-back

The collector hands a rotated token pair to a sink. The sink that keeps it in
the secret manager is already built (`DopplerSink`, [token sink](token-sink.md),
`--token-sink doppler`), but it is not enabled and no agent holds a write scope.
What the owner must approve, as
[token sink](token-sink.md#doppler-access-plan-owner-dev-only) states it:

- one Doppler service account token, limited to the `dev` environment and the
  one config the run names, with read and write on secrets in that config and
  nothing else, covering the two TikTok token secrets the engine reads
  (`TIKTOK_ACCESS_TOKEN` and `TIKTOK_REFRESH_TOKEN`); not a personal or CLI
  token;
- no write to `prd`; promotion is a separate step the owner decides;
- the token comes from the environment or stdin at run time, never a file, a
  command line or a GitHub secret.

Until that is approved, `auto_refresh` stays off and a run with an old access
token fails the source with `token_expired`.

Known mismatch (recorded for a follow-up, nothing changed here): the instance
repository's `ops/tiktok-authorize` helper currently stores one JSON secret
named `TIKTOK_OAUTH_STATE`, while the engine reads `TIKTOK_ACCESS_TOKEN` and
`TIKTOK_REFRESH_TOKEN`. Until the helper or the engine is aligned, a token
authorized with that helper is not seen by the engine.

## Other stats (not collected)

`following_count`, `likes_count` and `video_count` come from the same call under
the same scope (VERIFIED 2026-11-23), but the numbers model carries one follower
count per source and the provider returns a single whole number. Carrying more
needs: new private metric names in the insights table (`METRICS` in
`reach_around/counts/insights.py`, owner-report-only), a collector that reads
them from the same answer (no second request; asking for a field the token's
scope does not cover can make the whole call fail, so a longer `fields` list
must be tested against a real account first), history and kit rules for what may
be shown, and tests. That is a separate change and is not part of this one.

## Hand-kept routine (until a token exists)

TikTok is a normal hand-kept platform:

- Put the count in `numbers.json` (or `count` and `as_of` on the item), or
  import a CSV: `reach-around counts import --config site.yaml --csv FILE`.
- `reach-around check --production` fails when the number is older than
  `numbers.stale_days` (default 30 days). Plain `check` warns.
- `reach-around counts stale --config site.yaml --remind-days 21` lists the
  numbers due for an update (exit 4 when any is due) for a scheduler to turn
  into a reminder. See [config](config.md#hand-kept-numbers).
- While `TIKTOK_ACCESS_TOKEN` is empty in the process running `check` or
  `build`, the stale gate treats TikTok as hand-kept even if a `tiktok` source is
  configured.

## Configuration

```yaml
numbers:
  sources:
    - {platform: tiktok, kind: tiktok}
```

The credential variable names are fixed in the code, never taken from the
config:

| Purpose | Variable |
| --- | --- |
| User access token | `TIKTOK_ACCESS_TOKEN` |
| User refresh token | `TIKTOK_REFRESH_TOKEN` |
| App client key | `TIKTOK_CLIENT_KEY` |
| App client secret | `TIKTOK_CLIENT_SECRET` |

`access_token_env`, `client_key_env` and `client_secret_env` may be written, but
only with the exact names above. Anything else is refused when the config loads
and again before the environment is read (`bad_credential_name`, which never
echoes a name).

- `TIKTOK_ACCESS_TOKEN` empty or unset: the source is skipped (`token_not_set`)
  and the number stays as it was.
- Filled: one `GET` per run reads `follower_count`. History (when enabled)
  records `source: api`, metric followers. The token travels only in the
  `Authorization` header, never in a URL.
- The access token lives 2 hours. Without `auto_refresh`, a run with an old
  token fails the source with `token_expired`, makes no refresh request, and
  prints `token-health: source=tiktok kind=refresh_needed names=...`. Because of
  the 2 hour life, unattended runs need `auto_refresh`.
- `auto_refresh: true` is off by default and refused (`refresh_needs_sink`,
  before any request) unless the caller supplies a sink whose `store()` returns
  exactly `True`. The shipped no-op sink does not count and the command line
  supplies no other, so today the setting only works for code that embeds the
  collector. On a 401 whose code is `access_token_invalid` it refreshes once, hands the new pair to the sink first,
  and uses the new access token only after the sink kept it. A failed or
  non-`True` store fails the source (`refresh_store_failed`) because the old
  refresh token no longer works.

The refresh POST is sent exactly once (`max_attempts=1`, and the shared fetch
layer never replays a request with a body). A 429, 500 or 503 on the refresh
therefore ends in `refresh_failed` with no second POST, because rotation may
already have happened and a replay would send the invalidated refresh token
(tests: `test_the_refresh_post_is_sent_exactly_once`).

The refresh answer is checked before the new access token is used:
`expires_in` a whole number from 1 second to 2 days, `refresh_expires_in` from 1
second to 400 days, `token_type` equal to `bearer` (any case), and `scope`
including `user.info.stats`. When the tokens are present but any of that fails,
the new pair is STILL handed to the sink (rotation may already have happened),
the health state `refresh_response_invalid` is raised, and the new access token
is not used for the count. A missing or unusable token in the answer is
`refresh_failed`. Both states keep the old pair in use.

The fetch is bounded: https to exactly `open.tiktokapis.com`, no redirect
followed, 10 second timeout, 64 KiB body cap, JSON only, a bounded retry of 429
and 5xx honoring `Retry-After` up to a cap (GET only; the refresh POST is sent
once). Any unexpected error becomes the fixed word `fetch_failed`. The count must
be a whole number from 0 to the platform maximum (`bad_count` otherwise).

Failure words: `bad_credential_name`, `bad_auto_refresh`, `bad_source_setting`, `missing_credential`,
`invalid_credential`, `token_expired`, `token_rejected`, `refresh_needs_sink`,
`refresh_failed`, `refresh_response_invalid`, `refresh_store_failed`,
`scope_missing`, `api_error`, `fetch_failed`,
`missing_field`, `bad_count`, `bad_json`, `too_large`, `redirect`,
`content_type`, `rate_limited`, `server_error`, `egress_timeout`, `bad_host`.
None includes a response body, URL or token. Alert line (names only):

```
token-health: source=tiktok kind=<refresh_needed|refresh_failed|rotated_unstored|refresh_response_invalid|scope_missing|token_rejected> names=TIKTOK_ACCESS_TOKEN,TIKTOK_REFRESH_TOKEN
```

## Owner steps (no secret values here)

1. Create a TikTok for Developers app (a developer account is free). Add Login
   Kit and the Display API product. Request the scopes `user.info.basic` and
   `user.info.stats`.
2. Try a Sandbox first (no review): add your own TikTok account as a target
   user, authorize it through Login Kit, and read
   `GET /v2/user/info/?fields=follower_count` once with the resulting token. If
   it returns a count, the Sandbox is enough for one owned account. If it
   returns `scope_not_authorized` or no count, submit the app for review (demo
   video of the whole flow, a reason for each scope, verified URLs) and wait; no
   duration is documented.
3. Store the client key and secret in the secret manager as `TIKTOK_CLIENT_KEY`
   and `TIKTOK_CLIENT_SECRET`, and the token pair from the authorization as
   `TIKTOK_ACCESS_TOKEN` and `TIKTOK_REFRESH_TOKEN`. Use the secret manager
   only: not the repository, a GitHub secret, shell history or a command line.
4. Add the `tiktok` source to the config and run `reach-around counts refresh`
   with the secrets injected.
5. Mind the 180 day refresh token: a pair unused for 180 days needs a new
   authorization. Every refresh replaces the refresh token, so a refresh whose
   result is not stored breaks the next run.

## What is not collected

By default only the follower total. With the optional `insights: true` (off,
partly UNVERIFIED: [engagement-sources.md](engagement-sources.md)) the collector
also calls `video.list` and stores three aggregate numbers over a 28 day window
(video count, summed views, summed likes, comments and shares). Individual video
records are never stored. Never collected, either way: per-video data, watch time, demographics (the Display
API offers none; a Business API route is UNVERIFIED), the profile fields, any
data about viewers or other accounts. TikTok Developer Terms (modified
2025-12-26, research note): data is for developing and supporting the app and
is deleted on termination; storing the owner's own aggregate count in a private
repository is consistent with that (INFERRED). If the app is closed, delete the
stored history for TikTok.
