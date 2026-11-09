<!-- SPDX-License-Identifier: Apache-2.0 -->
# Twitch followers source

The followers total needs a USER access token with the scope
`moderator:read:followers`. The broadcaster authorizes once; the token then
refreshes. Nothing here calls Twitch from the repository's tests.

Marks: VERIFIED = read on the vendor page on 2026-11-09. INFERRED = not
confirmed on a readable page.

## API facts

| Fact | Mark | Source |
| --- | --- | --- |
| `GET https://api.twitch.tv/helix/channels/followers` "Requires a user access token that includes the moderator:read:followers scope" | VERIFIED | https://dev.twitch.tv/docs/api/reference/#get-channel-followers |
| `broadcaster_id` is required; `first` is 1 to 100; `total` is "The total number of users that follow this broadcaster" | VERIFIED | same |
| 401 when the token lacks the scope or is not valid; the page lists no 403 row | VERIFIED | same |
| The page lists no `moderator_id` parameter, so none is sent | VERIFIED | same |
| Refresh: POST `https://id.twitch.tv/oauth2/token`, form body `client_id`, `client_secret`, `grant_type=refresh_token`, `refresh_token` (URL-encoded); answer has `access_token`, `expires_in`, `refresh_token`, `scope`, `token_type` | VERIFIED | https://dev.twitch.tv/docs/authentication/refresh-tokens/ |
| The refresh token "may change"; the page does not say it is single use. This code treats it as rotating and always hands the new pair to the sink | VERIFIED (the wording), INFERRED (that Twitch invalidates the old one) | same |
| Twitch recommends refreshing on a 401, not ahead of time; invalid refresh token answers 400 or 401 and needs a new consent; refresh tokens of Public client apps expire after 30 days, confidential ones do not; password change or disconnecting the app invalidates them | VERIFIED | same |
| Authorization code grant: send the browser to `https://id.twitch.tv/oauth2/authorize` with `client_id`, `response_type=code`, `redirect_uri`, `scope`, optional `state` and `force_verify`; exchange the `code` by POST to `https://id.twitch.tv/oauth2/token` with `client_id`, `client_secret`, `code`, `grant_type=authorization_code`, `redirect_uri` | VERIFIED | https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/ |
| The redirect URI must be registered on the app; the page names `http://localhost:3000` for testing and gives no further localhost rule | VERIFIED | same |
| A 429 carries `Retry-After` | INFERRED (the rate-limit page returned 403 to the reader) | https://dev.twitch.tv/docs/api/rate-limits/ |
| Whether a confidential app's refresh token can be left unrotated is not documented | INFERRED | |

## Configuration

The credential variable names are fixed in the code, never taken from the
config, so a config cannot point the collector at another variable:

| Purpose | Variable |
| --- | --- |
| App client id | `TWITCH_CLIENT_ID` |
| App client secret | `TWITCH_CLIENT_SECRET` |
| Broadcaster access token | `TWITCH_USER_ACCESS_TOKEN` |
| Broadcaster refresh token | `TWITCH_USER_REFRESH_TOKEN` |

`client_id_env` and `client_secret_env` may still be written, but only with the
exact values above. `user_token_env` and `refresh_token_env` do not exist. Any
other value is refused when the config loads and again before the environment
is read (`bad_env_name`; the check never echoes a value).

```yaml
numbers:
  sources:
    - {platform: twitch, kind: twitch, login: example_login}
```

- `TWITCH_USER_ACCESS_TOKEN` empty or unset: the old behavior. The source uses
  an app token and is reported as skipped (`needs_user_token`) on a 401 or 403.
  Never an error.
- `TWITCH_USER_ACCESS_TOKEN` filled: the total is read with that token. History
  (when enabled) records `source: api`, metric followers. `as_of` and the stale
  gate work as for every other source.
- On a 401 the shipped code makes NO refresh request. It fails the source with
  `token_expired` and prints the content-free line
  `token-health: ... kind=refresh_needed ...`. The token's remaining lifetime is
  not stored, so there is no early warning before the first 401.
