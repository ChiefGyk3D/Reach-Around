// SPDX-License-Identifier: Apache-2.0
//
// Funnel rollups for clicks (issues #331 and #409), aggregate only.
//
// Two marginal counters, one pair each, never a three-way cell:
//   click_src_daily      site x day x link x source label
//   click_country_daily  site x day x link x country
// The click route increments them when a click arrives (clickCounterStatements),
// so country by link covers every click, with or without a source. Nothing
// finer than a UTC day is kept, and nothing identifies a visitor: a row is a
// count and closed labels.
//
// Caps. Once a (site, day) holds the per-table cap, or the site-wide daily click budget
// is used up, a label that has no row yet goes into ONE overflow row per (site, day)
// ('overflow-all', 'other' or 'ZZ'). Rows per (site, day) are therefore at most the cap
// plus one. Counting never stops: existing cells and the overflow row keep adding.
//
// Seal. The hourly job (rollupClickFunnel) seals a day once it is closed plus a
// grace period; the increment statements write nothing for a sealed day.
//
// Months. A closed month is summed into the monthly tables (with a fold cap),
// kept 25 months. Day cells and seals go after 90 days, once their month is
// rolled up; a hard ceiling drops them anyway so a stuck rollup cannot keep
// them past the retention promise.

import type { QueryMeter } from "./d1budget";
import { meterOf } from "./d1budget";

/** General cells per (site, day), on top of the reserved ones below. */
export const SRC_DAY_CAP = 110;
export const COUNTRY_DAY_CAP = 160;
/** Cells every destination may always create per day (and one `direct` more), whatever the general pool holds. */
export const RESERVED_PER_LINK = 2;
export const SRC_MONTH_CAP = 300;
export const COUNTRY_MONTH_CAP = 400;
/**
 * Clicks per (site, day) the new-cell budget is sized for; each UTC hour gets an equal slice, so
 * flooding in one hour cannot stop new cells for the rest of the day. Past a slice counting goes on
 * (existing cells and the overflow row) and the day is marked as exhausted.
 */
export const SITE_DAY_BUDGET = 50_000;
export const HOUR_BUDGET = Math.ceil(SITE_DAY_BUDGET / 24);
/** The one overflow link per (site, day) and per (site, month): every cell past the cap lands here. */
export const OVERFLOW_LINK = "overflow-all";
export const DAY_KEEP_DAYS = 90;
/** Day cells go at this age even when their month could not be rolled up. */
export const DAY_HARD_KEEP_DAYS = 125;
export const MONTHS_KEPT = 25;
export const GRACE_HOURS = 2;
/** The label of a click that carried no (or no valid) source. */
export const DIRECT = "direct";
/** The fold label for a source. */
export const OTHER = "other";
/** The country counter's label for an unknown country or a folded one. */
export const UNKNOWN_COUNTRY = "ZZ";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;

export function utcDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** The UTC month ("YYYY-MM") of a day or a time. */
export function utcMonth(ms: number): string {
  return new Date(ms).toISOString().slice(0, 7);
}

/**
 * The statements that count one click in the marginal tables. The source
 * counter is written only for a site that lists sources (`src` is null when it
 * does not, and DIRECT is used for a click without a label). Neither statement
 * writes anything for a sealed (site, day).
 */
