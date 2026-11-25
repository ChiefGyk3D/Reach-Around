# D1 `changes()` inside a batch: dev-database check (#759)

Why: the emergency delete in `worker/src/viewretention.ts` (and older code in `view.ts`
and `clickceiling.ts`) writes its counter and flag in the same `db.batch()` as the delete,
using SQLite's `changes()` in the statements after it. That is atomic if (1) Cloudflare
runs a batch as one transaction and (2) the statements of one batch run in order on one
SQLite connection, so `changes()` is the delete's count.

What the vendor docs say (read 2026-11-24): the D1 Worker API page
(`developers.cloudflare.com/d1/worker-api/d1-database/`, dateModified 2026-06-22) says
batched statements are SQL transactions, run sequentially and non-concurrently, and a
failure rolls back the whole sequence. It does not mention `changes()` or a shared
connection. The SQL statements page (`developers.cloudflare.com/d1/sql-api/sql-statements/`,
dateModified 2026-04-21) does not mention `changes()` and points to the source for the
supported function list. So the cross-statement `changes()` behavior is INFERRED from
SQLite semantics, not documented. This check turns it into a measured fact.

Run it on the DEV database only (never prd). Replace `DEV_DB` with the dev D1 database
name from `wrangler.toml`.

```sh
cd worker
npx wrangler d1 execute DEV_DB --remote --file ../docs/d1-changes-check.sql
```

Expected: the final SELECT prints `ok = 1` for all three checks. If any is 0, stop and
tell the maintainer: the emergency bookkeeping must then be rewritten to read the count
from the delete's own result. The script creates and drops its own scratch tables.
