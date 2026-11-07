// SPDX-License-Identifier: Apache-2.0
//
// Limits for the media kit: UTC day and hour labels, one atomic counter
// reservation, a per-client minute check, and the counter purge.
//
// COST RULE. The free plan is a hard stop: when a limit is reached the answer
// is a refusal (an outage), never a bill and never a fail-open. A reservation
// that is refused writes NOTHING: the statement is a conditional upsert whose
// update branch carries `WHERE count < limit`, so a flood over the limit costs
// reads only. Only a granted reservation writes one row.
//
// Every statement is a constant string with bound parameters. The key and day
// are validated here as well as by the table's CHECK constraints, so a bad
// value is a RangeError in the caller, not a database error.

/** The largest limit any caller may pass. */
export const MAX_RESERVE_LIMIT = 100_000;
/** Counters older than this many whole UTC days are purged. */
export const COUNTER_KEEP_DAYS = 2;

const DAY_MS = 86_400_000;
const MAX_TIME_MS = Date.UTC(9999, 11, 31, 23, 59, 59, 999);
const KEY_RE = /^[a-z0-9_-]{1,64}$/;
const DAY_RE = /^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])(?:T(?:[01][0-9]|2[0-3]))?$/;

function checkTime(nowMs: unknown): asserts nowMs is number {
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs) || nowMs < 0 || nowMs > MAX_TIME_MS) {
    throw new RangeError("time must be a finite number of milliseconds since 1970 and before year 10000");
  }
}

/** `YYYY-MM-DD` in UTC. */
export function utcDay(nowMs: number): string {
  checkTime(nowMs);
  return new Date(nowMs).toISOString().slice(0, 10);
}

/** `YYYY-MM-DDTHH` in UTC. */
export function utcHour(nowMs: number): string {
  checkTime(nowMs);
  return new Date(nowMs).toISOString().slice(0, 13);
}

/**
 * Take one unit from the counter `(key, day)` if it is below `limit`. Resolves
 * true when granted, false when the limit is reached (limit 0 always refuses
 * and stores no row). One statement, so concurrent callers cannot overshoot;
 * a refusal writes nothing. Throws RangeError on a bad key, day or limit and
 * lets a database error propagate (callers treat a throw as a refusal).
 */
export async function reserveBudget(db: D1Database, key: string, day: string, limit: number): Promise<boolean> {
  if (typeof key !== "string" || !KEY_RE.test(key)) throw new RangeError("budget key must be 1-64 of a-z 0-9 _ -");
  if (typeof day !== "string" || !DAY_RE.test(day)) throw new RangeError("budget day must be YYYY-MM-DD or YYYY-MM-DDTHH");
  const lim: unknown = limit;
  if (typeof lim !== "number" || !Number.isInteger(lim) || lim < 0 || lim > MAX_RESERVE_LIMIT) {
    throw new RangeError(`limit must be an integer from 0 to ${MAX_RESERVE_LIMIT}`);
  }
  const r = await db
    .prepare(
      `INSERT INTO counters (key, day, count) SELECT ?1, ?2, 1 WHERE ?3 > 0
       ON CONFLICT (key, day) DO UPDATE SET count = count + 1 WHERE count < ?3`, // CONTROL:budget
    )
    .bind(key, day, lim)
    .run();
  return r.meta.changes === 1;
}

/** Minimal shape of a Workers Rate Limiting binding. */
export interface MinuteLimiter {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

/**
 * Per-client burst check. `key` is an already-derived limiter key (never a raw
 * address; see ratekey.ts). A missing or empty key, a throw, or any verdict
 * other than `success: true` refuses.
 */
export async function checkIpMinute(limiter: MinuteLimiter, key: string | null): Promise<boolean> {
  if (typeof key !== "string" || key === "") return false; // CONTROL:ip-minute
  try {
    const v = await limiter.limit({ key });
    return v?.success === true;
  } catch {
    return false;
  }
}

/**
 * Delete counters older than COUNTER_KEEP_DAYS UTC days (hour rows included).
 * Not wired to the scheduler here: the retention task owns that.
 */
export async function purgeOldCounters(db: D1Database, nowMs: number): Promise<number> {
  checkTime(nowMs);
  const cutoff = utcDay(Math.max(0, nowMs - COUNTER_KEEP_DAYS * DAY_MS));
  const r = await db.prepare("DELETE FROM counters WHERE substr(day, 1, 10) < ? RETURNING key").bind(cutoff).all();
  return r.results.length;
}
