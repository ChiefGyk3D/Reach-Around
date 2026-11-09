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
// Caps. Once a (site, day) holds the per-table cap, a label that has no row yet
// folds into 'other' (source) or 'ZZ' (country). The row count of a (site, day)
// is therefore at most the cap plus one fold row per link.
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

export const SRC_DAY_CAP = 150;
export const COUNTRY_DAY_CAP = 200;
export const SRC_MONTH_CAP = 300;
export const COUNTRY_MONTH_CAP = 400;
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
  const out: D1PreparedStatement[] = [];
  const guard = "WHERE NOT EXISTS (SELECT 1 FROM click_seal WHERE site = ?1 AND day = ?2) ";
  const pick = (table: string, col: string, other: string, cap: string) =>
    `CASE WHEN EXISTS (SELECT 1 FROM ${table} WHERE site = ?1 AND day = ?2 AND link_id = ?3 AND ${col} = ?4) ` +
    `OR (SELECT count(*) FROM ${table} WHERE site = ?1 AND day = ?2) < ${cap} THEN ?4 ELSE '${other}' END`;
  if (p.listsSources) {
    out.push(
      db
        .prepare(
          "INSERT INTO click_src_daily (site, day, link_id, src, clicks) " +
            `SELECT ?1, ?2, ?3, ${pick("click_src_daily", "src", OTHER, "?5")}, 1 ${guard}` +
            "ON CONFLICT (site, day, link_id, src) DO UPDATE SET clicks = clicks + 1",
        )
        .bind(p.site, day, p.linkId, p.src ?? DIRECT, SRC_DAY_CAP),
    );
  }
  out.push(
    db
      .prepare(
        "INSERT INTO click_country_daily (site, day, link_id, country, clicks) " +
          `SELECT ?1, ?2, ?3, ${pick("click_country_daily", "country", UNKNOWN_COUNTRY, "?5")}, 1 ${guard}` +
          "ON CONFLICT (site, day, link_id, country) DO UPDATE SET clicks = clicks + 1",
      )
      .bind(p.site, day, p.linkId, p.country ?? UNKNOWN_COUNTRY, COUNTRY_DAY_CAP),
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
    "r AS (SELECT link_id, v, c, ROW_NUMBER() OVER (ORDER BY c DESC, link_id, v) AS rn FROM s) " +
    `SELECT ?1, ?2, link_id, CASE WHEN rn <= ?3 THEN v ELSE '${fold}' END, SUM(c) FROM r ` +
    `GROUP BY link_id, CASE WHEN rn <= ?3 THEN v ELSE '${fold}' END`
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

  // 1. Seal every closed day that has cells, and find the closed months not yet rolled up.
  const sealed = await db.batch([
    db
      .prepare(
        "INSERT OR IGNORE INTO click_seal (site, day, sealed_at) " +
          "SELECT site, day, ?1 FROM (SELECT site, day FROM click_src_daily UNION SELECT site, day FROM click_country_daily) " +
          "WHERE day < ?2",
      )
      .bind(nowMs, sealBefore),
    db
      .prepare(
        "SELECT site, m FROM (SELECT site, substr(day, 1, 7) AS m FROM click_src_daily " +
          "UNION SELECT site, substr(day, 1, 7) FROM click_country_daily) " +
          "WHERE m < ?1 AND NOT EXISTS (SELECT 1 FROM click_month_log l WHERE l.site = site AND l.month = m) " +
          "ORDER BY m, site LIMIT 8",
      )
      .bind(curMonth),
  ]);
  result.sealedDays = sealed[0]?.meta?.changes ?? 0;
  const due = (sealed[1]?.results ?? []) as Array<{ site: string; m: string }>;

  // 2. Roll each due month: the sums and the log row in one transaction.
  for (const { site, m } of due) {
    if (meter !== undefined && meter.remaining() < 3) break;
    await db.batch([
      // Days of the month that are not sealed yet cannot be: the month is closed and sealing came first.
      db.prepare(monthSql("click_src_daily", "click_src_monthly", "src", OTHER)).bind(site, m, SRC_MONTH_CAP),
      db
        .prepare(monthSql("click_country_daily", "click_country_monthly", "country", UNKNOWN_COUNTRY))
        .bind(site, m, COUNTRY_MONTH_CAP),
      db.prepare("INSERT OR IGNORE INTO click_month_log (site, month, rolled_at) VALUES (?1, ?2, ?3)").bind(site, m, nowMs),
    ]);
    result.monthsRolled += 1;
  }

  // 3. Prune.
  const dayCut = utcDay(nowMs - DAY_KEEP_DAYS * DAY_MS);
  const hardCut = utcDay(nowMs - DAY_HARD_KEEP_DAYS * DAY_MS);
  const [cy, cm] = curMonth.split("-").map(Number) as [number, number];
  const monthIndex = cy * 12 + (cm - 1) - MONTHS_KEPT;
  const monthCut = `${String(Math.floor(monthIndex / 12)).padStart(4, "0")}-${String((monthIndex % 12) + 1).padStart(2, "0")}`;
  const rolled = (t: string) =>
    `DELETE FROM ${t} WHERE (day < ?1 AND EXISTS (SELECT 1 FROM click_month_log l WHERE l.site = ${t}.site AND l.month = substr(${t}.day, 1, 7))) OR day < ?2`;
  await db.batch([
    db.prepare(rolled("click_src_daily")).bind(dayCut, hardCut),
    db.prepare(rolled("click_country_daily")).bind(dayCut, hardCut),
    db.prepare("DELETE FROM click_seal WHERE day < ?1").bind(dayCut),
    db.prepare("DELETE FROM click_src_monthly WHERE month < ?1").bind(monthCut),
    db.prepare("DELETE FROM click_country_monthly WHERE month < ?1").bind(monthCut),
    db.prepare("DELETE FROM click_month_log WHERE month < ?1").bind(monthCut),
  ]);
  return result;
}
