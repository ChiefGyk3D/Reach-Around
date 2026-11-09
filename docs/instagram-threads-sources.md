<!-- SPDX-License-Identifier: Apache-2.0 -->
# Instagram and Threads follower sources

`reach-around counts refresh` can read the follower count of an Instagram
professional account and of a Threads profile with a long-lived user token.
Everything here was built and tested against recorded response shapes. No
live call to Instagram, Threads or Meta was made.

Marks: **VERIFIED** means read on the vendor's own page on 2026-11-09 (URL
given). **INFERRED** means the page did not state it.

## What is collected, and what is not

Collected: the follower total, one number per run, written to the numbers file
and, when `numbers.history_file` is set, appended to the
[history store](social-history.md) as `source: api`, metric `followers`.

Not collected here: views, reach, engagement, demographics, per-post data
(separate work), watch time (neither platform has such a metric), and anything
about individual followers. The token is only used for the one read (a refresh is done by hand,
see below).

## Config

```yaml
numbers:
  sources:
    - platform: instagram
      kind: instagram
      token_expires: 2027-01-08      # optional, non-secret, at most 120 days ahead
      refresh_within_days: 10        # optional, 1 to 59, default 10
      # auto_refresh: false          # optional, see the routine below; leave off
    - platform: threads
      kind: threads
      user_id: "1234567890"          # optional, see the INFERRED note below
```

The token is read from one fixed environment variable per platform:
`INSTAGRAM_ACCESS_TOKEN` or `THREADS_ACCESS_TOKEN`. The config cannot name a
different variable (a config that tries is refused when it loads), so no other
secret in the environment can be sent to the platform. The value is supplied to
the process by your secret manager at run time. The token is never in the config,
the repo or a GitHub secret.

If the variable is empty or unset, the source is skipped with `token_not_set`
(exit 0, the old number and its date stay). To keep a platform fully hand-kept
with the stale gate, leave its source out of the config until the token exists:
a platform with a configured source is treated as fed by the API.

## Hosts and requests

| Platform | Count request | Refresh request |
|---|---|---|
| Instagram | `GET https://graph.instagram.com/v27.0/me?fields=followers_count&access_token=...` | `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token&access_token=...` |
| Threads | `GET https://graph.threads.com/v1.0/<user>/threads_insights?metric=followers_count&access_token=...` | `GET https://graph.threads.com/refresh_access_token?grant_type=th_refresh_token&access_token=...` |

- VERIFIED https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started
  (`graph.instagram.com`, `/me`, `followers_count` field, token as the
  `access_token` query parameter).
- VERIFIED https://developers.facebook.com/docs/threads/insights
  (`graph.threads.com/v1.0/<id>/threads_insights`, `followers_count` is a total
  value metric, the answer holds `total_value.value`, no `since` or `until`).
- VERIFIED https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
  and https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens
  (refresh endpoints and grant types above; the token must be at least 24 hours
  old and not expired; the answer holds `access_token`, `token_type`,
  `expires_in` in seconds; a token not refreshed within 60 days expires and
  cannot be refreshed).
- VERIFIED https://developers.facebook.com/docs/threads/get-started : Threads
  permissions `threads_basic` (all endpoints) and `threads_manage_insights`
  (insights reads); testers can grant at any time; a public-profile grant lasts
  90 days and a refresh extends it by 90 days.
- VERIFIED https://developers.facebook.com/docs/graph-api/overview/rate-limiting :
  calls in 24 hours = 4800 x impressions (Threads floors impressions at 10);
  throttling is signaled by error codes (4, 17, 613, 80002 and others); the page
  names no Retry-After header. One run makes one call.
- Research note (2026-11-08, from the vendor docs): Instagram needs
  `instagram_business_basic`; Standard Access works for accounts with a role on
  the app; Advanced Access (App Review plus Business Verification) is only for
  accounts you do not own. "Your own account needs no App Review" is INFERRED
  from those statements.
- INFERRED: that `instagram_business_basic` alone covers the `followers_count`
  field (the get-started page lists the field but not its permission).
- INFERRED: the Threads path segment `me` in place of the numeric user id. Set
  `user_id` to avoid relying on it.
- INFERRED: that an `Authorization` header is accepted instead of the query
  parameter. Neither vendor page says so, so the token travels in the query
  string, as in the vendor examples. The fetch layer never puts a URL, header or
  response body into an error, status file or log line, and any other exception
  from the fetch layer is replaced by the fixed word `fetch_failed` before it
  leaves the collector. Header support is unverified; the owner can test by hand
  whether an `Authorization: Bearer` header works on a harmless call, and only
  then would the collector be switched to it. Each request is checked
  to be plain https to exactly the one host, no redirect is followed, and a 10
  second limit and a 64 KiB response cap apply.
