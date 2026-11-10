# What a read-only D1 token can do (issue #248)

> **Status: draft.** Documentation-only research by an AI agent on 2026-11-10, written for issue #248 (epic #245). Every claim is marked VERIFIED (the page text was read, URL and read date given) or INFERRED. Nothing here was tested against a live Cloudflare account: the owner approved documented behavior only, so no token was created and no call was made. It has not been checked by an independent reviewer or by the maintainer. Treat every INFERRED item and the "Unverified" list at the end as open.

All pages below were read on 2026-11-10 from developers.cloudflare.com, as the page's markdown source (the same text as the rendered page). "Updated" is the page's own "Last updated" line or release-note date.

## Summary

- A read-only D1 permission exists: **D1 Read**. The documented query endpoints accept D1 Read or D1 Write, and Cloudflare's release notes say a write query under a read-only token fails.
- It cannot be scoped to one database or one table. Token policies name a user, an account or a zone, nothing smaller. D1 Read reads every table of every D1 database in the accounts the policy includes, including `leads`, `kit_events`, `visitor_seen` and `view_events`.
- So the server enforces "no writes", but only our script (or a route we write) can enforce "aggregates only".
- Recommendation (for the owner to decide): the access-protected report route gives true least privilege; the fixed-query script with a D1 Read token is cheaper but leaves the lead tables readable by anyone who holds the token. Details in section 5.

## 1. Is there a read-only D1 permission, and what can it do?

