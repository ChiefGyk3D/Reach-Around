// SPDX-License-Identifier: Apache-2.0
//
// Shared durable state for owner alerts (migration 0011): the per-minute
// quota, the daily budget and the dedup claim. Each is ONE conditional
// statement, so concurrent Worker isolates cannot overshoot, and a refused
// call writes nothing. Both tables are bounded by a hard row cap and pruned on
// every write. Every statement is a constant string with bound parameters.

export const MINUTE_MS = 60_000;
export const DAY_MS = 86_400_000;
/** Hard caps on rows; a new row is refused at the cap. */
export const QUOTA_ROW_CAP = 2000;
export const DEDUP_ROW_CAP = 500;
/** Quota rows are kept this long past their window. */
const MINUTE_KEEP_MS = 5 * MINUTE_MS;
const DAY_KEEP_MS = 2 * DAY_MS;

export type QuotaScope = "minute" | "day";

const SITE_RE = /^[a-z0-9._-]{1,64}$/;
const CHANNEL_RE = /^[a-z]{1,16}$/;
const HASH_RE = /^[0-9a-f]{32}$/;

function checkTime(nowMs: unknown): asserts nowMs is number {
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs) || nowMs < 0 || nowMs > 4_000_000_000_000) {
    throw new RangeError("time must be a finite number of milliseconds");
  }
}

/** The site key for the rows: the lowercase SITE_HOST when it is a plain name, else "default". */
export function siteKey(siteHost: unknown): string {
  return typeof siteHost === "string" && SITE_RE.test(siteHost.toLowerCase()) ? siteHost.toLowerCase() : "default";
}

export function windowStart(scope: QuotaScope, nowMs: number): number {
  checkTime(nowMs);
  const size = scope === "minute" ? MINUTE_MS : DAY_MS;
  return Math.floor(nowMs / size) * size;
}

/**
 * Take one unit of (site, channel, scope, window) if the count is below
 * `limit`. Resolves the NEW count when granted, or null when the limit is
 * reached (limit 0 always refuses and stores no row) or the table is at its
 * row cap and this is a new row. A refusal writes nothing. Throws RangeError on
 * a bad argument and lets a database error propagate (callers treat a throw as
 * a refusal).
 */
export async function reserveQuota(
  db: D1Database,
  site: string,
  channel: string,
  scope: QuotaScope,
  windowStartMs: number,
  limit: number,
): Promise<number | null> {
  if (typeof site !== "string" || !SITE_RE.test(site)) throw new RangeError("bad site");
  if (typeof channel !== "string" || !CHANNEL_RE.test(channel)) throw new RangeError("bad channel");
  if (scope !== "minute" && scope !== "day") throw new RangeError("bad scope");
  checkTime(windowStartMs);
  const lim: unknown = limit;
  if (typeof lim !== "number" || !Number.isInteger(lim) || lim < 0 || lim > 100_000) throw new RangeError("bad limit");
  const row = await db
    .prepare(
      `INSERT INTO alert_quota (site, channel, scope, window_start, count)
       SELECT ?1, ?2, ?3, ?4, 1
       WHERE ?5 > 0
         AND ((SELECT COUNT(*) FROM alert_quota) < ?6
              OR EXISTS (SELECT 1 FROM alert_quota WHERE site = ?1 AND channel = ?2 AND scope = ?3 AND window_start = ?4))
       ON CONFLICT (site, channel, scope, window_start) DO UPDATE SET count = count + 1 WHERE count < ?5
       RETURNING count`, // CONTROL:alert-quota
    )
    .bind(site, channel, scope, windowStartMs, lim, QUOTA_ROW_CAP)
    .first<{ count: number }>();
  return row === null ? null : row.count;
}

/**
 * Claim an event hash for `ttlMs`. Resolves true when this call claimed it
 * (new, or an expired claim), false when a live claim exists or the table is at
 * its row cap. Expired dedup rows and old quota rows are deleted in the same
 * batch, so the tables stay small without a scheduled job.
 */
export async function claimDedup(
  db: D1Database,
  site: string,
  eventHash: string,
  nowMs: number,
  ttlMs: number,
): Promise<boolean> {
  if (typeof site !== "string" || !SITE_RE.test(site)) throw new RangeError("bad site");
  if (typeof eventHash !== "string" || !HASH_RE.test(eventHash)) throw new RangeError("bad hash");
  checkTime(nowMs);
  if (!Number.isInteger(ttlMs) || ttlMs < 1 || ttlMs > DAY_MS) throw new RangeError("bad ttl");
  const now = Math.floor(nowMs);
  const res = await db.batch([
    db.prepare("DELETE FROM alert_dedup WHERE expires_at <= ?1").bind(now),
    db
      .prepare(
        "DELETE FROM alert_quota WHERE (scope = 'minute' AND window_start < ?1) OR (scope = 'day' AND window_start < ?2)",
      )
      .bind(now - MINUTE_KEEP_MS, now - DAY_KEEP_MS),
    db
      .prepare(
        `INSERT INTO alert_dedup (site, event_hash, expires_at)
         SELECT ?1, ?2, ?3
         WHERE (SELECT COUNT(*) FROM alert_dedup) < ?4
            OR EXISTS (SELECT 1 FROM alert_dedup WHERE site = ?1 AND event_hash = ?2)
         ON CONFLICT (site, event_hash) DO UPDATE SET expires_at = excluded.expires_at WHERE alert_dedup.expires_at <= ?5
         RETURNING event_hash`, // CONTROL:alert-dedup
      )
      .bind(site, eventHash, now + ttlMs, DEDUP_ROW_CAP, now),
  ]);
  return (res[2]?.results?.length ?? 0) === 1;
}

/** A salted SHA-256 prefix (32 hex) of the event identity. The address itself is never stored. */
export async function eventHash(site: string, kind: string, who: string): Promise<string> {
  const data = new TextEncoder().encode(`reach-around-alert|${site}|${kind}|${who.toLowerCase().slice(0, 254)}`);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", data));
  return Array.from(digest.slice(0, 16), (b) => b.toString(16).padStart(2, "0")).join("");
}
