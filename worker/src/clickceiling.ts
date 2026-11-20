// SPDX-License-Identifier: Apache-2.0
//
// The click write ceiling (hardening, Refs #566 and #610). One accepted click writes several D1 rows (the
// raw row and its index, the day counters, the capacity row), and the per-client limiter does not bound
// the total across clients. This is the enforced bound: per UTC day the click route may be charged at
// most CLICK_ADMIT_CEILING row writes in all, split evenly over the configured sites (so one site cannot
// spend another's share), atomically, BEFORE any click write. Past it a click is answered exactly as
// before (an empty 204) and only the counting stops. The deploy-time budget model (kit/budgets.ts) reads
// CLICK_ACCOUNT_DAILY_WRITES from here and nowhere else, so the model and the enforcement cannot drift.
//
// FOUR LIMITS, one admission. A site's share S of the day is spent through these:
//   hour slice   no UTC hour may be charged more than S / CLICK_HOUR_SLICES (twice the average hour), so
//                a burst cannot spend the whole day.
//   lane         the charge is kept in two lanes, verified and unverified. The unverified lane may be
//                charged at most CLICK_UNVERIFIED_PERCENT of S in a day. Until a token exists every click
//                is unverified (the default lane); a later change sets the lane per request.
//   general day  S minus the first-click reserve: the most any click but a link's first of the day may
//                bring the day's charge to.
//   first click  CLICK_FIRST_RESERVE_PERCENT of S is held back for the first click of each link each UTC
//                day. That click is judged against S itself and ignores the hour slice and the lane cap,
//                so a flood on one link cannot stop another link from being counted at all.
// The dropped-click counter and its cap are unchanged.
//
// STORAGE. Rows of the existing `counters` table per (site, day), no migration:
//   cw-<site>            the charged writes of the day (all lanes); read by the health check
//   cwh-<site>-<HH>      the charged writes of one UTC hour
//   cwl-<lane>-<site>    the charged writes of one lane (u: unverified, v: verified)
//   cf-<site>-<link>     marks that the link has had its first click today (written once per link per day)
//   cd-<site>            clicks dropped past the limits, capped at CLICK_DROP_COUNT_CAP
// The admission is one batch: a conditional upsert on the day row, then the hour, lane and marker rows, each
// of which writes only if the statement before it changed a row. A batch is one transaction, so concurrent
// clicks cannot overshoot. A refused click writes no `cw-`, hour, lane or marker row and one `cd-` row only
// while the dropped count is under CLICK_DROP_COUNT_CAP, so refused traffic costs at most that many writes a
// day. The dropped count therefore saturates at the cap: "at least this many were dropped".
//
// TRADE. Counting stops for the rest of the hour, lane or UTC day once a limit is spent. Failing closed
// protects the D1 write allowance (the account's, not just this feature's); see docs/deploy.md.

/** Row writes the click route may be charged per UTC day, all sites together. */
export const CLICK_ADMIT_CEILING = 300_000;
/** Most dropped clicks recorded per UTC day, all sites together (one write each; past it a refused click writes nothing). */
export const CLICK_DROP_COUNT_CAP = 500;
/** Worst-case click-route writes per UTC day, whatever the number of sites: what the model reserves. */
export const CLICK_ACCOUNT_DAILY_WRITES = CLICK_ADMIT_CEILING + CLICK_DROP_COUNT_CAP;
/** Percent of a site's share held back for the first click of each link each UTC day. */
export const CLICK_FIRST_RESERVE_PERCENT = 20;
/** Percent of a site's share the unverified lane may be charged in a UTC day. */
export const CLICK_UNVERIFIED_PERCENT = 15;
/** One UTC hour may be charged share / CLICK_HOUR_SLICES: twice the average hour, so a busy hour is not starved. */
export const CLICK_HOUR_SLICES = 12;

/** The lane a click is charged to: verified (v) or unverified (u). Every click is unverified until a token exists. */
export type ClickLane = "v" | "u";

/** One site's share of a total when `sites` sites are served: equal, rounded down, so the shares never add to more. */
export function siteShare(total: number, sites: number): number {
  return Math.floor(total / Math.max(1, sites));
}

export interface ClickLimits {
  /** The site's whole share of the day. */
  share: number;
  /** The day's budget for every click but a link's first of the day. */
  general: number;
  /** Held back for the first click of each link. */
  reserved: number;
  /** The most one UTC hour may be charged. */
  hourSlice: number;
  /** The most the unverified lane may be charged in the day. */
  unverified: number;
}

/** The limits one site's share of the ceiling is spent through (integer math, rounded down). */
export function clickLimits(share: number): ClickLimits {
  const reserved = Math.floor((share * CLICK_FIRST_RESERVE_PERCENT) / 100);
  return {
    share,
    general: share - reserved,
    reserved,
    hourSlice: Math.floor(share / CLICK_HOUR_SLICES),
    unverified: Math.floor((share * CLICK_UNVERIFIED_PERCENT) / 100),
  };
}

