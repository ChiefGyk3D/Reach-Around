// SPDX-License-Identifier: Apache-2.0
//
// Retention for the page-view tables, run by the daily cron.
//
//   visitor_seen   every row before today's UTC date (the daily hash lives
//                  one UTC day; the 00:05 cron keeps that within about 24 h)
//   view_events    rows older than the raw keep window. Default 7 days (Plan 4
//                  open decision P-3), RAW_KEEP_DAYS overrides, ceiling 90.
//                  NOTE: the daily rollup (Plan 4 Task A5) is not built yet,
//                  so raw rows are deleted by age alone. When A5 lands it must
//                  roll a day up before this purge reaches it.
//   view_meta      hourly counters. Their keys are `bud:<hour>:<route>` and
//                  `cap:<hour>:<hash>` with the hour zero-padded to eight
//                  digits, so keys sort by hour and expiry is a plain range
//                  delete on the primary key (no computed-suffix scan):
//                  bud:* older than 48 hours, cap:* older than the previous
//                  hour. Other keys are never touched.
//
// Deletes run in bounded batches so one run cannot hold the database for long.
// A run that hits its batch budget with work left records `purge_backlog` in
// view_meta and the next run simply continues (every delete is a predicate on
// age, so it resumes where it stopped); a run that finishes clears the marker.
// A bad keep value falls back to the default; it never means "delete all".

const DAY_MS = 86_400_000;
const HOUR_MS = 3_600_000;
const BATCH = 500;
const MAX_BATCHES = 200;

export const DEFAULT_RAW_KEEP_DAYS = 7;
export const MAX_RAW_KEEP_DAYS = 90;

export interface RetentionEnv {
  DB: D1Database;
  RAW_KEEP_DAYS?: string;
}

export interface PurgeOptions {
  /** Rows per delete statement (default 500). */
  batch?: number;
  /** Delete statements per step per run (default 200). */
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

/**
 * Run a bounded delete (`?1` and `?2` bound, the second being the row limit)
 * until it removes less than a full batch. Returns true when the batch budget
 * ran out with work possibly left.
 */
async function deleteBatched(
  db: D1Database,
  sql: string,
  args: Array<string | number>,
  batch: number,
  maxBatches: number,
): Promise<boolean> {
  for (let i = 0; i < maxBatches; i++) {
    const res = await db.prepare(sql).bind(...args, batch).run();
    if (res.meta.changes < batch) return false;
  }
  return true;
}

/** Purge every view table. Each step is independent; the first error is rethrown at the end. */
export async function purgeViews(
  env: RetentionEnv,
  nowMs: number,
  opts: PurgeOptions = {},
): Promise<void> {
  if (!Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const batch = opts.batch ?? BATCH;
  const maxBatches = opts.maxBatches ?? MAX_BATCHES;
  const today = new Date(nowMs).toISOString().slice(0, 10);
  const cutoff = Math.floor(nowMs - keepDays(env.RAW_KEEP_DAYS) * DAY_MS);
  const hour = Math.floor(nowMs / HOUR_MS);
  const run = (sql: string, args: Array<string | number>) =>
    deleteBatched(env.DB, sql, args, batch, maxBatches);
  const steps: Array<() => Promise<boolean>> = [
    () =>
      run(
        "DELETE FROM visitor_seen WHERE (day, h) IN (SELECT day, h FROM visitor_seen WHERE day < ?1 LIMIT ?2)",
        [today],
      ), // CONTROL:seen-purge
    () =>
      run(
        "DELETE FROM view_events WHERE id IN (SELECT id FROM view_events WHERE ts < ?1 LIMIT ?2)",
        [cutoff],
      ), // CONTROL:raw-purge
    () =>
      run(
        "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'bud:' AND key < ?1 LIMIT ?2)",
        [`bud:${padHour(hour - 48)}`],
      ),
    () =>
      run(
        "DELETE FROM view_meta WHERE key IN (SELECT key FROM view_meta WHERE key >= 'cap:' AND key < ?1 LIMIT ?2)",
        [`cap:${padHour(hour - 1)}`],
      ),
  ];
  let failure: unknown = null;
  let backlog = false;
  for (const step of steps) {
    try {
      if (await step()) backlog = true;
    } catch (e) {
      failure ??= e;
    }
  }
  try {
    if (backlog) {
      await env.DB.prepare(
        "INSERT INTO view_meta (key, value) VALUES ('purge_backlog', ?1) " +
          "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
      )
        .bind(today)
        .run();
    } else if (failure === null) {
      await env.DB.prepare("DELETE FROM view_meta WHERE key = 'purge_backlog'").run();
    }
  } catch (e) {
    failure ??= e;
  }
  if (failure !== null) throw failure;
}