export function clickCounterStatements(
  db: D1Database,
  p: { site: string; ts: number; linkId: string; src: string | null; listsSources: boolean; country: string | null },
): D1PreparedStatement[] {
  const day = utcDay(p.ts);
  const hour = new Date(p.ts).getUTCHours();
  const out: D1PreparedStatement[] = [];
  const guard = "WHERE NOT EXISTS (SELECT 1 FROM click_seal WHERE site = ?1 AND day = ?2) ";
  // A new cell is made only while this hour's slice of the budget lasts AND one of: the destination
  // is under its reserved cells (a `direct` cell is always reserved), or the general pool is not full.
  // Anything else goes to ONE overflow row per (site, day). Existing cells and the overflow row always
  // keep counting. Rows per (site, day) are at most the general cap plus (RESERVED_PER_LINK + 1) per
  // destination plus the overflow row.
  // ?1 site, ?2 day, ?3 link, ?4 label, ?5 general cap, ?6 reserved per link, ?7 hour budget,
  // ?8 overflow link, ?9 fold label, ?10 the always-reserved label ('' when none), ?11 hour.
  const insert = (table: string, col: string, conflict: string) =>
    `WITH f(fold) AS (SELECT NOT (EXISTS (SELECT 1 FROM ${table} WHERE site = ?1 AND day = ?2 AND link_id = ?3 AND ${col} = ?4) ` +
    "OR (COALESCE((SELECT clicks FROM click_budget WHERE site = ?1 AND day = ?2 AND hour = ?11), 0) < ?7 AND (" +
    `?4 = ?10 OR (SELECT count(*) FROM ${table} WHERE site = ?1 AND day = ?2 AND link_id = ?3 AND ${col} <> ?10) < ?6 OR ` +
    `(SELECT COALESCE(SUM(MAX(c - ?6, 0)), 0) FROM (SELECT count(*) AS c FROM ${table} WHERE site = ?1 AND day = ?2 ` +
    `AND link_id <> ?8 AND ${col} <> ?10 GROUP BY link_id)) < ?5)))) ` +
    `INSERT INTO ${table} (site, day, link_id, ${col}, clicks) ` +
    "SELECT ?1, ?2, CASE WHEN f.fold THEN ?8 ELSE ?3 END, CASE WHEN f.fold THEN ?9 ELSE ?4 END, 1 FROM f " +
    `${guard}` +
    `ON CONFLICT (${conflict}) DO UPDATE SET clicks = clicks + 1`;
  if (p.listsSources) {
    out.push(
      db
        .prepare(insert("click_src_daily", "src", "site, day, link_id, src"))
        .bind(p.site, day, p.linkId, p.src ?? DIRECT, SRC_DAY_CAP, RESERVED_PER_LINK, HOUR_BUDGET, OVERFLOW_LINK, OTHER, DIRECT, hour),
    );
  }
  out.push(
    db
      .prepare(insert("click_country_daily", "country", "site, day, link_id, country"))
      .bind(p.site, day, p.linkId, p.country ?? UNKNOWN_COUNTRY, COUNTRY_DAY_CAP, RESERVED_PER_LINK, HOUR_BUDGET, OVERFLOW_LINK, UNKNOWN_COUNTRY, "", hour),
  );
  // The integrity marker: an overflow row means counting capacity ran out on this (site, day), so the
  // report must not release cells for it. Idempotent; written only when an overflow row exists.
  out.push(
    db
      .prepare(
        "INSERT OR IGNORE INTO click_capacity (site, day, marked_at) SELECT ?1, ?2, ?4 " +
          `${guard}AND (EXISTS (SELECT 1 FROM click_src_daily WHERE site = ?1 AND day = ?2 AND link_id = ?3) ` +
          "OR EXISTS (SELECT 1 FROM click_country_daily WHERE site = ?1 AND day = ?2 AND link_id = ?3))",
      )
      .bind(p.site, day, OVERFLOW_LINK, p.ts - (p.ts % HOUR_MS)),
  );
  // The hourly slice of the site-wide new-cell budget: clicks counted this hour.
  out.push(
    db
      .prepare(
        "INSERT INTO click_budget (site, day, hour, clicks) SELECT ?1, ?2, ?3, 1 " +
          `${guard}ON CONFLICT (site, day, hour) DO UPDATE SET clicks = clicks + 1`,
      )
      .bind(p.site, day, hour),
  );
  return out;
}

export interface FunnelRollupEnv {
  DB: D1Database;
}

export interface FunnelRollupResult {
  sealedDays: number;
  monthsRolled: number;
}

/** Monthly sum of one daily table, folding everything past the top `cap` cells into the fold label. */
function monthSql(daily: string, monthly: string, col: string, fold: string): string {
  return (
    `INSERT INTO ${monthly} (site, month, link_id, ${col}, clicks) ` +
    `WITH s AS (SELECT link_id, ${col} AS v, SUM(clicks) AS c FROM ${daily} ` +
    "WHERE site = ?1 AND substr(day, 1, 7) = ?2 GROUP BY link_id, v), " +
    "r AS (SELECT link_id, v, c, ROW_NUMBER() OVER (ORDER BY (link_id = ?4) ASC, c DESC, link_id, v) AS rn FROM s) " +
    `SELECT ?1, ?2, CASE WHEN rn <= ?3 THEN link_id ELSE ?4 END, CASE WHEN rn <= ?3 THEN v ELSE '${fold}' END, SUM(c) FROM r ` +
    `GROUP BY CASE WHEN rn <= ?3 THEN link_id ELSE ?4 END, CASE WHEN rn <= ?3 THEN v ELSE '${fold}' END`
  );
}

/**
 * The hourly funnel job: seal closed days, roll closed months up, prune. All
 * of it is a few batched queries. It never throws past a failed query (the
 * caller isolates it), and every step is a predicate on age, so a run that
 * stops early is continued by the next one.
 */
