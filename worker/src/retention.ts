// SPDX-License-Identifier: Apache-2.0
import type { Env } from "./click";

const DAY_MS = 24 * 60 * 60 * 1000;

/** How long click rows are kept unless the caller says otherwise. */
export const DEFAULT_KEEP_DAYS = 90;

/**
 * Delete click rows older than `keepDays` days and return how many went.
 *
 * The cutoff is compared against the indexed `ts` column with a bound
 * parameter. A zero, negative or non-finite `keepDays` is refused rather than
 * clamped, because a bad value there would wipe every row.
 */
export async function purgeOldEvents(
  env: Env,
  nowMs: number,
  keepDays: number = DEFAULT_KEEP_DAYS,
): Promise<number> {
  if (!Number.isFinite(keepDays) || keepDays <= 0) {
    throw new RangeError("keepDays must be a positive, finite number");
  }
  if (!Number.isFinite(nowMs)) {
    throw new RangeError("nowMs must be a finite number");
  }
  const cutoff = Math.floor(nowMs - keepDays * DAY_MS);
  const result = await env.DB.prepare("DELETE FROM click_events WHERE ts < ?")
    .bind(cutoff)
    .run();
  return result.meta.changes;
}
