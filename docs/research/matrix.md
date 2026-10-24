# Matrix research note (issue #17)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #17. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23. Spec read: Client-Server API v1.19 (spec.matrix.org/latest).

## Summary

Matrix is an open protocol, so there is no app review and no vendor ToS: access is whatever the user's homeserver allows. A daily job can log in as a user (or reuse an existing bot account) that is a member of the room, call `joined_members` for the current member count, and call `/members` with a `membership` filter to count leavers and banned users. Historical joins and leaves are not a first-class metric; they come from daily snapshots or from state events. Public room counts are available from the room directory on many servers.

## Metrics

| Metric | How | Scope or permission |
|---|---|---|
| Current joined members | `GET /_matrix/client/v3/rooms/{roomId}/joined_members`, count the map | Authenticated; caller must be in the room (Application Services excepted). Not rate limited per spec |
| Members by state | `GET /rooms/{roomId}/members?membership=join` (also `leave`, `ban`, `invite`, `knock`; `not_membership` excludes) | Authenticated; must be or have been a member. Not rate limited per spec |
| Members at a past point | Same, with `at=<token>` (pagination token, e.g. a sync `prev_batch`) | Same |
| Joined/left over time | Daily snapshot deltas; or read `m.room.member` events (with `origin_server_ts`) via `/messages` | Member of the room; depth depends on history visibility. Timestamp-derived: my inference, UNVERIFIED |
| Public room size | Room directory `GET /_matrix/client/v3/publicRooms`, field `num_joined_members` | Many servers allow it; Synapse defaults to requiring auth (`allow_public_rooms_without_auth: false`). Field name and response shape UNVERIFIED in the spec text I could read |
| Space member count | Spaces are rooms; use the space's own room ID. Count of unique users across child rooms must be computed client-side | Member of each room |

## Setup steps for a user

1. Pick or create a dedicated account on the homeserver (or reuse an existing bot account; read-only use is fine).
2. Join that account to each room or space to measure. This is the main least-privilege cost: the account becomes a visible member and counts itself.
3. Obtain an access token by logging in with `m.login.password` (spec) or the homeserver's login flow, and store it as a secret. Prefer a token tied to a dedicated device.
4. Configure homeserver base URL (not just the domain; homeservers can be reached at a different base URL) and room IDs or aliases.
5. No power level is required; do not grant moderator rights.

## Rate limits and cost

Homeservers should rate limit; a 429 carries `M_LIMIT_EXCEEDED`, and servers should send a `Retry-After` header (`retry_after_ms` deprecated since v1.10). Synapse defaults: `rc_message` 0.2 per second, burst 10; `rc_login` 0.003 per second per address and per account, burst 5 (Synapse docs). Daily reads are trivial. Free; hosting cost is the user's.

## Across homeservers

The client-server endpoints above are in the spec, so they work on Synapse, Dendrite, Conduit and other compliant servers. Behavior details (token expiry, directory publishing, federation delays for remote rooms) vary per server: UNVERIFIED per implementation.

## Risks and ToS

No central ToS. Risks: the data includes user IDs and display names, so store only counts; the bot account's membership is public; room history visibility settings may hide earlier state; counts in federated rooms can lag. Showing counts publicly is the creator's decision. The homeserver operator's own rules apply to automated accounts (check per server; UNVERIFIED).

## Token lifetime and unattended access

Access tokens may expire; an expired token returns `M_UNKNOWN_TOKEN`, preferably with `soft_logout: true`. Servers that support refresh tokens (Synapse since 1.49 per search results) issue a refresh token at login when the client sends `refresh_token: true`, and the old refresh token is invalidated when a new one is issued, so the job must persist the rotated token after every run. Matrix.org moved to the Matrix Authentication Service (MAS) in April 2025, keeping legacy login compatible; how MAS compatibility tokens expire was not verified. Safest design: a long-lived token for a dedicated device, plus a re-login fallback using a stored password only if the user opts in (more risk). UNVERIFIED on exact lifetimes.

## Fallbacks

Public directory count (current only, only for published rooms); manual entry; a bot in the room that logs joins and leaves going forward (exact, but starts from install day); import CSV. Accuracy cost: leaver counts exclude users who left before the bot joined, if history is not visible.

## Verdict

Official API (client-server). Current count: `joined_members`. Joined/left over time: daily snapshots, optionally refined with `m.room.member` events. Public endpoint is a fallback for published rooms only.

## Sources (fetched 2026-10-23)

- https://spec.matrix.org/latest/client-server-api/ (v1.19; joined_members, members, rate limiting, refresh tokens, lazy loading, membership values)
- https://element-hq.github.io/synapse/latest/usage/configuration/config_documentation.html (first 100k chars only; rate-limit defaults, public rooms)
- https://matrix.org/blog/2025/04/morg-now-running-mas/ (search result only, not opened)

## Open questions for the maintainer

1. Is requiring the account to join the room acceptable?
2. Reuse an existing bot account, or a dedicated read-only one?
3. Which homeservers must be supported day one (Synapse only, or also Dendrite, Conduit, matrix.org with MAS)?
4. Should the tool store password for re-login, or only tokens?
5. A live test is needed for `publicRooms` shape and Synapse token expiry settings (not read).
