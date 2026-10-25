// SPDX-License-Identifier: Apache-2.0
//
// Retention for the page-view tables, run by the daily cron.
//
//   visitor_seen   every row before today's UTC date, unconditionally. It is
//                  pseudonymous (a daily hash) and the rollup never reads it
//                  (uniques come from the raw `first` flags), so it never
//                  waits on the rollup.
//   view_events    rows older than the raw keep window (default 7 days, Plan 4
//                  open decision P-3, RAW_KEEP_DAYS overrides, ceiling 90),
//                  but ONLY rows whose UTC day has a view_rollup_log row. A
//                  day not yet rolled up is never deleted by age until the
//                  hard ceiling of min(3x the keep window, 90 days); then it is dropped and
//                  view_meta('rollup_overdue') records the date.
//   view_daily, view_dim_daily, view_geo_daily, view_rollup_log
//                  rows older than 90 days.
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
/** Unrolled raw rows are dropped at this multiple of the keep window. */
export const HARD_CEILING_FACTOR = 3;
export const AGGREGATE_KEEP_DAYS = 90;

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

/** True when raw rows past the hard ceiling exist whose day was never rolled up. */
async function countUnrolledAged(db: D1Database, hardCutoff: number): Promise<boolean> {
  const r = await db
    .prepare(
      "SELECT 1 AS x FROM view_events WHERE ts < ?1 " +
        "AND strftime('%Y-%m-%d', ts / 1000, 'unixepoch') NOT IN (SELECT day FROM view_rollup_log) LIMIT 1",
    )
    .bind(hardCutoff)
    .first();
  return r !== null;
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
  const keep = keepDays(env.RAW_KEEP_DAYS);
  const cutoff = Math.floor(nowMs - keep * DAY_MS);
  const hardCutoff = Math.floor(nowMs - Math.min(keep * HARD_CEILING_FACTOR, MAX_RAW_KEEP_DAYS) * DAY_MS);
  const aggCutoff = new Date(nowMs - AGGREGATE_KEEP_DAYS * DAY_MS).toISOString().slice(0, 10);
  const hour = Math.floor(nowMs / HOUR_MS);
  const run = (sql: string, args: Array<string | number>) =>
    deleteBatched(env.DB, sql, args, batch, maxBatches);
  const steps: Array<() => Promise<boolean>> = [
    () =>
      run(
        "DELETE FROM visitor_seen WHERE (day, h) IN (SELECT day, h FROM visitor_seen WHERE day < ?1 LIMIT ?2)",
        [today],
      ), // CONTROL:seen-purge
    async () => {
      // CONTROL:raw-purge  unrolled days survive until the hard ceiling.
      const hardHit = await countUnrolledAged(env.DB, hardCutoff);
      const more = await run(
        "DELETE FROM view_events WHERE id IN (SELECT id FROM view_events WHERE ts < ?1 " +
          "AND (strftime('%Y-%m-%d', ts / 1000, 'unixepoch') IN (SELECT day FROM view_rollup_log) OR ts < ?2) LIMIT ?3)",
        [cutoff, hardCutoff],
      );
      if (hardHit) {
        await env.DB.prepare(
          "INSERT INTO view_meta (key, value) VALUES ('rollup_overdue', ?1) " +
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        )
          .bind(today)
          .run();
      }
      return more;
    },
    () => run("DELETE FROM view_daily WHERE day IN (SELECT day FROM view_daily WHERE day < ?1 LIMIT ?2)", [aggCutoff]),
    () =>
      run("DELETE FROM view_dim_daily WHERE day IN (SELECT day FROM view_dim_daily WHERE day < ?1 LIMIT ?2)", [aggCutoff]),
    () =>
      run("DELETE FROM view_geo_daily WHERE day IN (SELECT day FROM view_geo_daily WHERE day < ?1 LIMIT ?2)", [aggCutoff]),
    () =>
      run("DELETE FROM view_rollup_log WHERE day IN (SELECT day FROM view_rollup_log WHERE day < ?1 LIMIT ?2)", [aggCutoff]),
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
