// SPDX-License-Identifier: Apache-2.0
//
// Retention for the page-view tables, run by the hourly and daily crons.
//
//   visitor_seen   every row before today's UTC date, unconditionally. It is
//                  pseudonymous (a daily hash) and the rollup never reads it
//                  (uniques come from the raw `first` flags), so it never
//                  waits on the rollup.
//   view_events    rows older than the raw keep window (default 7 days, Plan 4
//                  open decision P-3, RAW_KEEP_DAYS overrides, ceiling 90),
//                  but ONLY rows whose UTC day has a view_rollup_log row and
//                  only up to that row's max_id (a late row above it is not
//                  yet counted, so it stays until the rollup folds it in). A
//                  day not yet rolled up is never deleted by age until the
//                  hard ceiling of min(3x the keep window, 90 days); then it
//                  is dropped and view_meta('rollup_overdue') records the
//                  date. The flag is cleared by the first later run (a later
//                  UTC day) that finds nothing left beyond the ceiling.
//   view_daily, view_dim_daily, view_geo_daily, view_rollup_log
//                  rows older than 90 days.
//   view_meta      hourly counters. Their keys are `bud:<hour>:<route>` and
//                  `cap:<hour>:<hash>` with the hour zero-padded to eight
//                  digits, so keys sort by hour and expiry is a plain range
//                  delete on the primary key (no computed-suffix scan):
//                  bud:* older than 48 hours, cap:* older than the previous
//                  hour. Other keys are never touched.
//
// SITES (docs/analytics-design.md 3.6 and threat B1). A raw row is purgeable only
// if ITS site's day is sealed and its id is at most that log row's max_id, and the
// unrolled-day ceiling is evaluated per site. A site may set a SHORTER raw keep
// (raw_keep_days in SITES); the effective keep is min(site, global), so no site
// can keep rows longer than RAW_KEEP_DAYS or the 90 day ceiling. Rows of a site
// that is no longer in the config (or of any site while SITES is broken) age out
// with the global window: retention iterates the data, not the config.
//
// The network class (`net`) is cleared from a raw row once it is counted: when
// the day's log row for the row's site is more than a day old (past the window in
// which a late row re-rolls the day) and the row's id is at most its max_id, the
// aggregate already holds the class, and the raw row goes back to 'unknown'
// (decision D-A7). A late row above max_id keeps its class until it is counted.
//
// Deletes run in bounded batches so one run cannot hold the database for long,
// and within the invocation's D1 query budget (src/d1budget.ts): round one is
// ONE db.batch() holding the overdue check and every step's first bounded
// delete; later rounds repeat only the steps that came back full, while the
// budget lasts. A run that stops with work left records `purge_backlog` in
// view_meta and the next run simply continues (every delete is a predicate on
// age, so it resumes where it stopped); a run that finishes clears the marker.
// A bad keep value falls back to the default; it never means "delete all".

import { QueryMeter, meterOf, meteredDb, queryBudget } from "./d1budget";
import { loadSites, type SitesEnv } from "./sites";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const BATCH = 500;
const MAX_BATCHES = 200;

export const DEFAULT_RAW_KEEP_DAYS = 7;
export const MAX_RAW_KEEP_DAYS = 90;
/** Unrolled raw rows are dropped at this multiple of the keep window (capped at MAX_RAW_KEEP_DAYS). */
export const HARD_CEILING_FACTOR = 3;
export const AGGREGATE_KEEP_DAYS = 90;

export interface RetentionEnv extends SitesEnv {
  DB: D1Database;
  RAW_KEEP_DAYS?: string;
  QUERY_BUDGET?: string;
}

export interface PurgeOptions {
  /** Rows per delete statement (default 500). */
  batch?: number;
  /** Rounds of delete batches per run (default 200). */
  maxBatches?: number;
}

/** Hour number as an eight-digit, zero-padded string so keys sort by hour. */
export function padHour(hour: number): string {
  return String(Math.max(0, Math.trunc(hour))).padStart(8, "0");
}