/**
 * Rows one admitted click writes, worst case: the four admission rows (the day total, the hour slice, the
 * lane and the link's first-click marker, charged in every case although the marker is written once a day),
 * the raw row and its `ts` index (2), the country counter (1), the capacity row (1, written whenever a click
 * folds), plus one counter per enabled axis (source, landing, hop), and the hourly new-cell budget rows:
 * `click_budget` (country is always on, so a new cell there writes it) and, when landing or hop is on,
 * `click_budget_lh` (one row for both). The budget rows are charged in every combination, whether or not
 * this click makes a new cell, so the charge is never below what a new-cell click writes (clickfunnel.ts,
 * migration 0017).
 */
export function clickWriteCost(on: { sources?: boolean; landings?: boolean; hops?: boolean }): number {
  const lh = on.landings === true || on.hops === true;
  return 4 + 2 + 1 + 1 + 1 + (on.sources === true ? 1 : 0) + (on.landings === true ? 1 : 0) + (on.hops === true ? 1 : 0) + (lh ? 1 : 0);
}

// The admission test for one value of the day row's count (`cur`: 0 for a new row, `count` for an existing
// one). ?3 cost, ?2 day, ?4 marker key, ?5 1 when there is a marker to look up, ?6 hour key, ?7 lane key,
// ?8 hour slice, ?9 lane cap, ?10 whole share, ?11 general budget.
const FIRST = "(?5 = 1 AND NOT EXISTS (SELECT 1 FROM counters f WHERE f.key = ?4 AND f.day = ?2))";
const admits = (cur: string): string =>
  `(${cur} + ?3 <= CASE WHEN ${FIRST} THEN ?10 ELSE ?11 END AND (${FIRST} OR (` +
  "COALESCE((SELECT h.count FROM counters h WHERE h.key = ?6 AND h.day = ?2), 0) + ?3 <= ?8 AND " +
  "COALESCE((SELECT l.count FROM counters l WHERE l.key = ?7 AND l.day = ?2), 0) + ?3 <= ?9)))";

// A charge to one more counter, only if the statement before it changed a row (the admission).
const CHARGE =
  "INSERT INTO counters (key, day, count) SELECT ?1, ?2, ?3 WHERE changes() = 1 " +
  "ON CONFLICT (key, day) DO UPDATE SET count = count + ?3";
const MARK =
  "INSERT INTO counters (key, day, count) SELECT ?1, ?2, 1 WHERE changes() = 1 ON CONFLICT (key, day) DO NOTHING";

/**
 * Charge `cost` writes to the site's day against its share of the ceiling (`sites` = sites served).
 * `linkId` enables the first-click reserve for that link; `lane` is the lane charged (default: unverified).
 * True: admitted, go on and write. False: refused, write nothing.
 * A database error propagates (the route treats it as a refusal and records nothing).
 */
export async function admitClick(
  db: D1Database,
  site: string,
  nowMs: number,
  cost: number,
  sites = 1,
  opts: { linkId?: string; lane?: ClickLane } = {},
): Promise<boolean> {
  const day = new Date(nowMs).toISOString().slice(0, 10);
  const hour = String(new Date(nowMs).getUTCHours()).padStart(2, "0");
  const lim = clickLimits(siteShare(CLICK_ADMIT_CEILING, sites));
  const lane: ClickLane = opts.lane ?? "u";
  const marker = opts.linkId === undefined ? null : `cf-${site}-${opts.linkId}`;
  const hourKey = `cwh-${site}-${hour}`;
  const laneKey = `cwl-${lane}-${site}`;
  const gate = db
    .prepare(
      `INSERT INTO counters (key, day, count) SELECT ?1, ?2, ?3 WHERE ${admits("0")} ` +
        `ON CONFLICT (key, day) DO UPDATE SET count = count + ?3 WHERE ${admits("count")}`, // CONTROL:click-write-ceiling
    )
    .bind(`cw-${site}`, day, cost, marker ?? "", marker === null ? 0 : 1, hourKey, laneKey, lim.hourSlice, lane === "u" ? lim.unverified : lim.general, lim.share, lim.general);
  const batch = [
    gate,
    db.prepare(CHARGE).bind(hourKey, day, cost),
    db.prepare(CHARGE).bind(laneKey, day, cost),
  ];
  if (marker !== null) batch.push(db.prepare(MARK).bind(marker, day));
  const r = await db.batch(batch);
  if (r[0].meta.changes === 1) return true;
  await db
    .prepare(
      "INSERT INTO counters (key, day, count) VALUES (?1, ?2, 1) " +
        "ON CONFLICT (key, day) DO UPDATE SET count = count + 1 WHERE count < ?3",
    )
    .bind(`cd-${site}`, day, siteShare(CLICK_DROP_COUNT_CAP, sites))
    .run();
  return false;
}
