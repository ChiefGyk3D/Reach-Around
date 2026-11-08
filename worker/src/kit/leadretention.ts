// SPDX-License-Identifier: Apache-2.0
//
// Lead retention for the media kit, run by the hourly scheduled job (never by
// the live-poller cron, see src/index.ts). A lead is the address the visitor
// typed, a coarse country code and timestamps; deleting the row deletes its
// kit events too (ON DELETE CASCADE).
//
// RULE. One retention for EVERY lead, verified or not, counted from created_ts.
// A lead exactly LEAD_RETENTION_DAYS old is kept; one older by a single
// millisecond is deleted (strict `<`). Re-seeing an address does not extend it.
//
// CONFIG. LEAD_RETENTION_DAYS is plain digits, 1 to MAX_LEAD_RETENTION_DAYS
// (365), default DEFAULT_LEAD_RETENTION_DAYS (30). Anything else is invalid and
// the purge uses the default and logs one fixed "rejected" line: a typo never
// means "keep longer" and never means "delete everything".
//
// COST. Each run deletes at most LEAD_PURGE_MAX_PER_RUN leads, in batches of
// LEAD_PURGE_BATCH, one D1 query per batch, plus one heartbeat write
// (kit_meta last_purge_ts). Queries are taken from the invocation's meter
// (src/d1budget.ts) and the run stops before the meter is empty. The worst
// case of 24 runs a day times LEAD_DELETE_WRITES stays under half of
// PURGE_RESERVE (src/kit/budgets.ts), which is the daily write allowance set
// aside for purges; the other half is left for cascaded kit_events rows and
// the page-view purge. A backlog simply continues on the next run.
//
// LOGS. Fixed strings and one count, through log.ts. Never an address.

import { QueryMeter, meterOf, meteredDb } from "../d1budget";
import { logEvent } from "./log";

export const DEFAULT_LEAD_RETENTION_DAYS = 30;
export const MAX_LEAD_RETENTION_DAYS = 365;
export const LEAD_PURGE_BATCH = 25;
export const LEAD_PURGE_MAX_PER_RUN = 50;
/** Worst-case rows written per deleted lead: the row and its three indexes. Cascaded events are extra. */
export const LEAD_DELETE_WRITES = 4;
/** Queries a run takes when it has no outer meter: every batch plus the heartbeat. */
export const LEAD_PURGE_QUERIES = Math.ceil(LEAD_PURGE_MAX_PER_RUN / LEAD_PURGE_BATCH) + 1;

const DAY_MS = 86_400_000;
const DIGITS = /^[0-9]{1,9}$/;

export interface LeadRetentionEnv {
  DB: D1Database;
  LEAD_RETENTION_DAYS?: string;
}

export interface LeadPurgeOptions {
  /** Leads per delete; can only lower LEAD_PURGE_BATCH. */
  batch?: number;
  /** Leads per run; can only lower LEAD_PURGE_MAX_PER_RUN. */
  maxPerRun?: number;
}

/** Parse LEAD_RETENTION_DAYS. `valid` is false when a set value was refused. */
export function leadRetentionDays(raw: unknown): { days: number; valid: boolean } {
  if (raw === undefined || raw === "") return { days: DEFAULT_LEAD_RETENTION_DAYS, valid: true };
  if (typeof raw === "string" && DIGITS.test(raw)) {
    const n = Number(raw);
    if (n >= 1 && n <= MAX_LEAD_RETENTION_DAYS) return { days: n, valid: true };
  }
  return { days: DEFAULT_LEAD_RETENTION_DAYS, valid: false };
}

function lower(requested: unknown, ceiling: number): number {
  if (typeof requested !== "number" || !Number.isInteger(requested) || requested < 1) return ceiling;
  return Math.min(requested, ceiling);
}

/** Delete expired leads in bounded batches. Resolves to the number deleted this run. */
export async function purgeExpiredLeads(env: LeadRetentionEnv, nowMs: number, opts: LeadPurgeOptions = {}): Promise<number> {
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const outer = meterOf(env.DB);
  const meter = outer ?? new QueryMeter(LEAD_PURGE_QUERIES);
  const db = outer ? env.DB : meteredDb(env.DB, meter);
  const batch = lower(opts.batch, LEAD_PURGE_BATCH);
  const cap = lower(opts.maxPerRun, LEAD_PURGE_MAX_PER_RUN);

  const { days, valid } = leadRetentionDays(env.LEAD_RETENTION_DAYS);
  if (!valid) logEvent("purge", "rejected", "other");
  const cutoff = Math.floor(nowMs - days * DAY_MS);

  let deleted = 0;
  // Keep one query for the heartbeat; a batch is never started without a free query.
  while (deleted < cap && meter.remaining() >= 1) {
    const limit = Math.min(batch, cap - deleted);
    const r = await db
      .prepare("DELETE FROM leads WHERE id IN (SELECT id FROM leads WHERE created_ts < ?1 ORDER BY created_ts, id LIMIT ?2) RETURNING id")
      .bind(cutoff, limit)
      .all();
    const n = r.results.length;
    deleted += n;
    if (n < limit) break;
  }
  if (meter.remaining() >= 1) {
    await db
      .prepare("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', ?1) ON CONFLICT (key) DO UPDATE SET value = excluded.value")
      .bind(Math.floor(nowMs))
      .run();
  }
  if (deleted > 0) logEvent("purge", "ok", undefined, deleted);
  return deleted;
}
