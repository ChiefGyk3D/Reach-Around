// SPDX-License-Identifier: Apache-2.0
//
// Route budgets, the daily send and alert budgets, and storage caps for the
// media kit, sized to the Cloudflare FREE plan.
//
// RULING (cutover slice, hosting plan): an outage when a limit is hit is
// accepted over any bill. Everything here fails CLOSED. A reservation is false
// when the limit is reached, when the configuration is invalid, when the time
// or route is bad, and when the database errors. No path grants on error.
//
// ENV. Strict, like the kit kill switch: a value must be an unsigned decimal
// integer of at most nine digits, exactly, no sign, space, exponent or other
// form, inside the variable's range. Unset or "" is the default. Anything else
// is invalid; the load throws KitLimitsError listing variable NAMES only, and
// every reservation then refuses. Nothing is clamped or guessed.
//
//   ROUTE_BUDGET_GATE    0..100000 per UTC hour, default 150
//   ROUTE_BUDGET_VIEW    0..100000 per UTC hour, default 300
//   MAX_LEADS            1..50000, default 5000 (the lead cap upsertLead enforces)
//   MAX_KIT_EVENTS       1..500000, default 50000 (cap on stored kit_events)
//   DAILY_ALERT_BUDGET   0..1000 per UTC day, default 20 (owner alerts)
//   DAILY_SEND_BUDGET    0..1000 per UTC day, default 0 (no send route in v1)
//   D1_DAILY_WRITE_QUOTA 1000..1000000000, default 100000 (the free D1 plan's
//                        daily rows-written figure; re-check against Cloudflare)
//
// The load REFUSES a configuration whose worst-case daily D1 writes exceed the
// quota, and warns above 70 percent.
//
// COST. Counters are one row per (key, hour or day), written by one conditional
// upsert; a refused reservation writes nothing. Event storage is capped with a
// rowid span (MAX(id) - MIN(id) + 1), which is O(1) and never smaller than the
// row count, so it can only refuse early, never overshoot.

import { MAX_LEADS_CEILING } from "./leads";
import { reserveBudget, utcDay, utcHour, MAX_RESERVE_LIMIT } from "./limits";
import type { KitEventKind } from "./leads";

export type RouteName = "gate" | "view";

export const DEFAULTS = {
  routeGatePerHour: 150,
  routeViewPerHour: 300,
  maxLeads: 5000,
  maxKitEvents: 50_000,
  dailyAlertBudget: 20,
  dailySendBudget: 0,
  d1DailyWriteQuota: 100_000,
} as const;

export const MAX_KIT_EVENTS_CEILING = 500_000;
const MAX_DAILY_BUDGET = 1000;
const MIN_QUOTA = 1000;
const MAX_QUOTA = 1_000_000_000;

/** Worst-case rows written by one accepted operation (the row, its indexes, the counter). */
export const WRITES_PER_OP = { gate: 5, view: 2, send: 7, alert: 1 } as const;
/** Daily reserve for the purge and the hourly rollup, counted against the quota. */
export const PURGE_RESERVE = 10_000;
/** Pessimistic stored size per row, indexes included. */
export const BYTES_PER_LEAD = 600;
export const BYTES_PER_EVENT = 100;

export interface KitLimitsEnv {
  ROUTE_BUDGET_GATE?: string;
  ROUTE_BUDGET_VIEW?: string;
  MAX_LEADS?: string;
  MAX_KIT_EVENTS?: string;
  DAILY_ALERT_BUDGET?: string;
  DAILY_SEND_BUDGET?: string;
  D1_DAILY_WRITE_QUOTA?: string;
}

/** The limits are unusable. Carries variable names only, never values. */
export class KitLimitsError extends Error {
  readonly fields: readonly string[];
  constructor(fields: readonly string[]) {
    super(`kit limits invalid: ${fields.join(", ")}`);
    this.name = "KitLimitsError";
    this.fields = fields;
  }
}

export interface KitLimits {
  routeGatePerHour: number;
  routeViewPerHour: number;
  maxLeads: number;
  maxKitEvents: number;
  dailyAlertBudget: number;
  dailySendBudget: number;
  d1DailyWriteQuota: number;
  /** Non-fatal notes, e.g. worst-case writes above 70 percent of the quota. */
  warnings: readonly string[];
}

const DIGITS = /^[0-9]{1,9}$/;

/** Returns the number, `fallback` for unset or "", or NaN for anything invalid. */
function parseStrict(v: unknown, lo: number, hi: number, fallback: number): number {
  if (v === undefined || v === "") return fallback;
  if (typeof v !== "string" || !DIGITS.test(v)) return NaN;
  const n = Number(v);
  return n >= lo && n <= hi ? n : NaN;
}

export function worstCaseDailyWrites(
  c: Pick<KitLimits, "routeGatePerHour" | "routeViewPerHour" | "dailyAlertBudget" | "dailySendBudget">,
): number {
  return (
    24 * (c.routeGatePerHour * WRITES_PER_OP.gate + c.routeViewPerHour * WRITES_PER_OP.view) +
    c.dailyAlertBudget * WRITES_PER_OP.alert +
    c.dailySendBudget * WRITES_PER_OP.send +
    PURGE_RESERVE
  );
}

