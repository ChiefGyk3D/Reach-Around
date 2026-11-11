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
// plus one per destination kind. Counting never stops: existing cells and the overflow row keep adding.
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
 * New cells a (site, day, UTC hour) may create. The admission budget counts NEW CELLS only, never
 * clicks, so a flood of clicks on existing cells cannot use it up; each hour has its own slice, so
 * an early burst of new labels cannot stop new cells for the rest of the day. Past a slice, counting
 * goes on (existing cells and the overflow row) and the day is recorded in `click_capacity`.
 */
export const HOUR_BUDGET = 150;
/** The overflow link per (site, day/month, destination kind): every cell past the cap lands here. */
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
  p: { site: string; ts: number; linkId: string; destinationKind?: "link" | "social_icon"; src: string | null; listsSources: boolean; country: string | null },
): D1PreparedStatement[] {
  const day = utcDay(p.ts);
  const hour = new Date(p.ts).getUTCHours();
  const destinationKind = p.destinationKind ?? "link";
  const out: D1PreparedStatement[] = [];
  const guard = "WHERE NOT EXISTS (SELECT 1 FROM click_seal WHERE site = ?1 AND day = ?2) ";
  // A new cell is made only while this hour's slice of the budget lasts AND one of: the destination
  // is under its reserved cells (a `direct` cell is always reserved), or the general pool is not full.
  // Anything else goes to one overflow row per (site, day, destination kind). Existing cells and the overflow row always
  // keep counting. Rows per (site, day) are at most the general cap plus (RESERVED_PER_LINK + 1) per
  // destination-kind pair plus one overflow row per kind.
  // Shared: ?1 site, ?2 day, ?3 link, ?6 reserved per link, ?7 hour budget, ?8 overflow link, ?11 hour.
  // The fold test for one table: label, general cap and always-reserved label are the given parameters.
  const fold = (table: string, col: string, label: string, cap: string, direct: string) =>
    `NOT (EXISTS (SELECT 1 FROM ${table} WHERE site = ?1 AND day = ?2 AND link_id = ?3 AND destination_kind = ?16 AND ${col} = ${label}) ` +
    "OR (COALESCE((SELECT clicks FROM click_budget WHERE site = ?1 AND day = ?2 AND hour = ?11), 0) < ?7 AND (" +
    `${label} = ${direct} OR (SELECT count(*) FROM ${table} WHERE site = ?1 AND day = ?2 AND link_id = ?3 AND destination_kind = ?16 AND ${col} <> ${direct}) < ?6 OR ` +
    `(SELECT COALESCE(SUM(MAX(c - ?6, 0)), 0) FROM (SELECT count(*) AS c FROM ${table} WHERE site = ?1 AND day = ?2 ` +
    `AND link_id <> ?8 AND ${col} <> ${direct} GROUP BY link_id, destination_kind)) < ${cap})))`;
  // ?4 label, ?5 cap, ?9 fold label, ?10 direct label.
  const insert = (table: string, col: string, conflict: string, direct: string) =>
    `WITH f(fold) AS (SELECT ${fold(table, col, "?4", "?5", direct)}) ` +
    `INSERT INTO ${table} (site, day, link_id, destination_kind, ${col}, clicks) ` +
    "SELECT ?1, ?2, CASE WHEN f.fold THEN ?8 ELSE ?3 END, ?16, CASE WHEN f.fold THEN ?9 ELSE ?4 END, 1 FROM f " +
    `${guard}` +
    `ON CONFLICT (${conflict}) DO UPDATE SET clicks = clicks + 1`;
  const srcLabel = p.src ?? DIRECT;
  const countryLabel = p.country ?? UNKNOWN_COUNTRY;
  // The capacity counter, FIRST so it sees the state the inserts below see: a click that would
  // overflow on either axis adds ONE to that day's folded count (a click, not a cell). It records the
  // UTC hour the day's overflow began. Counted clicks are the denominator (country counts every click).
  const srcFold = p.listsSources ? `${fold("click_src_daily", "src", "?4", "?5", "?10")} OR ` : "";
  out.push(
    db
      .prepare(
        "INSERT INTO click_capacity (site, day, marked_at, first_hour, folded) SELECT ?1, ?2, ?15, ?11, 1 " +
          `${guard}AND (${srcFold}${fold("click_country_daily", "country", "?12", "?13", "''")}) ` +
          "ON CONFLICT (site, day) DO UPDATE SET folded = folded + 1",
      )
      .bind(
        p.site, day, p.linkId, srcLabel, SRC_DAY_CAP, RESERVED_PER_LINK, HOUR_BUDGET, OVERFLOW_LINK, null,
        DIRECT, hour, countryLabel, COUNTRY_DAY_CAP, null, p.ts - (p.ts % HOUR_MS), destinationKind,
      ),
  );
  if (p.listsSources) {
    out.push(
      db
        .prepare(insert("click_src_daily", "src", "site, day, link_id, destination_kind, src", "?10"))
        .bind(p.site, day, p.linkId, srcLabel, SRC_DAY_CAP, RESERVED_PER_LINK, HOUR_BUDGET, OVERFLOW_LINK, OTHER, DIRECT, hour, null, null, null, null, destinationKind),
    );
  }
  out.push(
    db
      .prepare(insert("click_country_daily", "country", "site, day, link_id, destination_kind, country", "''"))
      .bind(p.site, day, p.linkId, countryLabel, COUNTRY_DAY_CAP, RESERVED_PER_LINK, HOUR_BUDGET, OVERFLOW_LINK, UNKNOWN_COUNTRY, null, hour, null, null, null, null, destinationKind),
  );
  // The hourly slice of the new-cell budget, AFTER the inserts: it counts the cells THIS click made.
  // A cell the insert just made holds exactly 1 click (an older cell holds more, and a folded click
  // makes no (link, label) cell), so click volume on existing cells never uses the budget up.
  const made = (table: string, col: string, label: string) =>
    `(SELECT count(*) FROM ${table} WHERE site = ?1 AND day = ?2 AND link_id = ?3 AND destination_kind = ?7 AND ${col} = ${label} AND clicks = 1)`;
  const madeSrc = p.listsSources ? `${made("click_src_daily", "src", "?4")} + ` : "";
  const madeAll = `${madeSrc}${made("click_country_daily", "country", "?5")}`;
  out.push(
    db
      .prepare(
        `INSERT INTO click_budget (site, day, hour, clicks) SELECT ?1, ?2, ?6, ${madeAll} ` +
          `${guard}AND (${madeAll}) > 0 ` +
          "ON CONFLICT (site, day, hour) DO UPDATE SET clicks = clicks + excluded.clicks",
      )
      .bind(p.site, day, p.linkId, srcLabel, countryLabel, hour, destinationKind),
  );
  return out;
}