export async function rollupClickFunnel(env: FunnelRollupEnv, nowMs: number): Promise<FunnelRollupResult> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const meter: QueryMeter | undefined = meterOf(env.DB);
  const db = env.DB;
  const settled = nowMs - GRACE_HOURS * HOUR_MS;
  const sealBefore = utcDay(settled); // days strictly before this one are closed
  const curMonth = utcMonth(settled); // months strictly before this one are closed
  const result: FunnelRollupResult = { sealedDays: 0, monthsRolled: 0 };

  // 1. Seal every closed day of every site that has counted anything, the quiet days too (a seal
  // is the proof a day is closed, so the report can tell "no clicks" from "not rolled yet"), and
  // find the closed months not yet rolled up. At most 31 days per site per run; the next run goes on.
  const read = await db.batch([
    db.prepare(
      "SELECT u.site AS site, MIN(u.day) AS first_cell, " +
        "(SELECT MAX(day) FROM click_seal s WHERE s.site = u.site) AS last_seal " +
        "FROM (SELECT site, day FROM click_src_daily UNION SELECT site, day FROM click_country_daily) u GROUP BY u.site",
    ),
    db
      .prepare(
        "SELECT u.site AS site, u.m AS m FROM (SELECT site, substr(day, 1, 7) AS m FROM click_src_daily " +
          "UNION SELECT site, substr(day, 1, 7) FROM click_country_daily) u " +
          "WHERE u.m < ?1 AND NOT EXISTS (SELECT 1 FROM click_month_log l WHERE l.site = u.site AND l.month = u.m) " +
          "ORDER BY u.m, u.site LIMIT 8",
      )
      .bind(curMonth),
  ]);
  const sites = (read[0]?.results ?? []) as Array<{ site: string; first_cell: string; last_seal: string | null }>;
  const due = (read[1]?.results ?? []) as Array<{ site: string; m: string }>;
  const seals: D1PreparedStatement[] = [];
  for (const { site, first_cell, last_seal } of sites) {
    let from = last_seal === null || last_seal === undefined ? first_cell : utcDay(Date.parse(`${last_seal}T00:00:00Z`) + DAY_MS);
    for (let n = 0; from < sealBefore && n < 31; n++) {
      seals.push(db.prepare("INSERT OR IGNORE INTO click_seal (site, day, sealed_at) VALUES (?1, ?2, ?3)").bind(site, from, nowMs));
      from = utcDay(Date.parse(`${from}T00:00:00Z`) + DAY_MS);
    }
  }
  if (seals.length > 0) {
    const done = await db.batch(seals);
    result.sealedDays = done.reduce((a, r) => a + (r.meta?.changes ?? 0), 0);
  }

  // 2. Roll each due month: the sums and the log row in one transaction.
  for (const { site, m } of due) {
    if (meter !== undefined && meter.remaining() < 3) break;
    await db.batch([
      // Days of the month that are not sealed yet cannot be: the month is closed and sealing came first.
      db.prepare(monthSql("click_src_daily", "click_src_monthly", "src", OTHER)).bind(site, m, SRC_MONTH_CAP, OVERFLOW_LINK),
      db
        .prepare(monthSql("click_country_daily", "click_country_monthly", "country", UNKNOWN_COUNTRY))
        .bind(site, m, COUNTRY_MONTH_CAP, OVERFLOW_LINK),
      db
        .prepare(
          "INSERT OR IGNORE INTO click_month_log (site, month, rolled_at, exhausted_days) VALUES (?1, ?2, ?3, " +
            "(SELECT count(*) FROM click_capacity WHERE site = ?1 AND substr(day, 1, 7) = ?2))",
        )
        .bind(site, m, nowMs),
    ]);
    result.monthsRolled += 1;
  }

  // 3. Prune.
  // Retention is in whole UTC days: a day is kept while it is fewer than 90 days old, so with `today`
  // being the UTC day of now, day cells dated today minus 90 days or earlier go (90 days kept, not 91).
  // The seal and the budget row of a day go only when none of its cells is left.
  const dayCut = utcDay(nowMs - DAY_KEEP_DAYS * DAY_MS);
  const hardCut = utcDay(nowMs - DAY_HARD_KEEP_DAYS * DAY_MS);
  const [cy, cm] = curMonth.split("-").map(Number) as [number, number];
  const monthIndex = cy * 12 + (cm - 1) - MONTHS_KEPT;
  const monthCut = `${String(Math.floor(monthIndex / 12)).padStart(4, "0")}-${String((monthIndex % 12) + 1).padStart(2, "0")}`;
  const rolled = (t: string) =>
    `DELETE FROM ${t} WHERE (day <= ?1 AND EXISTS (SELECT 1 FROM click_month_log l WHERE l.site = ${t}.site AND l.month = substr(${t}.day, 1, 7))) OR day <= ?2`;
  const leftover = (t: string) => `SELECT 1 FROM ${t} c WHERE c.site = %T.site AND c.day = %T.day`;
  const noCells = (t: string) =>
    `NOT EXISTS (${leftover("click_src_daily").replace(/%T/g, t)}) AND NOT EXISTS (${leftover("click_country_daily").replace(/%T/g, t)})`;
  await db.batch([
    db.prepare(rolled("click_src_daily")).bind(dayCut, hardCut),
    db.prepare(rolled("click_country_daily")).bind(dayCut, hardCut),
    db.prepare(`DELETE FROM click_seal WHERE day <= ?1 AND ${noCells("click_seal")}`).bind(dayCut),
    db.prepare(`DELETE FROM click_budget WHERE day <= ?1 AND ${noCells("click_budget")}`).bind(dayCut),
    db.prepare(`DELETE FROM click_capacity WHERE day <= ?1 AND ${noCells("click_capacity")}`).bind(dayCut),
    db.prepare("DELETE FROM click_src_monthly WHERE month < ?1").bind(monthCut),
    db.prepare("DELETE FROM click_country_monthly WHERE month < ?1").bind(monthCut),
    db.prepare("DELETE FROM click_month_log WHERE month < ?1").bind(monthCut),
  ]);
  return result;
}