- INFERRED: the response content type (`text/javascript` is accepted for these
  two hosts besides JSON).

## One-time owner setup

Do these in the vendor dashboards yourself. Nothing here needs a token pasted
into a chat, an issue or the repo.

Instagram (permission and role facts are VERIFIED above; the step order is
INFERRED):

1. The Instagram account must be a professional (Business or Creator) account.
2. In Meta for Developers, create an app of the Business type and add the
   Instagram API with Instagram Login product.
3. Give yourself a role on the app (Standard Access then applies).
4. Generate a long-lived token for your account with `instagram_business_basic`
   (VERIFIED that dashboard-generated tokens are long-lived, 60 days).
5. Store it in your secret manager as `INSTAGRAM_ACCESS_TOKEN` (this exact name),
   and write the day it expires (about 60 days out) as `token_expires`.

Threads (permissions and tester role VERIFIED; step order INFERRED):

1. In Meta for Developers, create an app with the Threads use case.
2. Invite your Threads profile as a Threads Tester (App roles, Roles tab) and
   accept the invitation in Threads.
3. Authorize the app with `threads_basic` and `threads_manage_insights`, and
   exchange the short-lived token for a long-lived one (server side).
4. Store it as `THREADS_ACCESS_TOKEN` (this exact name) and record `token_expires`.

## The 60-day routine

A long-lived token lasts about 60 days. The shipped code cannot store a new
token (it never writes a secret anywhere), so it does not refresh on its own:
asking for a new token it cannot keep would only lose it.

1. Record the expiry day in `token_expires` (non-secret).
2. A run within `refresh_within_days` of that day still reads the count with the
   current token and prints one line per run: `token-health: source=... 
   kind=refresh_needed names=... days_left=N`. No refresh request is made.
3. Refresh by hand, in a shell where the token is available, with the request
   VERIFIED on the two pages above:
   - Instagram: `GET https://graph.instagram.com/refresh_access_token` with
     `grant_type=ig_refresh_token` and `access_token=<current token>`.
   - Threads: `GET https://graph.threads.com/refresh_access_token` with
     `grant_type=th_refresh_token` and `access_token=<current token>`.
   The token must be at least 24 hours old and not expired. The answer holds the
   new `access_token` and `expires_in` (seconds). Store the new value in the
   secret manager under the same variable NAME, and set `token_expires` to the new
   expiry day. Do not paste the token anywhere else.
4. If the recorded day has passed, the source fails with `token_expired` before
   any call (`kind=token_expired`). A token the platform rejects (HTTP 400 or
   401) fails as `token_rejected` (`kind=token_rejected`). For either, repeat the
   one-time authorization.
5. Without `token_expires`, no reminder is possible. Record it.

Checks at run time, whatever way the settings were built: `auto_refresh` must be
a real boolean (only `true` counts), `refresh_within_days` a whole number from 1
to 59, `token_expires` a date, else the source fails with `bad_source_setting`
and makes no request. A `token_expires` more than 120 days ahead is refused when
the config loads; one more than 120 days away in either direction at run time
prints `kind=expiry_implausible` with the days. A refresh answer must carry a
plausible long-lived `expires_in` (7 to 120 days), else it counts as refused; the
new expiry day is computed in UTC. If the sink cannot keep a new token, the
original token stays in use for that run and the run prints
`kind=refresh_store_failed`; the new token is dropped, never used unstored.

`auto_refresh: true` exists for a future secret-storing sink. It is refused with
`refresh_sink_required` unless the calling code supplies a sink, and the shipped
command supplies none, so leave it off. The `token-health:` lines carry only the
source slug, a fixed kind, the variable NAME and the days left: the shape the
token health work (#390) consumes.

Failure words in `numbers.status.json`: `token_not_set` (skipped),
`token_expired`, `token_rejected`, `refresh_sink_required`, `bad_source_setting`, `fetch_failed`, `bad_credential_name`, `missing_credential`, `invalid_credential`,
`missing_field`, `bad_count`, `bad_json`, `too_large`, `content_type`,
`rate_limited`, `server_error`, `redirect`, `http_<status>`.

## Terms

Meta Platform Terms (updated 2026-02-03, from the research note) ask for deleting
Platform Data that is no longer needed or on request; the history store holds
only the follower total.