**The permission exists.** VERIFIED: the API token permissions page (https://developers.cloudflare.com/fundamentals/api/reference/permissions/, updated 2026-10-30) lists, under Account permissions, "D1 Read: Grants read access to D1" and a write counterpart. The page has two tables: the first names it "D1 Edit", the second "D1 Write". The endpoint pages use "D1 Write". INFERRED: they are the same permission under two names, but the page does not say what separates the two tables. The create-token page (https://developers.cloudflare.com/fundamentals/api/get-started/create-token/, updated 2026-11-06) says that most permissions offer `Edit` or `Read`, where `Edit` is full create, read, update, delete and list, and `Read` is the read permission and list where appropriate.

**Which operations accept it.** VERIFIED from the D1 API reference pages (https://developers.cloudflare.com/api/resources/d1/, each page read 2026-11-10). The "Accepted Permissions (at least one required)" line reads `D1 Read` or `D1 Write` for:

| Endpoint | What it does |
|---|---|
| `POST /accounts/{account_id}/d1/database/{database_id}/query` | Run SQL, rows as objects. The `sql` field "supports multiple statements, joined by semicolons, which will be executed as a batch"; a `batch` array form also exists. |
| `POST .../raw` | Same, rows as arrays. |
| `GET /accounts/{account_id}/d1/database/{database_id}` | Database details. |
| `GET /accounts/{account_id}/d1/database` | List databases in the account. |
| `GET .../time_travel/bookmark` | Current bookmark, or the nearest one at or before a timestamp. |

**Does the query endpoint reject writes under D1 Read?** The endpoint page itself does not say which statements a read permission may run. It lists D1 Read and D1 Write as equally acceptable for `/query`, and its response schema has `changed_db` ("Denotes if the database has been altered in some way") and `rows_written`, which is how a caller can see whether a statement wrote. The answer comes from the D1 release notes (https://developers.cloudflare.com/d1/platform/release-notes/, page updated 2026-04-21), VERIFIED:

- 2025-02-04: "Fixed a bug with D1 permissions which allowed ... users with read-only API tokens via the `/query` REST API to execute queries that modified databases ... Write queries with read-only access will now fail."
- 2025-05-02: a bug that let account and user tokens with `D1:Read` plus `Edit` on another Cloudflare product perform D1 writes was fixed: "`D1:Edit` permission is required for any database writes via HTTP API."

Two lessons. First, the documented behavior is that writes fail under D1 Read, and that is enforced by Cloudflare, not by us. Second, this boundary has leaked twice before, once in a combination case (read on D1 plus edit on another product). A read token for this job should therefore carry no other permission at all, not even a harmless-looking one. The exact error code and message a rejected write returns are not documented on the pages read (see Unverified).

**What D1 Read cannot be shown to do.** The `export` and `import` endpoint pages (https://developers.cloudflare.com/api/resources/d1/subresources/database/methods/export/ and `.../import/`) and the Time Travel `restore` page print no "Accepted Permissions" line at all, VERIFIED (absence on the page text). So it is not documented whether D1 Read is enough for the REST export, which is what `wrangler d1 export` is documented to do ("Export the contents or schema of your database as a .sql file", https://developers.cloudflare.com/d1/wrangler-commands/, updated 2026-04-21). It is also not documented whether D1 Read can call `restore`; INFERRED that restore needs D1 Write because it overwrites the database ("a *destructive* operation, and overwrites the database in place", https://developers.cloudflare.com/d1/reference/time-travel/, updated 2026-11-06, VERIFIED text), but the API page does not say so.

**What it can read, and what it costs.** VERIFIED: the `/query` and `/raw` endpoints run SQL against the database, so a read token can `SELECT` from any table. The response metadata includes `rows_read`, so reads are metered per query (billing rules not read; see Unverified). The global Cloudflare API rate limit applies: 1,200 requests per five minutes per user or account token (https://developers.cloudflare.com/fundamentals/api/reference/limits/, updated 2026-08-25, VERIFIED), and the D1 proxy-Worker tutorial says the REST API "is best suited for administrative use" for that reason (https://developers.cloudflare.com/d1/tutorials/build-an-api-to-access-d1/, updated 2026-10-12, VERIFIED). A daily export of about ten queries is far under that limit.

**Side effects of an export.** VERIFIED (https://developers.cloudflare.com/d1/best-practices/import-export-data/, updated 2026-04-21): "A running export will block other database requests." The export API page says the same ("your D1 will be unavailable to serve queries"; an in-progress export "must be continually polled or will automatically cancel"). A report export that uses `execute` with `SELECT` does not carry that cost; a full-table `export` does. This is why `docs/backup-restore.md` warns against exporting in a busy hour.

## 2. Can a token be scoped to one database?

No, not by the documented model. VERIFIED: the token policy page (https://developers.cloudflare.com/fundamentals/api/how-to/create-via-api/, read 2026-11-10) says "API token policies support three resource types: `User`, `Account`, and `Zone`." The documented resource strings are a single account, all accounts, a single zone, all zones in an account, all zones in all accounts, and the user. There is no D1 database resource string, and the permission list gives D1 Read no narrower scope. The permissions page says the "scope of each permission" can be fetched from the List permission groups endpoint; that endpoint was not called.

Result: INFERRED from the absence, a D1 Read token for the account reads every D1 database in that account. The narrowest documented grant is "D1 Read, on one account". If the lead and visitor tables must be unreachable by the report credential, the documented ways are (a) keep them out of the account the token covers, or (b) do not hand out a Cloudflare API token for this job at all (the route in section 5). Moving data into a second Cloudflare account is INFERRED to work and is not recommended here as it costs a second account and a second deploy path.

Token type: VERIFIED that D1 is marked compatible with account-owned tokens (https://developers.cloudflare.com/fundamentals/api/get-started/account-owned-tokens/, updated 2026-10-23, the table row "D1"). `docs/deploy.md` uses a user token for deploys because that token needs zone permissions that account-owned tokens cannot carry. A read-only D1 token has no zone permission, so an account-owned token is INFERRED to be usable, and it would not be tied to a person's membership. Wrangler may also want "User Details: Read" to identify the token owner (as `docs/deploy.md` already notes for deploys); whether `d1 execute --remote` needs it is not documented, so the safest path for the script is the REST `/query` endpoint called directly with the fixed queries, not wrangler. INFERRED.

## 3. Can SELECT-only be enforced server-side?

Partly.

| Property | Enforced by | Basis |
|---|---|---|
| No writes (INSERT, UPDATE, DELETE, schema changes) through the HTTP API | Cloudflare, for a token with D1 Read and nothing else | VERIFIED, release notes 2025-02-04 and 2025-05-02 above. Not tested live. |
| Only `SELECT` statements (no `PRAGMA`, `EXPLAIN`, `ATTACH`, multi-statement batches) | Not documented. Cloudflare's own docs describe the boundary as read versus write, not as a statement list. | Absence on the pages read. |
| Only certain tables or columns (aggregates yes, leads no) | Nobody on Cloudflare's side. Only our script or a route we write. | VERIFIED absence of any table or column scope in the permission list. |
| Only certain databases | Nobody on Cloudflare's side. | Section 2. |
| Row limits, quotas, time-of-day | Not by the token. Token expiry and a client IP filter exist (create-token page, step 8). | VERIFIED. |

The release notes also describe how D1 itself recognizes a read: automatic retries apply to "queries containing only the following SQLite keywords: `SELECT`, `EXPLAIN`, `WITH`", and D1 "checks for modifications after every query execution" and rolls back a retried query that wrote (release notes, page updated 2026-04-21, VERIFIED). That is a retry heuristic, not a permission check, and must not be read as a promise about the token.

So: the guarantee we can lean on from Cloudflare is "a read-only token cannot change data". The guarantee "the export cannot see leads" has to come from our script (a fixed list of allowed statements, with the table and column names in code, matching `scripts/dump-schema.json` and the column list in `docs/report.md`, "Making the export"). The report loader already refuses unknown columns, which is a second line, but that runs after the data left Cloudflare.

## 4. The scheduled export in the private instance repo

What the scheduled export in the private instance repo (issue #370) would use, from the rules the owner set (secret manager, dev first, never a GitHub secret, separate from the backup credential of #400):

- **Credential.** A Cloudflare token with exactly `Account > D1 > Read`, one account, nothing else, with an expiry (the create-token page offers token expiration and client IP filtering). The client IP filter is probably unusable if the job runs on GitHub-hosted runners, whose addresses change (INFERRED), so expiry plus rotation are the controls left. Name it for the job (for example "reach-around report read"), so an audit log names the purpose.
- **Where it lives.** The secret manager, not the repository and not a GitHub Actions secret. Following the owner's layout rules: the Doppler project for the product (`reach-around` today, per `docs/deploy.md`), created in the **dev** environment first, in its own config, not in the `ci` config that holds the deploy token (`D1: Edit`). It is promoted to prd only after the owner says the project is live. `docs/deploy.md` already documents a pattern for a job that reads Doppler over GitHub OIDC with a Service Account Identity made for that one job and a dedicated project and config, with no long-lived token stored; the export would reuse that pattern with its own identity, read access to that one config, and nothing else. VERIFIED (that doc, read in this repository), not exercised for this job.
- **Separate from the backup credential (#400).** Keep two credentials. The report read credential should be able to run only the fixed `SELECT` queries. A backup uses `wrangler d1 export`, which the export endpoint page does not tie to D1 Read (section 1); the backup credential may need more, so it is decided by its own proof, and it must not widen the report credential. Neither is the deploy token.
- **No credential in logs or argv** (acceptance criterion of #370). Read the token from the environment or stdin inside the script, send it only in an `Authorization` header over HTTPS, never print the request, and never put it on a command line (a command line is visible to other processes and in CI logs). `docs/deploy.md` already tells the owner to set the value through a prompt and to end any Doppler command that echoes with `> /dev/null`.
- **Failure alert.** A failed query, a refused export or an unexpected column should exit non-zero and raise the alert named in the monitoring issue of the production readiness epic. Not designed here.

If the route in section 5 is chosen instead, this credential changes shape: it becomes an Access service token (Client ID and Client Secret, with an expiry set when it is created; https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/, updated 2026-11-02, VERIFIED) stored the same way, and it can reach the one protected route and not the Cloudflare API.

## 5. Recommendation

The owner decides. The two paths from #370:

| | Fixed-query script with a D1 Read token | Access-protected report route |
|---|---|---|
| How | A script in the instance repo sends a fixed list of `SELECT` statements to the REST `/query` endpoint, writes `export.json`, runs `reach-around report`. | A Worker route behind Cloudflare Access returns only the fixed aggregate result set. The scheduled job presents an Access service token and writes `export.json`. |
| What the credential can do if leaked | Read every table of every D1 database in the account, including leads and visitor tables. Cannot write (documented, not tested). | Fetch the fixed aggregate result set that the report already prints, floored. Cannot reach Cloudflare's API, D1 or the lead tables. |
| What enforces "aggregates only" | Our script only. A leaked token bypasses it. | The route code, on the server, for every caller. |
| New code and attack surface | None in the Worker. | A new route in the Worker that holds the data; needs a review, tests and a threat-model update (#333). Cloudflare Access setup (not read beyond the service-token page). |
| Fits the unattended job | Yes. | Yes, per the service-token page. |
| Rate limit | Global API limit applies (fine at this volume). | Not the API limit. |

**Recommendation: the access-protected report route**, on least-privilege grounds. The decisive documented facts are: D1 Read has no table or database scope (sections 2 and 3), the same database holds lead and visitor tables, and a read credential in a scheduled job is the kind of secret that leaks (a log line, a runner compromise). With the route, a leak exposes data the report already publishes to the owner; with the token, it exposes the leads. The route costs engineering and a larger Worker (size M in #370 may grow) and leaves the access policy as a second thing to get right.

If the owner prefers the cheaper first step, the script path is acceptable **only if** the token has D1 Read and no other permission, an expiry, and the Doppler dev-first handling above, **and** the owner accepts that the token can read lead data. In that case do not describe it as "aggregates only"; describe it as "cannot write".

Either way, the backup credential from #400 stays a separate decision, and `docs/report.md` ("Not covered") and `docs/grafana.md` keep saying the commands make no network call: the fetch step lives outside them.

## What remains unproven

Cloudflare documents the permission model; nobody has exercised it for this repository. The owner approved documented behavior only, so these need a live proof (or Cloudflare's own confirmation) before #370 relies on them. See the list below.

## Comment-ready summary for #248

(Not posted. For the lead to post after the PR is reviewed.)

> Spike result (docs only, read 2026-11-10, no live token tests). 1) Yes, `D1 Read` exists. `/query`, `/raw`, get and list database, and the Time Travel bookmark accept D1 Read or D1 Write; Cloudflare's release notes (2025-02-04, 2025-05-02) say a write under a read-only token fails and that D1 Edit is required for writes. 2) No per-database scope: token policies name a user, an account or a zone only, so D1 Read covers every D1 database in the account. 3) Cloudflare enforces "no writes"; "SELECT only", table and column limits are not enforced server-side, only by our script. D1 Read can read the lead and visitor tables. 4) The scheduled export would use a job-named, expiring token with D1 Read only (or an Access service token, under the route option), held in the secret manager (dev first, own config, not the deploy `ci` config, never a GitHub secret), separate from the #400 backup credential, which the export endpoint page does not tie to D1 Read. 5) Recommendation for the owner: the Access-protected report route for least privilege; the fixed-query script is acceptable if the owner accepts that the token can read leads. Note: docs/research/d1-read-token.md. Unproven: the error a rejected write returns, whether D1 Read can run the REST export, multi-statement and PRAGMA behavior, and the Access route setup.

## Unverified

- That a write sent to `/query` with a D1 Read token fails today, and the error code and message it returns. Only the 2025 release notes say so; no live test.
- Whether D1 Read can call the export endpoint (and so `wrangler d1 export`), or import, or Time Travel restore. The three API pages list no accepted permission.
- Whether `wrangler d1 execute --remote` works with a D1 Read token alone, or also needs "User Details: Read" or another permission.
- Which statement kinds a D1 Read `/query` accepts beyond `SELECT` (`PRAGMA`, `EXPLAIN`, `WITH`, `ATTACH`, multi-statement batches).
- Whether the "D1 Edit" and "D1 Write" rows on the permissions page are the same permission under two names, and the exact permission group ID for D1 Read (the List permission groups endpoint was not called).
- That no narrower resource scope than the account exists for D1 today. Only the policy resource page was read; the dashboard may differ, and no screenshot was seen.
- That an account-owned token (rather than a user token) works for D1 Read in a scheduled job.
- Billing for rows read by report queries on the current plan, and whether the free-plan limits matter for a daily job.
- Whether a client IP filter is usable for the scheduler (depends on where it runs).
- The Cloudflare Access setup for a Worker route (policy type, service token header names, and how the Worker verifies the Access JWT). Only the service-token page was read.
- Whether the Doppler OIDC pattern in `docs/deploy.md` works unchanged for a scheduled (cron) workflow in the instance repo.
- Anything about the live database: its size, whether the lead tables share the database, and how long an export takes. Not read; no call was made.
