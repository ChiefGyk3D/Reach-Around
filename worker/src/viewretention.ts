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
// Deletes run in bounded batches so one run cannot hold the database for long,
// and within the invocation's D1 query budget (src/d1budget.ts): round one is
// ONE db.batch() holding the overdue check and every step's first bounded
// delete; later rounds repeat only the steps that came back full, while the
// budget lasts. A run that stops with work left records `purge_backlog` in
// view_meta and the next run simply continues (every delete is a predicate on
// age, so it resumes where it stopped); a run that finishes clears the marker.
// A bad keep value falls back to the default; it never means "delete all".

import { QueryMeter, meterOf, meteredDb, queryBudget } from "./d1budget";

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const BATCH = 500;
const MAX_BATCHES = 200;

export const DEFAULT_RAW_KEEP_DAYS = 7;
export const MAX_RAW_KEEP_DAYS = 90;
/** Unrolled raw rows are dropped at this multiple of the keep window (capped at MAX_RAW_KEEP_DAYS). */
export const HARD_CEILING_FACTOR = 3;
export const AGGREGATE_KEEP_DAYS = 90;

export interface RetentionEnv {
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
  const steps: Step[] = [
    {
      // CONTROL:seen-purge  unconditional: pseudonymous, and the rollup never reads it.
      sql: "DELETE FROM visitor_seen WHERE (day, h) IN (SELECT day, h FROM visitor_seen WHERE day < ?1 LIMIT ?2)",
      args: [today],
    },
    {
      // CONTROL:raw-purge  a rolled day's rows, only up to the id the aggregate counted.
      sql:
        "DELETE FROM view_events WHERE id IN (SELECT e.id FROM view_events e JOIN view_rollup_log l " +
        "ON l.day = strftime('%Y-%m-%d', e.ts / 1000, 'unixepoch') WHERE e.ts < ?1 AND e.id <= l.max_id LIMIT ?2)",
      args: [cutoff],
    },
    {
      // The hard ceiling: unrolled (and uncounted late) rows past min(3 x keep, 90 days) go.
      sql: "DELETE FROM view_events WHERE id IN (SELECT id FROM view_events WHERE ts < ?1 LIMIT ?2)",
      args: [hardCutoff],
    },
    { sql: "DELETE FROM view_daily WHERE day IN (SELECT day FROM view_daily WHERE day < ?1 LIMIT ?2)", args: [aggCutoff] },
    { sql: "DELETE FROM view_dim_daily WHERE day IN (SELECT day FROM view_dim_daily WHERE day < ?1 LIMIT ?2)", args: [aggCutoff] },
    { sql: "DELETE FROM view_geo_daily WHERE day IN (SELECT day FROM view_geo_daily WHERE day < ?1 LIMIT ?2)", args: [aggCutoff] },
    { sql: "DELETE FROM view_rollup_log WHERE day IN (SELECT day FROM view_rollup_log WHERE day < ?1 LIMIT ?2)", args: [aggCutoff] },
    {
      sql: "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'bud:' AND key < ?1 LIMIT ?2)",
      args: [`bud:${padHour(hour - 48)}`],
    },
    {
      sql: "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'cap:' AND key < ?1 LIMIT ?2)",
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
          "SELECT 1 AS x FROM view_events WHERE ts < ?1 AND " +
            "strftime('%Y-%m-%d', ts / 1000, 'unixepoch') NOT IN (SELECT day FROM view_rollup_log) LIMIT 1",
        )
        .bind(hardCutoff),
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
