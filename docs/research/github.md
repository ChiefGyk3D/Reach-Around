# GitHub API access and metrics (issue #18)

> **Status: draft.** First-pass research by an AI agent on 2026-10-23, written for issue #18. It has not yet been checked against its sources by an independent reviewer or by the maintainer. Treat every claim marked UNVERIFIED as open.

Fetched 2026-10-23 from docs.github.com unless noted.

## Summary

GitHub has a mature official REST and GraphQL API. Followers and stars need no special permission. Traffic (views, clones, referrers) needs repo admin-level read access and keeps only 14 days, so a daily job must store its own history. Sponsors income is exposed in GraphQL. For a self-hosted daily job, a fine-grained PAT is the simplest setup; a GitHub App is the more robust one.

## Metrics

| Metric | How to get it | Scope or permission |
|---|---|---|
| Followers (count) | `GET /users/{username}` returns integer `followers`. Docs list no scope. Private profiles show 0 unless authenticated as that user. [1] | None (public); token optional |
| Followers (list) | `GET /users/{username}/followers`, or `GET /user/followers` when authenticated. [2] | Fine-grained "Followers" read for the authenticated form [3] |
| Stars (count) | `GET /repos/{owner}/{repo}/stargazers/count`, or `stargazers_count` on `GET /repos/{owner}/{repo}`. [4][3] | Metadata read (public repos need none) |
| Stars (history) | `GET .../stargazers/history`, weekly buckets with per-day counts, page size capped at 30. [4] | UNVERIFIED beyond Metadata read per [3] |
| Stargazer list with timestamps | `GET .../stargazers` with `Accept: application/vnd.github.star+json` adds `starred_at`. Docs say that from July 2026 the listing is limited to admins and collaborators. [4] | Likely breaks for non-owners; test it |
| Forks | `forks_count` on `GET /repos/{owner}/{repo}` | Metadata read |
| Traffic views and clones | `GET /repos/{owner}/{repo}/traffic/views` and `/clones`, `per=day` or `week`, UTC. [5] | Administration (repository) read [3]; docs say write access to the repo [5] |
| Referrers, popular paths | `.../traffic/popular/referrers` and `/popular/paths`, top 10 only. [5] | Same as above |
| Sponsors (maintainer side) | GraphQL `Sponsorable.sponsors`, `sponsorshipsAsMaintainer` (`includePrivate` defaults false), `monthlyEstimatedSponsorsIncomeInCents`. Private sponsors return null sponsor. [6] | Docs page does not state scopes: UNVERIFIED |
| Commit activity | `GET .../stats/commits`, last year by week; first call may return 202 while GitHub computes it. [7] | Read |

## History depth

Traffic: last 14 days only [5]. Everything else is a current snapshot except star history and `starred_at`. To build trends, the daily job must write its own rows, and a job that misses more than 14 days loses traffic permanently.

## User setup steps

1. Create a fine-grained PAT, resource owner = the user, selected repos, permissions: Metadata read, Administration read (traffic). Add account permission Followers read only if the follower list is wanted. Set an expiry.
2. Put it in the user's own secret store or env var. Reach Around never receives it.
3. Alternative: register a private GitHub App owned by the user, install it on their repos with the same permissions, store the app ID, installation ID and private key.
4. For sponsors, test the GraphQL query with the token before relying on it.

## Rate limits and cost

Free. REST: 5,000 requests/hour for a PAT, 60/hour unauthenticated, GitHub App installations 5,000 minimum up to 12,500 [8]. GraphQL: 5,000 points/hour, 500,000 node cap per call [9]. Secondary limits (100 concurrent, 900 points/min per REST endpoint) can change without notice [8]. A daily job for one creator uses well under 1% of this.

## Tokens and unattended access

- Fine-grained PAT: expiry 1 to 366 days or none, org policy may forbid none; default in the prefill URL is 30 days; tokens unused for a year are removed [10]. A 90-day or 1-year PAT means the user must rotate it manually; the job should detect 401 and surface it.
- GitHub App: installation tokens last 1 hour and are minted from a JWT signed with the app private key, so a daily job never needs human renewal [11]. GitHub states Apps are generally preferred and are not tied to a person's account [12].
- Fine-grained PATs cannot access user-owned Projects or multiple orgs at once [10].

## Risks and terms

- GitHub ToS Section H: excessive API use can lead to suspension; do not share tokens to evade rate limits; no selling users' personal data to recruiters or job boards. It sets no specific storage or display limit [13].
- Showing the creator's own counts publicly is low risk. Storing follower or stargazer lists (usernames) is personal data; store counts only.
- Traffic referrers and paths are repo-private information; showing them publicly is the user's choice, so make it opt-in.
- Do not share one token across users; each user brings their own.
- Stargazer API access narrowing in July 2026 [4] shows the surface can change.

## Fallbacks

Public unauthenticated counts (`/users/{username}`, `stargazers_count`, `forks_count`) work at 60 requests/hour with no secret. Without traffic access, skip views and clones; there is no public equivalent. Manual or CSV import of traffic is possible from the repo Insights page (UNVERIFIED that an export button exists).

## Verdict

Official API. Followers, stars, forks: public endpoint, no key needed. Traffic and sponsors: official API with the user's token, daily snapshotting required. Recommend fine-grained PAT as the v1 default and GitHub App as the documented upgrade.

## Sources (all fetched 2026-10-23)

1. https://docs.github.com/en/rest/users/users#get-a-user
2. https://docs.github.com/en/rest/users/followers
3. https://docs.github.com/en/rest/overview/permissions-required-for-fine-grained-personal-access-tokens
4. https://docs.github.com/en/rest/activity/starring
5. https://docs.github.com/en/rest/metrics/traffic
6. https://docs.github.com/en/graphql/reference/sponsors
7. https://docs.github.com/en/rest/metrics/statistics
8. https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
9. https://docs.github.com/en/graphql/overview/rate-limits-and-node-limits-for-the-graphql-api
10. https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens
11. https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app
12. https://docs.github.com/en/apps/creating-github-apps/about-creating-github-apps/deciding-when-to-build-a-github-app
13. https://docs.github.com/en/site-policy/github-terms/github-terms-of-service

## Open questions for the maintainer

1. PAT default or GitHub App default for v1?
2. Store referrers and paths, and show them publicly, or keep them private?
3. Is sponsors income in scope, given the scope requirements are UNVERIFIED?
4. Org-owned repos: support them, given the fine-grained PAT one-owner limit?
5. Should the job alert when it misses 10 days, given the 14-day traffic window?
