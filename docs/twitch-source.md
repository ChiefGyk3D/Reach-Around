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

```yaml
numbers:
  sources:
    - platform: twitch
      kind: twitch
      login: example_login
      client_id_env: TWITCH_CLIENT_ID
      client_secret_env: TWITCH_CLIENT_SECRET
      user_token_env: TWITCH_USER_ACCESS_TOKEN
      refresh_token_env: TWITCH_REFRESH_TOKEN
```

- No `user_token_env`, or the variable is empty: the old behavior. The source
  uses an app token and is reported as skipped (`needs_user_token`) on a 401 or
  403. Never an error.
- `user_token_env` set and filled: the total is read with that token. History
  (when enabled) records `source: api`, metric followers. `as_of` and the stale
  gate work as for every other source.
- `refresh_token_env` is optional. Leave it out for the read-only mode below.

## Failure words

`missing_credential`, `invalid_credential`, `token_expired` (401, no refresh
token configured), `token_rejected` (401 again after a refresh),
`refresh_failed`, `bad_json`, `too_large`, `redirect`, `rate_limited`,
`server_error`, `egress_timeout`, `missing_field`, `bad_count`, `bad_host`.
None includes a response body or a token.

## Rotation and the operational consequence

The code never writes a secret to disk or the repository. After a refresh it
gives the new pair to a token sink. The shipped sink (`NoopSink`) prints the
secret NAMES and one instruction: store the new values under those names in the
secret manager. A future sink that writes to the secret manager is a documented
hook (`TokenSink` in `reach_around.counts.twitch_user`) and is not implemented:
agents never touch the secret store.

If the rotated refresh token is not stored, the next run holds an old one and
may fail with `refresh_failed`. Two safe modes:

1. Read-only (recommended until a storing sink exists): omit
   `refresh_token_env`. The run uses the access token until it expires, then
   fails with `token_expired` and prints a health alert.
2. Refresh with a human in the loop: when the run prints `kind=rotated_unstored`,
   the owner stores the new pair before the next run.

Alert shape (for the health record of issue 390; names only, no values):

```
token-health: source=<platform> kind=<refresh_needed|refresh_failed|rotated_unstored> names=<ENV_NAME>,<ENV_NAME>
```

## One-time authorization (owner steps, no secret values here)

1. Register (or open) the Twitch application in the broadcaster's developer
   console. Set a redirect URI of `http://localhost:3000` (VERIFIED as the
   documented test value; the application must list it exactly). Note the
   client id and create a client secret.
2. Store the client id and secret in the secret manager under the names used in
   the config. Never in the repository or a GitHub secret.
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
5. Store them in the secret manager as the two names set in `user_token_env`
   and `refresh_token_env`, and discard the shell output.
6. Run `reach-around counts refresh` with the secrets injected. If Twitch
   answers a refresh with 400 or 401, repeat from step 3.
