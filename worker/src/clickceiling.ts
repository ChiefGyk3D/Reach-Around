// SPDX-License-Identifier: Apache-2.0
//
// The click write ceiling (hardening, Refs #566). One accepted click writes several D1 rows (the raw
// row and its index, the day counters, the capacity row), and the per-client limiter does not bound
// the total across clients. This is the enforced bound: per UTC day the click route may be charged at
// most CLICK_ADMIT_CEILING row writes in all, split evenly over the configured sites (so one site cannot
// spend another's share), atomically, BEFORE any click write. Past it a
// click is answered exactly as before (an empty 204) and only the counting stops. The deploy-time
// budget model (kit/budgets.ts) reads CLICK_ACCOUNT_DAILY_WRITES from here and nowhere else, so the model
// and the enforcement cannot drift.
//
// STORAGE. Two rows of the existing `counters` table per (site, day): `cw-<site>` is the charged
// writes and `cd-<site>` the clicks dropped past the ceiling (no migration). The admission is one
// conditional upsert, so concurrent clicks cannot overshoot. It writes one row (counted in the cost)
// for an admitted click. A refused click writes nothing to `cw-` and one `cd-` row only while the
// dropped count is under CLICK_DROP_COUNT_CAP, so refused traffic costs at most that many writes a day.
// The dropped count therefore saturates at the cap: "at least this many were dropped".
//
// TRADE. Counting stops for the rest of the UTC day once the ceiling is spent. Failing closed protects
// the D1 write allowance (the account's, not just this feature's); see docs/deploy.md.

/** Row writes the click route may be charged per UTC day, all sites together. */
export const CLICK_ADMIT_CEILING = 7_500;
/** Most dropped clicks recorded per UTC day, all sites together (one write each; past it a refused click writes nothing). */
export const CLICK_DROP_COUNT_CAP = 500;
/** Worst-case click-route writes per UTC day, whatever the number of sites: what the model reserves. */
export const CLICK_ACCOUNT_DAILY_WRITES = CLICK_ADMIT_CEILING + CLICK_DROP_COUNT_CAP;

/** One site's share of a total when `sites` sites are served: equal, rounded down, so the shares never add to more. */
export function siteShare(total: number, sites: number): number {
  return Math.floor(total / Math.max(1, sites));
}

/**
 * Rows one admitted click writes, worst case: the admission row (1), the raw row and its `ts` index (2),
 * the country counter (1), the capacity row (1, written whenever a click folds), plus one counter per
 * enabled axis (source, landing, hop). New-cell budget rows are modeled with the cells (funnelcaps.ts).
 */
export function clickWriteCost(on: { sources?: boolean; landings?: boolean; hops?: boolean }): number {
  return 1 + 2 + 1 + 1 + (on.sources === true ? 1 : 0) + (on.landings === true ? 1 : 0) + (on.hops === true ? 1 : 0);
}

/**
 * Charge `cost` writes to the site's day against its share of the ceiling (`sites` = sites served).
 * True: admitted, go on and write. False: refused, write nothing.
 * A database error propagates (the route treats it as a refusal and records nothing).
 */
export async function admitClick(db: D1Database, site: string, nowMs: number, cost: number, sites = 1): Promise<boolean> {
  const day = new Date(nowMs).toISOString().slice(0, 10);
  const r = await db
    .prepare(
      "INSERT INTO counters (key, day, count) SELECT ?1, ?2, ?3 WHERE ?3 <= ?4 " +
        "ON CONFLICT (key, day) DO UPDATE SET count = count + ?3 WHERE count + ?3 <= ?4", // CONTROL:click-write-ceiling
    )
    .bind(`cw-${site}`, day, cost, siteShare(CLICK_ADMIT_CEILING, sites))
    .run();
  if (r.meta.changes === 1) return true;
  await db
    .prepare(
      "INSERT INTO counters (key, day, count) VALUES (?1, ?2, 1) " +
        "ON CONFLICT (key, day) DO UPDATE SET count = count + 1 WHERE count < ?3",
    )
    .bind(`cd-${site}`, day, siteShare(CLICK_DROP_COUNT_CAP, sites))
    .run();
  return false;
}
