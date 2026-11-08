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
// COST. A run is ONE D1 query (a db.batch, which counts as a single query on
// the invocation's meter, src/d1budget.ts) holding three statements: delete up
// to LEAD_PURGE_EVENT_BATCH kit_events that belong to expired leads, delete up
// to LEAD_PURGE_BATCH expired leads that have NO events left, and the
// heartbeat (kit_meta last_purge_ts). A lead is deleted only after its events
// are gone, so the ON DELETE CASCADE never deletes events in bulk: a lead that
// owns thousands of events is purged over several runs, and the write cost of a
// run is bounded by the two batch sizes, not by the data. Rows written (an
// estimate, not measured on D1: the row plus its indexes): 4 per lead (row, the
// unique email index, created_ts, verified_ts), 3 per event (row, ts, lead_id),
// 1 for the heartbeat. 24 runs a day stay under 80 percent of PURGE_RESERVE
// (src/kit/budgets.ts, the kit's daily purge allowance); the rest is left for
// the counter purge. A backlog simply continues on the next run.
//
// LOGS. Fixed strings and one count, through log.ts. Never an address.

import { QueryMeter, meterOf, meteredDb } from "../d1budget";
import { logEvent } from "./log";

export const DEFAULT_LEAD_RETENTION_DAYS = 30;
export const MAX_LEAD_RETENTION_DAYS = 365;
/** Expired leads deleted per run. */
export const LEAD_PURGE_BATCH = 20;
/** Events of expired leads deleted per run. */
export const LEAD_PURGE_EVENT_BATCH = 80;
/** Estimated rows written per deleted lead: the row and its three indexes. */
export const LEAD_DELETE_WRITES = 4;
/** Estimated rows written per deleted event: the row and its two indexes. */
export const EVENT_DELETE_WRITES = 3;
/** Estimated rows written by the heartbeat. */
export const HEARTBEAT_WRITES = 1;
/** Worst-case estimated writes of one run. */
export const LEAD_PURGE_RUN_WRITES =
  LEAD_PURGE_BATCH * LEAD_DELETE_WRITES + LEAD_PURGE_EVENT_BATCH * EVENT_DELETE_WRITES + HEARTBEAT_WRITES;
/** Worst-case estimated writes of 24 hourly runs. */
export const LEAD_PURGE_DAILY_WRITES = 24 * LEAD_PURGE_RUN_WRITES;
/** D1 queries one run takes: the single batch. */
export const LEAD_PURGE_QUERIES = 1;

const DAY_MS = 86_400_000;
const DIGITS = /^[0-9]{1,9}$/;

export interface LeadRetentionEnv {
  DB: D1Database;
  LEAD_RETENTION_DAYS?: string;
}

export interface LeadPurgeOptions {
  /** Leads per run; can only lower LEAD_PURGE_BATCH. */
  leadBatch?: number;
  /** Events per run; can only lower LEAD_PURGE_EVENT_BATCH. */
  eventBatch?: number;
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

/** Purge one bounded step. Resolves to the number of leads deleted this run. */
export async function purgeExpiredLeads(env: LeadRetentionEnv, nowMs: number, opts: LeadPurgeOptions = {}): Promise<number> {
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs)) throw new RangeError("nowMs must be a finite number");
  const outer = meterOf(env.DB);
  const meter = outer ?? new QueryMeter(LEAD_PURGE_QUERIES);
  const db = outer ? env.DB : meteredDb(env.DB, meter);
  const leadBatch = lower(opts.leadBatch, LEAD_PURGE_BATCH);
  const eventBatch = lower(opts.eventBatch, LEAD_PURGE_EVENT_BATCH);

  const { days, valid } = leadRetentionDays(env.LEAD_RETENTION_DAYS);
  if (!valid) logEvent("purge", "rejected", "other");
  if (meter.remaining() < LEAD_PURGE_QUERIES) {
    logEvent("purge", "over_budget");
    return 0;
  }
  const cutoff = Math.floor(nowMs - days * DAY_MS);

  const res = await db.batch([
    // Events first, bounded: the lead delete below then cascades nothing.
    db
      .prepare(
        "DELETE FROM kit_events WHERE id IN (SELECT id FROM kit_events WHERE lead_id IN (SELECT id FROM leads WHERE created_ts < ?1) LIMIT ?2)",
      )
      .bind(cutoff, eventBatch),
    db
      .prepare(
        "DELETE FROM leads WHERE id IN (SELECT l.id FROM leads l WHERE l.created_ts < ?1 " +
          "AND NOT EXISTS (SELECT 1 FROM kit_events e WHERE e.lead_id = l.id) ORDER BY l.created_ts, l.id LIMIT ?2) RETURNING id",
      )
      .bind(cutoff, leadBatch),
    db
      .prepare("INSERT INTO kit_meta (key, value) VALUES ('last_purge_ts', ?1) ON CONFLICT (key) DO UPDATE SET value = excluded.value")
      .bind(Math.floor(nowMs)),
  ]);
  const deleted = res[1]?.results.length ?? 0;
  if (deleted > 0) logEvent("purge", "ok", undefined, deleted);
  return deleted;
}