export function worstCaseStorageBytes(c: Pick<KitLimits, "maxLeads" | "maxKitEvents">): number {
  return c.maxLeads * BYTES_PER_LEAD + c.maxKitEvents * BYTES_PER_EVENT;
}

/** Parse and validate every limit. Throws KitLimitsError (names only). Cheap: call per request. */
export function loadKitLimits(env: KitLimitsEnv): KitLimits {
  const e = (env ?? {}) as unknown as Record<string, unknown>;
  const bad: string[] = [];
  const get = (name: string, lo: number, hi: number, fallback: number): number => {
    const n = parseStrict(e[name], lo, hi, fallback);
    if (Number.isNaN(n)) {
      bad.push(name);
      return fallback;
    }
    return n;
  };
  const c = {
    routeGatePerHour: get("ROUTE_BUDGET_GATE", 0, MAX_RESERVE_LIMIT, DEFAULTS.routeGatePerHour),
    routeViewPerHour: get("ROUTE_BUDGET_VIEW", 0, MAX_RESERVE_LIMIT, DEFAULTS.routeViewPerHour),
    maxLeads: get("MAX_LEADS", 1, MAX_LEADS_CEILING, DEFAULTS.maxLeads),
    maxKitEvents: get("MAX_KIT_EVENTS", 1, MAX_KIT_EVENTS_CEILING, DEFAULTS.maxKitEvents),
    dailyAlertBudget: get("DAILY_ALERT_BUDGET", 0, MAX_DAILY_BUDGET, DEFAULTS.dailyAlertBudget),
    dailySendBudget: get("DAILY_SEND_BUDGET", 0, MAX_DAILY_BUDGET, DEFAULTS.dailySendBudget),
    d1DailyWriteQuota: get("D1_DAILY_WRITE_QUOTA", MIN_QUOTA, MAX_QUOTA, DEFAULTS.d1DailyWriteQuota),
  };
  if (bad.length > 0) throw new KitLimitsError(bad);

  const worst = worstCaseDailyWrites(c);
  if (worst > c.d1DailyWriteQuota) throw new KitLimitsError(["D1_DAILY_WRITE_QUOTA"]);
  const warnings = worst * 10 > c.d1DailyWriteQuota * 7 ? ["worst-case daily D1 writes are above 70 percent of D1_DAILY_WRITE_QUOTA"] : [];
  return { ...c, warnings };
}

type DbEnv = KitLimitsEnv & { DB: D1Database };

/**
 * Reserve one request on a route for the current UTC hour. False means answer
 * with the generic failure and do nothing else. Fails closed on every error.
 */
export async function reserveRoute(env: DbEnv, route: RouteName, nowMs: number): Promise<boolean> {
  try {
    const c = loadKitLimits(env);
    let limit: number;
    if (route === "gate") limit = c.routeGatePerHour;
    else if (route === "view") limit = c.routeViewPerHour;
    else return false;
    return await reserveBudget(env.DB, `route-${route}`, utcHour(nowMs), limit); // CONTROL:route-budget
  } catch {
    return false;
  }
}

async function reserveDaily(env: DbEnv, key: string, pick: (c: KitLimits) => number, nowMs: number): Promise<boolean> {
  try {
    return await reserveBudget(env.DB, key, utcDay(nowMs), pick(loadKitLimits(env)));
  } catch {
    return false;
  }
}

/** One outgoing email against the daily send budget (default 0: none allowed). */
export function reserveSend(env: DbEnv, nowMs: number): Promise<boolean> {
  return reserveDaily(env, "mail", (c) => c.dailySendBudget, nowMs); // CONTROL:send-budget
}

/** One owner alert against the daily alert budget. */
export function reserveAlert(env: DbEnv, nowMs: number): Promise<boolean> {
  return reserveDaily(env, "alert", (c) => c.dailyAlertBudget, nowMs); // CONTROL:alert-budget
}

const KINDS: readonly string[] = ["unlock", "view", "verify"];
const HOUR_MS = 3_600_000;

/**
 * Record a kit event only while the table is below `maxEvents` (the rowid span
 * test in the header). The check and the insert are one statement, so
 * concurrent callers cannot overshoot. Returns false when full. The time is
 * rounded down to the hour, as recordKitEvent does.
 */
export async function recordKitEventCapped(
  db: D1Database,
  leadId: string,
  kind: KitEventKind,
  nowMs: number,
  maxEvents: number,
): Promise<boolean> {
  if (!KINDS.includes(kind)) throw new RangeError("unknown kit event kind");
  if (typeof nowMs !== "number" || !Number.isFinite(nowMs) || nowMs < 0) throw new RangeError("nowMs must be a finite number");
  const cap: unknown = maxEvents;
  if (typeof cap !== "number" || !Number.isInteger(cap) || cap < 1 || cap > MAX_KIT_EVENTS_CEILING) {
    throw new RangeError(`maxEvents must be an integer from 1 to ${MAX_KIT_EVENTS_CEILING}`);
  }
  const ts = Math.floor(nowMs / HOUR_MS) * HOUR_MS;
  const r = await db
    .prepare(
      `INSERT INTO kit_events (ts, lead_id, kind)
       SELECT ?1, ?2, ?3
       WHERE (SELECT COALESCE(MAX(id) - MIN(id) + 1, 0) FROM kit_events) < ?4`,
    )
    .bind(ts, leadId, kind, cap)
    .run();
  return r.meta.changes === 1;
}