- `auto_refresh: true` is opt-in and is refused (`refresh_needs_sink`, before
  any request) unless the caller supplies a sink that stores the rotated pair.
  The shipped `NoopSink` does not count, and the command line supplies no other
  sink, so today the setting only works for code that embeds the collector.

## Failure words

`bad_env_name`, `missing_credential`, `invalid_credential`, `token_expired`
(401, no refresh done), `token_rejected` (401 again after a refresh),
`refresh_needs_sink`, `refresh_store_failed`, `bad_auto_refresh`, `refresh_failed`, `bad_json`, `too_large`, `redirect`,
`rate_limited`, `server_error`, `egress_timeout`, `missing_field`, `bad_count`,
`bad_host`. None includes a response body or a token.

## Rotation and the manual refresh

Sink contract: `store()` must return exactly `True` once the pair is kept. Any
other result (`None`, `False`, any other object) or any exception counts as not
stored. Then the run fails the source with `refresh_store_failed`, prints the
`rotated_unstored` line, and the new pair is not used for a count. A sink's
errors (from `store()` or `alert()`) are dropped without being formatted or
chained, so a sink that puts a token in its error text cannot leak it. The
`auto_refresh` setting is type-checked again at run time (it must be the boolean
`true`; `bad_auto_refresh` otherwise), before any credential is read.

The code never writes a secret to disk or the repository. Refresh rotates the
pair (the refresh token "may change"), so a refresh whose result is not stored
breaks the next run. That is why refresh is off by default and why the shipped
no-op sink is refused for `auto_refresh`. The `TokenSink` interface
(`reach_around.counts.twitch_user`) and the refresh function stay for a future
sink that writes to the secret manager; it is not implemented, because agents
never touch the secret store.

Manual refresh, run by the owner when the alert line appears (VERIFIED request
shape, refresh-tokens page, 2026-11-09): a form POST to
`https://id.twitch.tv/oauth2/token` with `client_id`, `client_secret`,
`grant_type=refresh_token` and the URL-encoded `refresh_token`. Read the values
from the secret manager without typing them into shell history, then store the
`access_token` and `refresh_token` from the answer as
`TWITCH_USER_ACCESS_TOKEN` and `TWITCH_USER_REFRESH_TOKEN` and discard the
output. If Twitch answers 400 or 401, repeat the authorization below.

Alert shape (for the health record of issue 390; names only, no values):

```
token-health: source=<platform> kind=<refresh_needed|refresh_failed|rotated_unstored> names=<ENV_NAME>,<ENV_NAME>
```

## One-time authorization (owner steps, no secret values here)

1. Register (or open) the Twitch application in the broadcaster's developer
   console. Set a redirect URI of `http://localhost:3000` (VERIFIED as the
   documented test value; the application must list it exactly). Note the
   client id and create a client secret.
2. Store the client id and secret in the secret manager under `TWITCH_CLIENT_ID` and `TWITCH_CLIENT_SECRET`. Never in the repository or a GitHub secret.
3. While logged in to Twitch as the broadcaster, open in a browser (replace the
   placeholders):
   `https://id.twitch.tv/oauth2/authorize?client_id=<CLIENT_ID>&redirect_uri=http%3A%2F%2Flocalhost%3A3000&response_type=code&scope=moderator%3Aread%3Afollowers&state=<RANDOM>`
   Approve. The browser lands on `http://localhost:3000/?code=<CODE>&state=...`
   (the page may not load; copy `code` from the address bar and check `state`
   matches). Treat the code as a secret; it is short lived and single use
   (INFERRED).
4. Exchange it once, from a private shell, with the values read from the secret
   manager and not typed into history:
   `POST https://id.twitch.tv/oauth2/token` with form fields `client_id`,
   `client_secret`, `code`, `grant_type=authorization_code`, `redirect_uri`.
   The answer holds `access_token` and `refresh_token`.
5. Store them in the secret manager as `TWITCH_USER_ACCESS_TOKEN` and
   `TWITCH_USER_REFRESH_TOKEN`, and discard the shell output.
6. Run `reach-around counts refresh` with the secrets injected. If Twitch
   answers a refresh with 400 or 401, repeat from step 3.
