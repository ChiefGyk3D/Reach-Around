// SPDX-License-Identifier: Apache-2.0
import type { Env } from "./click";
import { meterOf } from "./d1budget";

const DAY_MS = 24 * 60 * 60 * 1000;

/** How long click rows are kept unless the caller says otherwise. */
export const DEFAULT_KEEP_DAYS = 90;

/** Rows per delete statement and rounds per run: one run never holds the database for long. */
export const CLICK_PURGE_BATCH = 500;
export const CLICK_PURGE_ROUNDS = 10;
/** Queries left on a shared meter for the jobs that run after this one (the rollup needs at least 8). */
const KEEP_FREE = 12;

export interface ClickPurgeOptions {
  /** Rows per delete statement (default 500). */
  batch?: number;
  /** Delete statements per run (default 10). */
  maxBatches?: number;
}

/**
 * A purge option may lower the work of one run, never raise it above the default: that is what
 * keeps a run's cost at most CLICK_PURGE_ROUNDS deletes plus one marker batch. A non-finite value
 * is refused (it would otherwise reach SQL as LIMIT NaN or an unbounded loop); a value below 1
 * becomes 1; a value above the default is clamped to it.
 */
function boundedOption(name: string, value: number | undefined, max: number): number {
  if (value === undefined) return max;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new RangeError(`${name} must be a finite number`);
  }
  return Math.min(max, Math.max(1, Math.trunc(value)));
}

/**
 * Delete click rows older than `keepDays` days and return how many went.
 *
 * The cutoff is compared against the indexed `ts` column with a bound
 * parameter. A zero, negative or non-finite `keepDays` is refused rather than
 * clamped, because a bad value there would wipe every row.
 *
 * Deletes run in fixed batches of at most `batch` rows, one query each, and
 * stop after `maxBatches` of them or when the invocation's query meter is low.
 * A run that leaves an expired row behind records `click_purge_backlog`
 * (the UTC date) in view_meta, and the next run simply continues: every delete
 * is a predicate on age, so it resumes where this one stopped. A run that
 * finishes clears the marker. The marker is written best effort but reconciled on every run
 * (one extra query), so it is never left missing or stale for longer than one run.
 *
 * Query cost (each .run() and each db.batch() is one metered query): an idle run is 2 (one
 * delete that finds nothing, one marker batch); a backlog run is at most CLICK_PURGE_ROUNDS
 * deletes plus 1 marker batch. `batch` and `maxBatches` can only lower these bounds.
 */
export async function purgeOldEvents(
  env: Env,
  nowMs: number,
  keepDays: number = DEFAULT_KEEP_DAYS,
  opts: ClickPurgeOptions = {},
): Promise<number> {
  if (!Number.isFinite(keepDays) || keepDays <= 0) {
    throw new RangeError("keepDays must be a positive, finite number");
  }
  if (!Number.isFinite(nowMs)) {
    throw new RangeError("nowMs must be a finite number");
  }
  const cutoff = Math.floor(nowMs - keepDays * DAY_MS);
  const batch = boundedOption("batch", opts.batch, CLICK_PURGE_BATCH);
  const rounds = boundedOption("maxBatches", opts.maxBatches, CLICK_PURGE_ROUNDS);
  const meter = meterOf(env.DB);
  let deleted = 0;
  for (let i = 0; i < rounds; i++) {
    if (meter !== undefined && meter.remaining() < KEEP_FREE) break;
    const result = await env.DB.prepare(
      "DELETE FROM click_events WHERE id IN (SELECT id FROM click_events WHERE ts < ? LIMIT ?)",
    )
      .bind(cutoff, batch)
      .run();
    const n = result.meta.changes;
    deleted += n;
    if (n < batch) break;
  }
  // The marker is reconciled on EVERY run that has a query left, whether or not it deleted
  // anything: a run cut short by the shared meter, a run that finds nothing, and a run after a
  // failed marker write all converge on the truth, so a missing or stale marker heals on the next
  // run. It is decided in one transaction by whether an expired row is really left. A failure here
  // is swallowed (the click purge never depends on the view tables) and costs only a late marker.
  if (meter === undefined || meter.remaining() >= 1) {
    try {
      await env.DB.batch([
        env.DB.prepare(
          "INSERT INTO view_meta (key, value) SELECT 'click_purge_backlog', ?2 WHERE EXISTS (SELECT 1 FROM click_events WHERE ts < ?1) " +
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ).bind(cutoff, new Date(nowMs).toISOString().slice(0, 10)),
        env.DB.prepare(
          "DELETE FROM view_meta WHERE key = 'click_purge_backlog' AND NOT EXISTS (SELECT 1 FROM click_events WHERE ts < ?1)",
        ).bind(cutoff),
      ]);
    } catch {
      // The next run reconciles again, deleted or not.
    }
  }
  return deleted;
}