export interface FunnelRollupEnv {
  DB: D1Database;
  /**
   * The configured site ids. Every one gets a state row on its first run, so its quiet days are
   * sealed and its quiet months are logged even with no click at all; a site with cells but no
   * entry here is picked up from its cells.
   */
  sites?: readonly string[];
}

export interface FunnelRollupResult {
  sealedDays: number;
  monthsRolled: number;
}

/** Monthly sum of one daily table, folding everything past the top `cap` cells into the fold label. */
function monthSql(daily: string, monthly: string, col: string, fold: string): string {
  return (
    `INSERT INTO ${monthly} (site, month, link_id, destination_kind, ${col}, clicks) ` +
    `WITH s AS (SELECT link_id, destination_kind AS k, ${col} AS v, SUM(clicks) AS c FROM ${daily} ` +
    "WHERE site = ?1 AND substr(day, 1, 7) = ?2 GROUP BY link_id, k, v), " +
    "r AS (SELECT link_id, k, v, c, ROW_NUMBER() OVER (ORDER BY (link_id = ?4) ASC, c DESC, link_id, v) AS rn FROM s) " +
    `SELECT ?1, ?2, CASE WHEN rn <= ?3 THEN link_id ELSE ?4 END, k, CASE WHEN rn <= ?3 THEN v ELSE '${fold}' END, SUM(c) FROM r ` +
    `GROUP BY CASE WHEN rn <= ?3 THEN link_id ELSE ?4 END, k, CASE WHEN rn <= ?3 THEN v ELSE '${fold}' END`
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

  // 1. Seal every closed day of every known site, the quiet days too (a seal is the proof a day is
  // closed, so the report can tell "no clicks" from "not rolled yet"), and find the closed months
  // not yet logged, the empty ones too. A site is known from the configured registry (state row from
  // the day it was first seen) or from its cells. Catch-up is bounded: at most 31 days per site and 8
  // months per run, never before the retention window; the next run goes on.
  const registry = [...new Set(env.sites ?? [])];
  const today = utcDay(nowMs);
  const read = await db.batch([
    db.prepare(
      "INSERT OR IGNORE INTO click_funnel_state (site, from_day) SELECT u.site, MIN(u.day) FROM " +
        "(SELECT site, day FROM click_src_daily UNION SELECT site, day FROM click_country_daily " +
        "UNION SELECT site, strftime('%Y-%m-%d', ts / 1000, 'unixepoch') AS day FROM click_events) u GROUP BY u.site",
    ),
    ...registry.map((id) => db.prepare("INSERT OR IGNORE INTO click_funnel_state (site, from_day) VALUES (?1, ?2)").bind(id, today)),
    db.prepare(
      "SELECT st.site AS site, st.from_day AS from_day, (SELECT MAX(day) FROM click_seal s WHERE s.site = st.site) AS last_seal " +
        "FROM click_funnel_state st",
    ),
    db.prepare("SELECT site, month FROM click_month_log"),
  ]);
  const states = (read[read.length - 2]?.results ?? []) as Array<{ site: string; from_day: string; last_seal: string | null }>;
  const logged = new Set(((read[read.length - 1]?.results ?? []) as Array<{ site: string; month: string }>).map((r) => `${r.site}|${r.month}`));
  const firstKept = utcDay(nowMs - (DAY_KEEP_DAYS - 1) * DAY_MS); // the oldest day still kept
  const seals: D1PreparedStatement[] = [];
  const dueAll: Array<{ site: string; m: string }> = [];
  const [cy0, cm0] = curMonth.split("-").map(Number) as [number, number];
  const monthText = (idx: number) => `${String(Math.floor(idx / 12)).padStart(4, "0")}-${String((idx % 12) + 1).padStart(2, "0")}`;
  const curIndex = cy0 * 12 + (cm0 - 1);
  for (const { site, from_day, last_seal } of states) {
    let from = last_seal === null || last_seal === undefined ? from_day : utcDay(Date.parse(`${last_seal}T00:00:00Z`) + DAY_MS);
    if (from < firstKept) from = firstKept;
    for (let n = 0; from < sealBefore && n < 31; n++) {
      seals.push(
        db
          .prepare(
            "INSERT INTO view_hour_daily (site, day, hour, bot_class, views, clicks) " +
              "SELECT ?1, ?2, CAST(strftime('%H', ts / 1000, 'unixepoch') AS INTEGER), 'h', 0, COUNT(*) " +
              "FROM click_events WHERE site = ?1 AND ts >= strftime('%s', ?2) * 1000 " +
              "AND ts < (strftime('%s', ?2) + 86400) * 1000 GROUP BY 3 " +
              "ON CONFLICT(site, day, hour, bot_class) DO UPDATE SET clicks = excluded.clicks",
          )
          .bind(site, from),
      );
      seals.push(db.prepare("INSERT OR IGNORE INTO click_seal (site, day, sealed_at) VALUES (?1, ?2, ?3)").bind(site, from, nowMs));
      from = utcDay(Date.parse(`${from}T00:00:00Z`) + DAY_MS);
    }
    const [fy, fm] = from_day.slice(0, 7).split("-").map(Number) as [number, number];
    for (let idx = Math.max(fy * 12 + (fm - 1), curIndex - MONTHS_KEPT); idx < curIndex; idx++) {
      const m = monthText(idx);
      if (!logged.has(`${site}|${m}`)) dueAll.push({ site, m });
    }
  }
  dueAll.sort((x, y) => (x.m < y.m ? -1 : x.m > y.m ? 1 : x.site < y.site ? -1 : 1));
  const due = dueAll.slice(0, 8);
  if (seals.length > 0) {
    const done = await db.batch(seals);
    result.sealedDays = done
      .filter((_, i) => i % 2 === 1)
      .reduce((a, r) => a + (r.meta?.changes ?? 0), 0);
  }

  // 2. Roll each due month: the sums and the log row in one transaction.
  for (const { site, m } of due) {
    // A month roll is one batch (one query unit); one more stays for the prune batch below.
    if (meter !== undefined && meter.remaining() < 2) break;
    await db.batch([
      // Days of the month that are not sealed yet cannot be: the month is closed and sealing came first.
      db.prepare(monthSql("click_src_daily", "click_src_monthly", "src", OTHER)).bind(site, m, SRC_MONTH_CAP, OVERFLOW_LINK),
      db
        .prepare(monthSql("click_country_daily", "click_country_monthly", "country", UNKNOWN_COUNTRY))
        .bind(site, m, COUNTRY_MONTH_CAP, OVERFLOW_LINK),
      db
        .prepare(
          "INSERT OR IGNORE INTO click_month_log (site, month, rolled_at, exhausted_days, folded_clicks) VALUES (?1, ?2, ?3, " +
            "(SELECT count(*) FROM click_capacity WHERE site = ?1 AND substr(day, 1, 7) = ?2), " +
            "(SELECT COALESCE(SUM(folded), 0) FROM click_capacity WHERE site = ?1 AND substr(day, 1, 7) = ?2))",
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
    db.prepare("DELETE FROM view_hour_daily WHERE day <= ?1").bind(dayCut),
    db.prepare(`DELETE FROM click_seal WHERE day <= ?1 AND ${noCells("click_seal")}`).bind(dayCut),
    db.prepare(`DELETE FROM click_budget WHERE day <= ?1 AND ${noCells("click_budget")}`).bind(dayCut),
    db.prepare(`DELETE FROM click_capacity WHERE day <= ?1 AND ${noCells("click_capacity")}`).bind(dayCut),
    db.prepare("DELETE FROM click_src_monthly WHERE month < ?1").bind(monthCut),
    db.prepare("DELETE FROM click_country_monthly WHERE month < ?1").bind(monthCut),
    db.prepare("DELETE FROM click_month_log WHERE month < ?1").bind(monthCut),
  ]);
  return result;
}