function keepDays(raw: string | undefined): number {
  if (typeof raw !== "string" || !/^\d{1,9}$/.test(raw)) return DEFAULT_RAW_KEEP_DAYS;
  const n = Number(raw);
  if (n < 1) return DEFAULT_RAW_KEEP_DAYS;
  return Math.min(n, MAX_RAW_KEEP_DAYS);
}

interface Step {
  sql: string;
  args: Array<string | number>;
}

/** Purge every view table, inside the invocation's D1 query budget. */
export async function purgeViews(env: RetentionEnv, nowMs: number, opts: PurgeOptions = {}): Promise<void> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const outer = meterOf(env.DB);
  const meter = outer ?? new QueryMeter(queryBudget(env.QUERY_BUDGET));
  const db = outer ? env.DB : meteredDb(env.DB, meter);
  const batch = opts.batch ?? BATCH;
  const maxRounds = opts.maxBatches ?? MAX_BATCHES;
  const today = new Date(nowMs).toISOString().slice(0, 10);
  const keep = keepDays(env.RAW_KEEP_DAYS);
  const cutoff = Math.floor(nowMs - keep * DAY_MS);
  const hardCutoff = Math.floor(nowMs - Math.min(keep * HARD_CEILING_FACTOR, MAX_RAW_KEEP_DAYS) * DAY_MS);
  const aggCutoff = new Date(nowMs - AGGREGATE_KEEP_DAYS * DAY_MS).toISOString().slice(0, 10);
  const hour = Math.floor(nowMs / HOUR_MS);
  // Per-site raw windows: a VALUES list (site, cutoff, hard cutoff) for the
  // sites that set a shorter keep. The dummy row keeps the list non-empty; the
  // empty site never matches a real one. Everything else uses the global window.
  const lows: Array<{ site: string; keep: number }> = [];
  for (const site of loadSites(env).sites) {
    if (site.rawKeepDays !== undefined && site.rawKeepDays < keep) lows.push({ site: site.id, keep: site.rawKeepDays });
  }
  const kArgs: Array<string | number> = ["", 0, 0];
  for (const l of lows) {
    kArgs.push(
      l.site,
      Math.floor(nowMs - l.keep * DAY_MS),
      Math.floor(nowMs - Math.min(l.keep * HARD_CEILING_FACTOR, MAX_RAW_KEEP_DAYS) * DAY_MS),
    );
  }
  const kRows = Array.from({ length: kArgs.length / 3 }, () => "(?,?,?)").join(",");
  // Parameters are positional: the k list first, then the step's own, then the batch size.
  const kCte = `WITH k(site, cut, hard) AS (VALUES ${kRows}) `;
  const cutOf = "COALESCE((SELECT k.cut FROM k WHERE k.site = e.site), ?)";
  const hardOf = "COALESCE((SELECT k.hard FROM k WHERE k.site = e.site), ?)";
  const sealedJoin = "JOIN view_rollup_log l ON l.site = e.site AND l.day = strftime('%Y-%m-%d', e.ts / 1000, 'unixepoch')";
  const steps: Step[] = [
    {
      // CONTROL:seen-purge  unconditional: pseudonymous, and the rollup never reads it.
      sql: "DELETE FROM visitor_seen WHERE (day, h) IN (SELECT day, h FROM visitor_seen WHERE day < ? LIMIT ?)",
      args: [today],
    },
    {
      // CONTROL:raw-purge  a rolled day's rows, only up to the id the aggregate counted, per site.
      sql:
        `DELETE FROM view_events WHERE id IN (${kCte}SELECT e.id FROM view_events e ${sealedJoin} ` +
        `WHERE e.ts < ${cutOf} AND e.id <= l.max_id LIMIT ?)`,
      args: [...kArgs, cutoff],
    },
    {
      // The hard ceiling: unrolled (and uncounted late) rows past min(3 x keep, 90 days) go, per site.
      sql:
        `DELETE FROM view_events WHERE id IN (${kCte}SELECT e.id FROM view_events e ` +
        `WHERE e.ts < ${hardOf} OR typeof(e.ts) <> 'integer' LIMIT ?)`,
      args: [...kArgs, hardCutoff],
    },
    {
      // CONTROL:net-clear  the class is already in the aggregate once the day is counted and settled.
      sql:
        `UPDATE view_events SET net = 'unknown' WHERE id IN (SELECT e.id FROM view_events e ${sealedJoin} ` +
        "WHERE e.net <> 'unknown' AND e.id <= l.max_id AND l.rolled_at < ? LIMIT ?)",
      args: [Math.floor(nowMs - DAY_MS)],
    },
    { sql: "DELETE FROM view_daily WHERE day IN (SELECT day FROM view_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    { sql: "DELETE FROM view_dim_daily WHERE day IN (SELECT day FROM view_dim_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    { sql: "DELETE FROM view_geo_daily WHERE day IN (SELECT day FROM view_geo_daily WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    { sql: "DELETE FROM view_rollup_log WHERE day IN (SELECT day FROM view_rollup_log WHERE day < ? LIMIT ?)", args: [aggCutoff] },
    {
      sql: "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'bud:' AND key < ? LIMIT ?)",
      args: [`bud:${padHour(hour - 48)}`],
    },
    {
      sql: "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'cap:' AND key < ? LIMIT ?)",
      args: [`cap:${padHour(hour - 1)}`],
    },
  ];
  const del = (st: Step) => db.prepare(st.sql).bind(...st.args, batch);

  let failure: unknown = null;
  let backlog = false;
  let hardHit = false;
  let flagDay = "";
  try {
    // Round one: the overdue check, then every step once, in ONE query.
    const first = await db.batch([
      db
        .prepare(
          `${kCte}SELECT 1 AS x FROM view_events e WHERE e.ts < ${hardOf} AND ` +
            "NOT EXISTS (SELECT 1 FROM view_rollup_log l WHERE l.site = e.site AND l.day = strftime('%Y-%m-%d', e.ts / 1000, 'unixepoch')) LIMIT 1",
        )
        .bind(...kArgs, hardCutoff),
      db.prepare("SELECT value FROM view_meta WHERE key = 'rollup_overdue'"),
      ...steps.map(del),
    ]);
    hardHit = (first[0]?.results ?? []).length > 0;
    flagDay = String(((first[1]?.results ?? [])[0] as { value?: string } | undefined)?.value ?? "");
    let open = steps.filter((_, i) => (first[2 + i]?.meta?.changes ?? 0) >= batch);
    // Later rounds repeat only the full steps; two queries stay free for the closing batch.
    for (let round = 1; open.length > 0 && round < maxRounds; round++) {
      if (meter.remaining() < 3) break;
      const res = await db.batch(open.map(del));
      open = open.filter((_, i) => (res[i]?.meta?.changes ?? 0) >= batch);
    }
    backlog = open.length > 0;
  } catch (e) {
    failure = e;
  }

  if (failure === null) {
    const close: D1PreparedStatement[] = [];
    if (backlog) {
      close.push(
        db
          .prepare(
            "INSERT INTO view_meta (key, value) VALUES ('purge_backlog', ?1) " +
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          )
          .bind(today),
      );
    } else {
      close.push(db.prepare("DELETE FROM view_meta WHERE key = 'purge_backlog'"));
    }
    if (hardHit) {
      close.push(
        db
          .prepare(
            "INSERT INTO view_meta (key, value) VALUES ('rollup_overdue', ?1) " +
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
          )
          .bind(today),
      );
    } else if (flagDay !== "" && flagDay < today && !backlog) {
      // Nothing is beyond the ceiling any more and the flag is from an earlier day: clear it.
      close.push(db.prepare("DELETE FROM view_meta WHERE key = 'rollup_overdue'"));
    }
    try {
      await db.batch(close);
    } catch (e) {
      failure = e;
    }
  }
  if (failure !== null) throw failure;
}
